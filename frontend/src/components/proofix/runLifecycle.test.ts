/**
 * The backend-state → UI-state mapping, one case per lifecycle outcome.
 *
 * These exist because the mapping used to be implicit: "the run is over" meant
 * "an A10 completed event appeared", which is true for exactly one of the four
 * outcomes. Each test below names the outcome it pins and the thing the UI must
 * never say for it.
 */
import { describe, expect, it } from "vitest";
import {
  isTerminalFrameType,
  resolveRunLifecycle,
  stateForFrameType,
  statusToLifecycleState,
} from "./runLifecycle";

describe("resolveRunLifecycle", () => {
  // 1. Normal running run.
  it("keeps a running run live and never labels it terminal", () => {
    const view = resolveRunLifecycle({
      status: "running",
      lifecycle: [{ type: "run.started" }],
      decisionLabel: "Pending",
    });

    expect(view.state).toBe("running");
    expect(view.terminal).toBe(false);
    expect(view.statusLabel).toBe("Status · Running");
    expect(view.reason).toBeNull();
  });

  it("treats pending and validation_retry as still running", () => {
    for (const status of ["pending", "validation_retry"]) {
      expect(resolveRunLifecycle({ status }).state).toBe("running");
    }
  });

  // 2. Completed run.
  it("settles a completed run on the backend's own decision label", () => {
    const view = resolveRunLifecycle({
      status: "completed",
      lifecycle: [{ type: "run.started" }, { type: "run.completed", decision_label: "Auto Merge" }],
      decisionLabel: "Auto Merge",
    });

    expect(view.state).toBe("completed");
    expect(view.terminal).toBe(true);
    expect(view.statusLabel).toBe("Status · Completed");
    expect(view.decisionLabel).toBe("Auto Merge");
    expect(view.reason).toBeNull();
  });

  // 3. Failed run.
  it("shows a failed run's reason from the lifecycle event", () => {
    const view = resolveRunLifecycle({
      status: "failed",
      lifecycle: [{ type: "run.failed", reason: "RuntimeError: patch lock expired" }],
    });

    expect(view.state).toBe("failed");
    expect(view.terminal).toBe(true);
    expect(view.statusLabel).toBe("Status · Failed");
    expect(view.reason).toBe("RuntimeError: patch lock expired");
  });

  // 4. Blocked run.
  it("shows a blocked run as blocked, with the backend's reason", () => {
    const view = resolveRunLifecycle({
      status: "blocked",
      lifecycle: [
        {
          type: "run.blocked",
          decision_label: "Environment not prepared",
          reason: "No dependency manifest found in the repository.",
        },
      ],
      environment: { status: "blocked", reason: "No dependency manifest found." },
      decisionLabel: "Environment not prepared",
    });

    expect(view.state).toBe("blocked");
    expect(view.terminal).toBe(true);
    expect(view.statusLabel).toBe("Status · Blocked");
    expect(view.decisionLabel).toBe("Environment not prepared");
    // The reason is the backend's, verbatim, not composed here.
    expect(view.reason).toBe("No dependency manifest found in the repository.");
  });

  it("never reports a blocked run as running, completed or failed", () => {
    const view = resolveRunLifecycle({ status: "blocked" });
    expect(view.statusLabel).not.toContain("Running");
    expect(view.statusLabel).not.toContain("Completed");
    expect(view.statusLabel).not.toContain("Failed");
  });

  // 5. Blocked run with downstream agents absent — nothing after A0.7 ever
  //    emitted, so there is no A10 event to infer an ending from.
  it("settles a blocked run whose timeline stops at A0.7", () => {
    const view = resolveRunLifecycle({
      status: "blocked",
      lifecycle: [
        { type: "run.started" },
        { type: "run.blocked", reason: "pytest is not available in the target repository." },
      ],
    });

    expect(view.terminal).toBe(true);
    expect(view.reason).toBe("pytest is not available in the target repository.");
  });

  // 6. Blocked run with lifecycle event but no status yet — the socket
  //    announced the ending before the header was re-polled.
  it("settles on the lifecycle event alone when status is still running", () => {
    const view = resolveRunLifecycle({
      status: "running",
      lifecycle: [{ type: "run.blocked", reason: "No test runner detected." }],
    });

    expect(view.state).toBe("blocked");
    expect(view.terminal).toBe(true);
    expect(view.reason).toBe("No test runner detected.");
  });

  // 7. Blocked run without a lifecycle event, REST status already blocked.
  it("settles on REST status alone, falling back to the environment report", () => {
    const view = resolveRunLifecycle({
      status: "blocked",
      lifecycle: [],
      environment: { status: "blocked", reason: "Dependencies are not installed." },
      decisionLabel: "Environment not prepared",
    });

    expect(view.state).toBe("blocked");
    expect(view.terminal).toBe(true);
    expect(view.decisionLabel).toBe("Environment not prepared");
    expect(view.reason).toBe("Dependencies are not installed.");
  });

  it("reports no reason rather than inventing one", () => {
    const view = resolveRunLifecycle({ status: "blocked", lifecycle: [] });
    expect(view.reason).toBeNull();
  });

  it("ignores a stale reason belonging to a different outcome", () => {
    // Status is authoritative; a `run.failed` reason must not be attached to a
    // run the backend recorded as completed.
    const view = resolveRunLifecycle({
      status: "completed",
      lifecycle: [{ type: "run.failed", reason: "transient redis error" }],
    });
    expect(view.state).toBe("completed");
    expect(view.reason).toBeNull();
  });

  it("defaults to running when the backend has said nothing yet", () => {
    expect(resolveRunLifecycle({}).state).toBe("running");
    expect(resolveRunLifecycle({}).terminal).toBe(false);
  });
});

describe("frame helpers", () => {
  it("recognises all three terminal frame types and no others", () => {
    expect(isTerminalFrameType("run.completed")).toBe(true);
    expect(isTerminalFrameType("run.failed")).toBe(true);
    expect(isTerminalFrameType("run.blocked")).toBe(true);
    expect(isTerminalFrameType("run.started")).toBe(false);
    expect(isTerminalFrameType("ping")).toBe(false);
    expect(isTerminalFrameType(undefined)).toBe(false);
  });

  it("maps frame types onto states", () => {
    expect(stateForFrameType("run.blocked")).toBe("blocked");
    expect(stateForFrameType("run.started")).toBeNull();
  });

  it("reads the legacy fallback frame's status rather than its name", () => {
    // ws.py sends `{"type": "run.completed", "status": "blocked"}` for
    // backwards compatibility. The status is the truth.
    expect(statusToLifecycleState("blocked")).toBe("blocked");
    expect(statusToLifecycleState("failed")).toBe("failed");
    expect(statusToLifecycleState("completed")).toBe("completed");
    expect(statusToLifecycleState("running")).toBeNull();
    expect(statusToLifecycleState(undefined)).toBeNull();
  });
});
