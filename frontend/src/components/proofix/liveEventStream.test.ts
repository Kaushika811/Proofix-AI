/**
 * The live stream's terminal detection.
 *
 * The bug these pin: the stream decided a run was over by looking for an A10
 * `completed` event in the replayed history. A run blocked at A0.7 has no A10
 * event and never will, so the journal stayed live forever on a finished run.
 * Whether a run is over is now the backend's `status` and lifecycle record.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentEntry } from "./data";
import type { ExecutionEvent } from "./mockEventStream";

const apiFetch = vi.fn();

vi.mock("@/lib/api", () => ({
  API_BASE_URL: "http://127.0.0.1:8000",
  ENDPOINTS: {
    run: (id: string) => `/api/runs/${id}`,
    runEvents: (id: string) => `/api/runs/${id}/events`,
  },
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

const { createLiveEventSource } = await import("./liveEventStream");

function agent(id: string, lines: number): AgentEntry {
  return {
    id,
    index: 1,
    agent: id,
    purpose: "",
    handoff: "",
    status: "running",
    duration: "",
    lines: Array.from({ length: lines }, (_, i) => `line ${i}`),
    evidence: { title: "", subtitle: "", fields: [] },
  };
}

/** The V1 card list a blocked run gets back from `/agents`. */
const AGENTS = [agent("environment", 2), agent("repo-intel", 2), agent("merge", 2)];

/** Drain the paced queue: it emits one event per timer tick. */
async function drain(): Promise<void> {
  for (let i = 0; i < 200; i += 1) {
    await Promise.resolve();
    vi.advanceTimersByTime(500);
  }
}

function collect(runId: string) {
  const events: ExecutionEvent[] = [];
  const dispose = createLiveEventSource(runId)(AGENTS, (e) => events.push(e));
  return { events, dispose };
}

beforeEach(() => {
  vi.useFakeTimers();
  apiFetch.mockReset();
  // No socket in this environment: the stream must reach its verdict from REST
  // alone, which is exactly the path a reopened finished run takes.
  vi.stubGlobal("WebSocket", undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("createLiveEventSource terminal detection", () => {
  // 1. Normal running run.
  it("does not settle a run the backend still reports as running", async () => {
    apiFetch.mockImplementation(async (url: string) =>
      url.endsWith("/events")
        ? [{ agent_id: "A1", status: "progress", message: "", sequence: 1 }]
        : { status: "running", lifecycle: [{ type: "run.started" }] },
    );

    const { events, dispose } = collect("run-1");
    await drain();
    dispose();

    expect(events.some((e) => e.type === "run.settled")).toBe(false);
  });

  // 2. Completed run.
  it("settles a completed run as completed", async () => {
    apiFetch.mockImplementation(async (url: string) =>
      url.endsWith("/events")
        ? [{ agent_id: "A10", status: "completed", message: "", sequence: 9 }]
        : { status: "completed", lifecycle: [{ type: "run.completed" }] },
    );

    const { events, dispose } = collect("run-2");
    await drain();
    dispose();

    expect(events.filter((e) => e.type === "run.settled")).toEqual([
      { type: "run.settled", state: "completed" },
    ]);
  });

  // 3. Failed run.
  it("settles a failed run as failed", async () => {
    apiFetch.mockImplementation(async (url: string) =>
      url.endsWith("/events")
        ? [{ agent_id: "A4", status: "failed", message: "", sequence: 4 }]
        : { status: "failed", lifecycle: [{ type: "run.failed", reason: "boom" }] },
    );

    const { events, dispose } = collect("run-3");
    await drain();
    dispose();

    expect(events.filter((e) => e.type === "run.settled")).toEqual([
      { type: "run.settled", state: "failed" },
    ]);
  });

  // 4 + 5. Blocked run, downstream agents absent from the timeline entirely.
  it("settles a blocked run whose timeline stops at A0.7", async () => {
    apiFetch.mockImplementation(async (url: string) =>
      url.endsWith("/events")
        ? [
            { agent_id: "A0.7", status: "started", message: "Checking", sequence: 1 },
            { agent_id: "A0.7", status: "failed", message: "No manifest", sequence: 2 },
          ]
        : {
            status: "blocked",
            lifecycle: [{ type: "run.blocked", reason: "No manifest" }],
          },
    );

    const { events, dispose } = collect("run-4");
    await drain();
    dispose();

    // The run settles, and as blocked — never as completed.
    expect(events.filter((e) => e.type === "run.settled")).toEqual([
      { type: "run.settled", state: "blocked" },
    ]);

    // A0.7 is visible in the journal: its card is announced and finalized.
    const envIndex = AGENTS.findIndex((a) => a.id === "environment");
    expect(events).toContainEqual({ type: "agent.started", index: envIndex });
    expect(events).toContainEqual({
      type: "agent.finalized",
      index: envIndex,
      status: "failed",
    });

    // Downstream agents are not fabricated: nothing finalizes them.
    const mergeIndex = AGENTS.findIndex((a) => a.id === "merge");
    expect(events.some((e) => e.type === "agent.finalized" && e.index === mergeIndex)).toBe(false);
  });

  // 7. Blocked run without a lifecycle event; REST status already blocked.
  it("settles on REST status alone when no lifecycle event was recorded", async () => {
    apiFetch.mockImplementation(async (url: string) =>
      url.endsWith("/events") ? [] : { status: "blocked", lifecycle: [] },
    );

    const { events, dispose } = collect("run-5");
    await drain();
    dispose();

    expect(events.filter((e) => e.type === "run.settled")).toEqual([
      { type: "run.settled", state: "blocked" },
    ]);
  });
});

/**
 * Reconnect (B-F03).
 *
 * A close used to be terminal for the transport: the socket dropped, nothing
 * re-attached, and the run silently fell back to the 2.5s REST poll — updating
 * but no longer narrating. The invariant that must survive reconnect is the one
 * the original `onclose` omission protected: a close is never evidence the run
 * finished.
 */
describe("createLiveEventSource reconnect", () => {
  /** Minimal socket double that records instances and lets tests drive events. */
  class FakeSocket {
    static instances: FakeSocket[] = [];
    onmessage: ((e: { data: string }) => void) | null = null;
    onclose: (() => void) | null = null;
    onopen: (() => void) | null = null;
    closed = false;

    constructor() {
      FakeSocket.instances.push(this);
    }
    close() {
      this.closed = true;
      this.onclose?.();
    }
    /** Simulate a transport-level drop, as distinct from our own dispose. */
    drop() {
      this.onclose?.();
    }
  }

  beforeEach(() => {
    FakeSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeSocket);
  });

  /** A run the backend still reports as live, so the socket path is taken. */
  const liveBackend = (events: unknown[] = []) =>
    apiFetch.mockImplementation(async (url: string) =>
      url.endsWith("/events") ? events : { status: "running", lifecycle: [] },
    );

  it("re-attaches after the socket drops mid-run", async () => {
    liveBackend();
    const { dispose } = collect("run-rc1");
    await drain();

    expect(FakeSocket.instances).toHaveLength(1);
    FakeSocket.instances[0].drop();
    await drain();

    expect(FakeSocket.instances.length).toBeGreaterThan(1);
    dispose();
  });

  it("never emits run.settled because the socket closed", async () => {
    liveBackend();
    const { events, dispose } = collect("run-rc2");
    await drain();

    FakeSocket.instances[0].drop();
    await drain();

    expect(events.some((e) => e.type === "run.settled")).toBe(false);
    dispose();
  });

  it("does not duplicate journal lines when history replays on reconnect", async () => {
    // The same history is served on both fetches — exactly what a real backend
    // returns when nothing new happened while the socket was down.
    liveBackend([
      { agent_id: "A0.7", status: "completed", message: "", sequence: 1 },
      { agent_id: "A1", status: "completed", message: "", sequence: 2 },
    ]);

    const { events, dispose } = collect("run-rc3");
    await drain();
    const before = events.length;

    FakeSocket.instances[0].drop();
    await drain();

    expect(events).toHaveLength(before);
    dispose();
  });

  it("stops reconnecting once the run has settled", async () => {
    apiFetch.mockImplementation(async (url: string) =>
      url.endsWith("/events") ? [] : { status: "running", lifecycle: [] },
    );
    const { dispose } = collect("run-rc4");
    await drain();

    const socket = FakeSocket.instances[0];
    socket.onmessage?.({ data: JSON.stringify({ type: "run.completed", status: "completed" }) });
    await drain();

    const afterSettle = FakeSocket.instances.length;
    socket.drop();
    await drain();

    expect(FakeSocket.instances).toHaveLength(afterSettle);
    dispose();
  });

  it("does not reconnect after dispose", async () => {
    liveBackend();
    const { dispose } = collect("run-rc5");
    await drain();

    const count = FakeSocket.instances.length;
    dispose();
    await drain();

    expect(FakeSocket.instances).toHaveLength(count);
  });
});
