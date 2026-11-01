/**
 * Mock execution event stream.
 *
 * This module owns ALL execution-timing logic. `useExecutionRun` is a pure
 * reducer over events emitted here. Swap this factory for an SSE / WebSocket
 * / polling implementation to drive the journal from a real backend without
 * touching the hook or any component.
 */
import type { AgentEntry, AgentStatus } from "./data";
import type { RunLifecycleState } from "./runLifecycle";

/**
 * `run.settled` replaced the old `run.completed`: a run also ends by failing
 * and by being blocked at the environment precheck, and a single "completed"
 * event could only describe one of the three. It carries which.
 */
export type ExecutionEvent =
  | { type: "agent.started"; index: number }
  | { type: "agent.line"; index: number; lineIndex: number }
  | { type: "agent.finalized"; index: number; status: AgentStatus }
  | { type: "run.settled"; state: RunLifecycleState };

export type EventEmitter = (event: ExecutionEvent) => void;

/**
 * Subscribe to a stream of execution events for the given run.
 *
 * Returns a dispose function the consumer must call on unmount / restart.
 * Implementations:
 *  - mock  → schedule events via setTimeout (see `createMockEventSource`)
 *  - SSE   → `const es = new EventSource(ENDPOINTS.runEvents(runId))`
 *  - WS    → `new WebSocket(...)` and translate frames into ExecutionEvent
 *  - poll  → `setInterval` over `GET /runs/:id/events?cursor=...`
 */
export type EventSourceFactory = (agents: AgentEntry[], emit: EventEmitter) => () => void;

const LINE_INTERVAL_MS = 520;
// Pause after an agent finalizes so the user can read the final lines,
// visualizations, and generated outputs before the next agent appears.
const AGENT_GAP_MS = 900;
const START_DELAY_MS = 250;

export const createMockEventSource: EventSourceFactory = (agents, emit) => {
  const timeouts: ReturnType<typeof setTimeout>[] = [];
  const schedule = (fn: () => void, ms: number) => {
    timeouts.push(setTimeout(fn, ms));
  };

  let cursor = START_DELAY_MS;
  agents.forEach((agent, i) => {
    const agentStart = cursor;
    schedule(() => emit({ type: "agent.started", index: i }), agentStart);
    agent.lines.forEach((_, li) => {
      schedule(
        () => emit({ type: "agent.line", index: i, lineIndex: li + 1 }),
        agentStart + li * LINE_INTERVAL_MS,
      );
    });
    const finalizeAt = agentStart + agent.lines.length * LINE_INTERVAL_MS + 200;
    schedule(() => emit({ type: "agent.finalized", index: i, status: agent.status }), finalizeAt);
    cursor = finalizeAt + AGENT_GAP_MS;
  });
  schedule(() => emit({ type: "run.settled", state: "completed" }), cursor);

  return () => {
    timeouts.forEach(clearTimeout);
    timeouts.length = 0;
  };
};
