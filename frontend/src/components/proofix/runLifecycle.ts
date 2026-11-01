/**
 * How a run's backend lifecycle becomes what the workspace renders.
 *
 * One pure function, because this mapping was previously spread across three
 * components and each of them only knew about two of the four outcomes. A run
 * the environment precheck blocked stops at A0.7: no A10 event ever fires, so
 * every "is it over?" test in the UI answered no and the header kept saying
 * `Status · Running` on a run that had been finished for minutes.
 *
 * The backend already publishes the truth in two independent places — the run's
 * `status` on `GET /api/runs/{id}`, and the authoritative `RunLifecycleEvent`
 * list alongside it. Either alone is enough to settle the journal; they are
 * combined here so a run whose lifecycle event never landed still settles, and
 * a run whose state has not yet been re-read settles the moment the socket
 * announces it.
 *
 * | backend status        | state       | header badge        | journal | decision            |
 * |-----------------------|-------------|---------------------|---------|---------------------|
 * | pending/running/retry | `running`   | Status · Running    | live    | Pending             |
 * | completed             | `completed` | Status · Completed  | settles | routing outcome     |
 * | failed                | `failed`    | Status · Failed     | settles | Failed + reason     |
 * | blocked               | `blocked`   | Status · Blocked    | settles | backend label+reason|
 *
 * Nothing here invents a reason: every string either comes from the backend or
 * is a fixed label for a state the backend named.
 */
import type { AgentStatus } from "./data";

/** The four states a run can be in, as the UI understands them. */
export type RunLifecycleState = "running" | "completed" | "failed" | "blocked";

/** Lifecycle frame, both as REST returns it and as the socket delivers it. */
export interface RunLifecycleFrame {
  type: string;
  reason?: string | null;
  decision_label?: string | null;
}

/** The precheck report, published verbatim on the workspace header. */
export interface EnvironmentReport {
  status?: string | null;
  language?: string | null;
  reason?: string | null;
  test_runner?: string | null;
  suggested_command?: string | null;
}

export interface RunLifecycleInput {
  /** `state.status` from `GET /api/runs/{id}`. */
  status?: string | null;
  /** `lifecycle` from the same response, or frames seen on the socket. */
  lifecycle?: RunLifecycleFrame[] | null;
  /** `environment` from the same response — A0.7's report. */
  environment?: EnvironmentReport | null;
  /** `decisionLabel` from the same response. */
  decisionLabel?: string | null;
}

export interface RunLifecycleView {
  state: RunLifecycleState;
  /** True once the run is over and the journal must stop implying execution. */
  terminal: boolean;
  /** Text for the status badge, e.g. `Status · Blocked`. */
  statusLabel: string;
  /** Which `StatusBadge` tone to use. */
  badgeStatus: AgentStatus;
  /** Text for the decision badge, e.g. `Environment not prepared`. */
  decisionLabel: string;
  /** Why the run ended, in the backend's words. `null` when it did not end badly. */
  reason: string | null;
}

// `pending`, `running` and `validation_retry` are the non-terminal statuses;
// they are not enumerated because anything not terminal is running.
const LIFECYCLE_BY_TYPE: Record<string, RunLifecycleState> = {
  "run.completed": "completed",
  "run.failed": "failed",
  "run.blocked": "blocked",
};

const BADGE_BY_STATE: Record<RunLifecycleState, AgentStatus> = {
  running: "running",
  completed: "completed",
  failed: "failed",
  blocked: "blocked",
};

const STATUS_TEXT: Record<RunLifecycleState, string> = {
  running: "Status · Running",
  completed: "Status · Completed",
  failed: "Status · Failed",
  blocked: "Status · Blocked",
};

/** Terminal frame types, newest-wins if several somehow appear. */
export function lifecycleStateFromFrames(frames: RunLifecycleFrame[] | null | undefined): {
  state: RunLifecycleState | null;
  frame: RunLifecycleFrame | null;
} {
  for (let i = (frames?.length ?? 0) - 1; i >= 0; i -= 1) {
    const frame = frames![i];
    const state = LIFECYCLE_BY_TYPE[frame?.type ?? ""];
    if (state) return { state, frame };
  }
  return { state: null, frame: null };
}

/** True for any socket frame that means "the run is over". */
export function isTerminalFrameType(type: unknown): boolean {
  return typeof type === "string" && type in LIFECYCLE_BY_TYPE;
}

/** Map a socket frame type onto the state it announces. */
export function stateForFrameType(type: string): RunLifecycleState | null {
  return LIFECYCLE_BY_TYPE[type] ?? null;
}

/**
 * Map a raw backend `status` onto a terminal state, or `null` if not terminal.
 *
 * Used for the legacy fallback frame, which is always named `run.completed`
 * and states the actual outcome on `status`.
 */
export function statusToLifecycleState(status: unknown): RunLifecycleState | null {
  if (status === "completed" || status === "failed" || status === "blocked") return status;
  return null;
}

export function resolveRunLifecycle(input: RunLifecycleInput): RunLifecycleView {
  const status = (input.status ?? "").trim();
  const fromFrames = lifecycleStateFromFrames(input.lifecycle);

  // A status the backend recorded as terminal is authoritative. Otherwise a
  // terminal lifecycle frame settles it — a run whose state has not been
  // re-polled yet is still over once it says so.
  const terminalStatus =
    status === "completed" || status === "failed" || status === "blocked"
      ? (status as RunLifecycleState)
      : null;
  const state: RunLifecycleState = terminalStatus ?? fromFrames.state ?? "running";

  const frameReason = fromFrames.state === state ? (fromFrames.frame?.reason ?? null) : null;
  const frameLabel = fromFrames.state === state ? (fromFrames.frame?.decision_label ?? null) : null;

  // The reason is the backend's, never composed here. For a blocked run the
  // lifecycle frame and the precheck report carry the same sentence; either is
  // acceptable and the frame is preferred because it is the terminal record.
  const reason =
    state === "blocked"
      ? frameReason || input.environment?.reason || null
      : state === "failed"
        ? frameReason
        : null;

  return {
    state,
    terminal: state !== "running",
    statusLabel: STATUS_TEXT[state],
    badgeStatus: BADGE_BY_STATE[state],
    decisionLabel: frameLabel || input.decisionLabel || (state === "failed" ? "Failed" : "Pending"),
    reason,
  };
}
