/**
 * Pure reducer over backend execution events.
 *
 * This hook owns NO timing logic. Progression is driven entirely by an event
 * stream produced by `createEventSource` (default: `createMockEventSource`).
 * To wire a real backend, pass a factory that opens an SSE/WebSocket
 * connection and translates frames into `ExecutionEvent`s.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AGENTS as DEFAULT_AGENTS } from "./data";
import type { AgentEntry, AgentStatus } from "./data";
import {
  createMockEventSource,
  type EventSourceFactory,
  type ExecutionEvent,
} from "./mockEventStream";
import type { RunLifecycleState } from "./runLifecycle";

export interface LiveAgent extends AgentEntry {
  visibleLines: number;
  liveStatus: AgentStatus;
}

export interface UseExecutionRunOptions {
  /** Agents to play through. Supplied by runService once the backend is live. */
  agents?: AgentEntry[];
  /** Event-source factory. Replace with SSE/WS to consume backend events. */
  createEventSource?: EventSourceFactory;
  /**
   * Treat each agent's own `status` as authoritative once it settles.
   *
   * The event stream can only describe agents that emitted events, and only in
   * the vocabulary an event carries. Two things fall outside it: a stage the
   * pipeline routed around emits nothing at all and would spin as "running"
   * forever, and outcomes like `draft` or `retry` are derived from whole-run
   * state that no single event knows about — a bare `completed` frame would
   * flatten them. The backend already computes all of this correctly, so live
   * mode defers to it and uses events only for pacing while a stage is still
   * in flight. Mock mode leaves this off: its fixtures ship pre-completed, so
   * deferring to them would give the whole run away on the first frame.
   */
  seedStatusFromAgents?: boolean;
}

export function useExecutionRun(options: UseExecutionRunOptions = {}) {
  const {
    agents = DEFAULT_AGENTS,
    createEventSource = createMockEventSource,
    seedStatusFromAgents = false,
  } = options;

  const [token, setToken] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [visibleByIdx, setVisibleByIdx] = useState<number[]>(() => agents.map(() => 0));
  const [statusByIdx, setStatusByIdx] = useState<AgentStatus[]>(() =>
    agents.map(() => "running" as AgentStatus),
  );
  // Which ending the stream announced, or `null` while the run is live. `done`
  // is derived from it so every existing consumer keeps its boolean, while a
  // caller that must distinguish "finished" from "blocked" can.
  const [settledState, setSettledState] = useState<RunLifecycleState | null>(null);
  const done = settledState !== null;

  const restart = useCallback(() => {
    setVisibleByIdx(agents.map(() => 0));
    setStatusByIdx(agents.map(() => "running"));
    setActiveIndex(0);
    setSettledState(null);
    setToken((t) => t + 1);
  }, [agents]);

  /**
   * What the event source actually depends on: which cards exist and how many
   * lines each can reveal. Everything else on an `AgentEntry` — status,
   * duration, metrics — changes on almost every poll of a live run, and keying
   * the subscription on the array itself tore the WebSocket down and refetched
   * history every 2.5s, so the paced journal never got more than a few events
   * out before being restarted. Resubscribing on this key instead means the
   * stream survives a poll that only moved a number.
   */
  const topology = useMemo(
    () => agents.map((a) => `${a.id}:${a.lines.length}`).join("|"),
    [agents],
  );

  // The subscription reads the current list, but must not be rebuilt for it.
  const agentsRef = useRef(agents);
  agentsRef.current = agents;

  // Subscribe to the event source. Re-runs only on restart or topology change.
  useEffect(() => {
    const handle = (event: ExecutionEvent) => {
      switch (event.type) {
        case "agent.started":
          setActiveIndex(event.index);
          return;
        case "agent.line":
          setVisibleByIdx((prev) => {
            const next = [...prev];
            next[event.index] = event.lineIndex;
            return next;
          });
          return;
        case "agent.finalized":
          setStatusByIdx((prev) => {
            const next = [...prev];
            next[event.index] = event.status;
            return next;
          });
          return;
        case "run.settled":
          setSettledState(event.state);
          return;
      }
    };
    const dispose = createEventSource(agentsRef.current, handle);
    return dispose;
    // `token` is intentionally part of the dependency list to support restart.
    // `agents` is deliberately absent — see `topology` above.
  }, [token, topology, createEventSource]);

  const live: LiveAgent[] = useMemo(
    () =>
      agents.map((a, i) => {
        const fromEvents = statusByIdx[i] ?? "running";
        const settled = seedStatusFromAgents && a.status !== "running";
        // Backend wins once it has settled on an outcome; until then events
        // keep the card responsive between polls.
        const liveStatus = settled ? a.status : fromEvents;

        // A settled agent the stream never spoke for — a skipped stage emits no
        // events — would otherwise render as an empty card. It has nothing left
        // to animate, so show its narrative outright. Anything the stream is
        // already revealing keeps its paced count.
        const revealed = visibleByIdx[i] ?? 0;
        const visibleLines = settled && revealed === 0 ? a.lines.length : revealed;

        return { ...a, visibleLines, liveStatus };
      }),
    [agents, visibleByIdx, statusByIdx, seedStatusFromAgents],
  );

  return { agents: live, activeIndex, restart, done, settledState };
}
