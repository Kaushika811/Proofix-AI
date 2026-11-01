/**
 * Types for A3.5's failure-reproduction evidence endpoint
 * (`GET /api/runs/{runId}/reproduction`, `services/ui_projection.py::build_reproduction_evidence`).
 *
 * A3.5 is an independent full-suite pytest gate — it does not target a
 * specific A3 finding (confirmed by reading `orchestrator/nodes.py`, which
 * never passes `static_report` into the reproduction step). This panel
 * therefore never claims a finding-to-reproduction link that does not exist;
 * it answers a narrower, real question: does the reported bug reproduce as a
 * failing test right now, in this repository.
 *
 * Every field mirrors `ReproductionResult` (`models/reproduction.py`)
 * field-for-field. Fields the backend cannot measure are `null`, never
 * defaulted or invented.
 */

/** A3.5's four real outcomes, in the UI's own vocabulary. Never a 5th, invented state. */
export type ReproductionUiStatus = "reproduced" | "not_reproduced" | "unavailable" | "error";

/** Which parser branch produced CONFIRMED evidence — the real source of the 0.9 vs 0.7 confidence split. */
export type EvidenceSource = "pytest_report" | "output_text";

export type StageStatus = "done" | "not_triggered" | "skipped" | "failed";

/**
 * One of the five real stages A3.5's execution passes through, derived
 * post-hoc and deterministically from the final result — not live progress.
 * A3.5 runs as a single atomic subprocess call with no intermediate events.
 */
export interface ReproductionStage {
  id: "suite_executed" | "tests_collected" | "tests_run" | "failure_observed" | "evidence_captured";
  label: string;
  status: StageStatus;
  detail: string;
}

/** `GET /api/runs/{runId}/reproduction`. */
export interface ReproductionEvidence {
  status: "CONFIRMED" | "UNCONFIRMED" | "NO_TESTS" | "INFRA_ERROR";
  uiStatus: ReproductionUiStatus;
  /** Real confidence A3.5 computed: 0.9 (structured report), 0.7 (text fallback), or 0.0. Never invented. */
  confidence: number;
  evidenceSource: EvidenceSource | null;

  failingTest: string | null;
  exceptionType: string | null;
  exceptionMessage: string | null;
  /** `${exceptionType}: ${exceptionMessage}`, or just the type, or `null` — never fabricated. */
  errorSignature: string | null;
  failingFile: string | null;
  failingLine: number | null;
  traceback: string | null;
  infraDetail: string | null;

  /** The command A3.5 actually ran to produce this evidence (always the full-suite gate). */
  command: string | null;
  exitCode: number | null;
  timedOut: boolean;
  stdout: string | null;
  stderr: string | null;
  durationSeconds: number | null;
  startedAt: string | null;
  finishedAt: string | null;

  testsCollected: number | null;
  testsPassed: number | null;
  testsFailed: number | null;

  /**
   * A *different*, targeted command built for a later validation stage
   * (A8, proof bundles) — never itself executed to produce this result.
   * Rendering it beside `command` without labelling the distinction would
   * imply A3.5 ran a targeted reproduction when it always runs the full suite.
   */
  reexecutionCommand: string | null;
  reexecutionIsTargeted: boolean;
  reexecutionTimeoutSeconds: number | null;

  /** Other pre-existing failures from the same run, excluding `failingTest` itself. */
  baselineFailures: string[];

  stages: ReproductionStage[];
}
