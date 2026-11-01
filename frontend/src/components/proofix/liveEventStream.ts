/**
 * Live execution event source backed by the ProoFix backend.
 *
 * Mirrors the contract of `createMockEventSource`: given the agent list, emit
 * `ExecutionEvent`s and return a dispose function. `useExecutionRun` stays a
 * pure reducer — it never learns where events come from.
 *
 * Transport: replays `GET /api/runs/:id/events` for history, then attaches to
 * `WS /ws/runs/:id` for live frames. Backend `AgentStatusEvent`s are translated
 * into the UI's card-indexed events here.
 *
 * Backend frames arrive in bursts — history replays dozens at once, and a
 * single `completed` frame stands for a whole card's narrative. Emitting those
 * straight through collapses into one React render and the journal's reveal
 * animations never mount. So translated events go through a paced drain queue:
 * one event per tick, accelerating while a backlog exists so catching up on a
 * finished run stays quick, settling to a readable cadence once caught up.
 */
import { API_BASE_URL, ENDPOINTS, apiFetch } from "@/lib/api";
import type { AgentEntry, AgentStatus } from "./data";
import type { EventEmitter, EventSourceFactory, ExecutionEvent } from "./mockEventStream";
import {
  isTerminalFrameType,
  resolveRunLifecycle,
  stateForFrameType,
  statusToLifecycleState,
  type RunLifecycleFrame,
  type RunLifecycleState,
} from "./runLifecycle";

/**
 * Backend agent_id -> frontend card id. Mirrors backend AGENT_REGISTRY.
 *
 * A0.7 was added after the original eleven. Without it the environment
 * precheck — the only stage a blocked run ever reaches — emitted events the
 * journal silently discarded, so the boundary where the run actually stopped
 * was invisible.
 */
const CARD_BY_AGENT_ID: Record<string, string> = {
  "A0.7": "environment",
  A1: "repo-intel",
  A2: "deps",
  A3: "static",
  "A3.5": "reproduce",
  A4: "root",
  A5: "blast",
  A6: "planner",
  A7: "patch",
  A8: "mutation",
  A9: "security",
  A10: "merge",
};

/** A1/A2/A3 run concurrently and report under a combined id. */
const FAN_OUT_AGENT_IDS = new Set(["A1+A2+A3", "fan-in"]);

export interface BackendAgentEvent {
  run_id: string;
  agent_id: string;
  status: "started" | "progress" | "completed" | "failed" | "retry";
  timestamp: string;
  message: string;
  payload?: Record<string, unknown> | null;
  sequence: number;
}

function statusToAgentStatus(status: BackendAgentEvent["status"]): AgentStatus {
  switch (status) {
    case "completed":
      return "completed";
    case "failed":
      return "failed";
    case "retry":
      return "retry";
    default:
      return "running";
  }
}

/** Cadence a single reveal plays at once the queue has caught up. */
const LINE_INTERVAL_MS = 460;
/** Floor for the accelerated catch-up cadence. */
const MIN_INTERVAL_MS = 60;
/** Backlog beyond which the queue starts draining faster than real time. */
const CATCH_UP_THRESHOLD = 6;

/**
 * Reconnect backoff (B-F03).
 *
 * A dropped socket used to be permanent: the stream silently degraded to the
 * 2.5s REST poll, so a live run kept updating but stopped narrating, and the
 * user had no way to tell a quiet pipeline from a dead transport. These bound
 * an exponential backoff that gives up after `WS_MAX_RETRIES` attempts —
 * polling remains the floor, so failing to reconnect degrades rather than
 * breaks.
 */
const WS_RETRY_BASE_MS = 500;
const WS_RETRY_MAX_MS = 15_000;
const WS_MAX_RETRIES = 8;

function websocketUrl(runId: string): string {
  const base = API_BASE_URL || (typeof window !== "undefined" ? window.location.origin : "");
  const url = new URL(`/ws/runs/${encodeURIComponent(runId)}`, base || "http://127.0.0.1:8000");
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

/**
 * Build an event source bound to a specific run.
 *
 * Terminal runs (already completed before the UI attached) replay their full
 * history and finish immediately, so opening an old run renders the same
 * journal as watching it live.
 */
export function createLiveEventSource(runId: string): EventSourceFactory {
  // Reveal progress lives on the factory, not the subscription. The workspace
  // memoises one factory per run id, so when polling swaps in a longer agent
  // list and `useExecutionRun` re-subscribes, the history replay recognises
  // what the user has already watched and only queues genuinely new lines.
  //
  // Two levels of bookkeeping, because queued is not the same as delivered: a
  // subscription can be disposed with events still in its queue, and those
  // never reach the reducer. `emitted*` records what the user actually saw and
  // is the state a fresh subscription resumes from; `queued*` additionally
  // covers what is still in flight, so replaying history does not double-queue.
  // On dispose the queued level rolls back to the emitted level, letting the
  // next subscription re-queue whatever the old one dropped.
  const emittedLines = new Map<string, number>();
  const emittedFinal = new Set<string>();
  let queuedLines = new Map<string, number>();
  let queuedFinal = new Set<string>();

  return (agents: AgentEntry[], emit: EventEmitter) => {
    // No run selected yet — nothing to stream.
    if (!runId || runId === "vulnapi-live") return () => undefined;

    const indexByCard = new Map(agents.map((agent, index) => [agent.id, index]));
    const cardByIndex = new Map(agents.map((agent, index) => [index, agent.id]));
    const linesByCard = new Map(agents.map((agent) => [agent.id, agent.lines.length]));

    let disposed = false;
    let socket: WebSocket | null = null;

    // ---- paced drain queue ------------------------------------------------
    const queue: ExecutionEvent[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;

    // Shrink the gap while a backlog exists so replaying a finished run's
    // history animates without making the user wait out every line in it.
    const nextDelay = () =>
      queue.length > CATCH_UP_THRESHOLD
        ? Math.max(
            MIN_INTERVAL_MS,
            Math.round((LINE_INTERVAL_MS * CATCH_UP_THRESHOLD) / queue.length),
          )
        : LINE_INTERVAL_MS;

    const pump = () => {
      if (disposed || timer) return;
      const event = queue.shift();
      if (!event) return;

      // Promote to the delivered level only now that the reducer has it.
      const card = event.type === "run.settled" ? undefined : cardByIndex.get(event.index);
      if (card) {
        if (event.type === "agent.line") {
          emittedLines.set(card, Math.max(emittedLines.get(card) ?? 0, event.lineIndex));
        } else if (event.type === "agent.finalized") {
          emittedFinal.add(card);
        }
      }

      emit(event);
      timer = setTimeout(() => {
        timer = null;
        pump();
      }, nextDelay());
    };

    const enqueue = (event: ExecutionEvent) => {
      if (disposed) return;
      queue.push(event);
      pump();
    };

    const applyEvent = (event: BackendAgentEvent) => {
      if (FAN_OUT_AGENT_IDS.has(event.agent_id)) return;

      const card = CARD_BY_AGENT_ID[event.agent_id];
      if (!card) return;
      const index = indexByCard.get(card);
      if (index === undefined) return;

      const total = linesByCard.get(card) ?? 0;
      const seen = queuedLines.get(card) ?? 0;
      const terminal = event.status === "completed" || event.status === "failed";

      // Nothing new to show for this card — don't re-announce it as active.
      if (seen >= total && (!terminal || queuedFinal.has(card))) return;

      enqueue({ type: "agent.started", index });

      // A terminal frame stands for the card's whole remaining narrative; any
      // other frame advances by one. Either way the lines are queued one at a
      // time so each types in instead of the card jumping to its end state.
      const target = terminal ? total : Math.min(seen + 1, total);
      for (let lineIndex = seen + 1; lineIndex <= target; lineIndex += 1) {
        enqueue({ type: "agent.line", index, lineIndex });
      }
      queuedLines.set(card, Math.max(seen, target));

      if (terminal) {
        queuedFinal.add(card);
        enqueue({
          type: "agent.finalized",
          index,
          status: statusToAgentStatus(event.status),
        });
      }
    };

    // Once the run has settled there is nothing left to stream, so a close is
    // expected and must not trigger a reconnect.
    let settledSeen = false;
    let retries = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleReconnect = () => {
      if (disposed || settledSeen || reconnectTimer) return;
      if (retries >= WS_MAX_RETRIES) return;
      const delay = Math.min(WS_RETRY_MAX_MS, WS_RETRY_BASE_MS * 2 ** retries);
      retries += 1;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (disposed || settledSeen) return;
        // Replay history before re-attaching: frames published while the socket
        // was down exist only in the event log. `applyEvent` is idempotent —
        // it skips any card whose lines are already queued — so replaying the
        // whole log cannot duplicate journal lines.
        void (async () => {
          try {
            const history = await apiFetch<BackendAgentEvent[]>(ENDPOINTS.runEvents(runId));
            if (disposed || settledSeen) return;
            history.forEach(applyEvent);
          } catch {
            /* best-effort; the socket may still catch up */
          }
          if (disposed || settledSeen) return;
          attachSocket();
        })();
      }, delay);
    };

    const attachSocket = () => {
      if (disposed || settledSeen || typeof WebSocket === "undefined") return;
      try {
        socket = new WebSocket(websocketUrl(runId));
      } catch {
        scheduleReconnect();
        return;
      }

      // A clean attach resets the backoff, so a run that drops once an hour
      // never accumulates its way to the retry ceiling.
      socket.onopen = () => {
        retries = 0;
      };

      socket.onmessage = (message) => {
        try {
          const parsed = JSON.parse(message.data as string);
          if (parsed?.type === "ping") return;
          // The backend says outright when the run has settled, and which of
          // the three endings it reached. Queued rather than applied
          // immediately so the end-of-run reveal waits for the journal to
          // finish playing instead of firing while lines are still arriving.
          if (isTerminalFrameType(parsed?.type)) {
            // The state-poll fallback frame is always named `run.completed`
            // for backwards compatibility and carries the real outcome on
            // `status`. Trusting the name there reported a blocked run as a
            // completed one, so `status` wins whenever it is present.
            const state =
              statusToLifecycleState(parsed?.status) ?? stateForFrameType(parsed.type as string);
            if (state) {
              settledSeen = true;
              enqueue({ type: "run.settled", state });
            }
            return;
          }
          // `run.started` and any future non-terminal lifecycle frame carry no
          // agent id; dropping them here keeps them out of `applyEvent`.
          if (typeof parsed?.type === "string") return;
          applyEvent(parsed as BackendAgentEvent);
        } catch {
          /* ignore malformed frames */
        }
      };

      // A close is *not* evidence the run finished. Dropped wifi, a backend
      // restart and a proxy timeout all close the socket mid-run, and treating
      // that as completion once showed the user a final report for a run that
      // was still going. So `onclose` reconnects and nothing more — it never
      // emits `run.settled`. Completion arrives only as the explicit frame
      // above, or from the lifecycle read below.
      socket.onclose = () => {
        socket = null;
        scheduleReconnect();
      };
    };

    // Replay history first so a mid-run attach still shows everything.
    void (async () => {
      try {
        const history = await apiFetch<BackendAgentEvent[]>(ENDPOINTS.runEvents(runId));
        if (disposed) return;
        history.forEach(applyEvent);
      } catch {
        /* fall through — history is best-effort */
      }

      // Whether the run is over is the backend's `status` and lifecycle record,
      // not an inference from A10. Inferring from A10 could only ever recognise
      // one of the three endings, so a run that failed at A4 or was blocked at
      // A0.7 replayed its whole history and then sat there claiming to be live.
      let settled: RunLifecycleState | null = null;
      try {
        const header = await apiFetch<{
          status?: string;
          lifecycle?: RunLifecycleFrame[];
        }>(ENDPOINTS.run(runId));
        if (disposed) return;
        const view = resolveRunLifecycle(header);
        if (view.terminal) settled = view.state;
      } catch {
        /* fall through to the socket — it announces the ending too */
      }

      if (disposed) return;
      if (settled) {
        settledSeen = true;
        enqueue({ type: "run.settled", state: settled });
        return;
      }
      attachSocket();
    })();

    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      timer = null;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      queue.length = 0;
      // Anything still queued never reached the reducer. Forget it so the next
      // subscription re-queues it instead of skipping it as already shown.
      queuedLines = new Map(emittedLines);
      queuedFinal = new Set(emittedFinal);
      // Null the handler first: `close()` fires `onclose`, which would
      // otherwise schedule a reconnect for a subscription being torn down.
      if (socket) socket.onclose = null;
      socket?.close();
      socket = null;
    };
  };
}
