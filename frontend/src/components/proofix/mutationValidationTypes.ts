/**
 * Types for A8's scoped-validation + mutation gauntlet endpoint
 * (`GET /api/runs/{runId}/mutation`, `services/ui_projection.py::build_mutation_validation`).
 *
 * A8 runs three gates in a fixed order and stops at the first one that
 * fails: the target test, the full regression suite, then mutmut against the
 * one file it patched. Nothing here is invented:
 *
 * - Every mutant carries `function` — real, from mutmut's own
 *   `x_<function>__mutmut_<n>` naming convention.
 * - Only a bounded subset (survivors first, then killed) also carries
 *   `line` / `before` / `after` — fetched from a real `mutmut show <id>`
 *   diff. A mutant without `line` is not placed on a code line; it is
 *   counted in `MutationFunctionAttack.unattributed` instead. Never guessed.
 * - `patchedLines` is a real diff against A7's original file — which lines
 *   the patch changed, not which lines a mutant merely attacked.
 * - `mutationStatus` is the only trustworthy "did this measure anything"
 *   signal. A `null` score renders as "not measured", never as 0%.
 */

/** Which of A8's three ordered gates the run actually reached. */
export type MutationStage = "target_test" | "regression" | "mutation" | "not_reached";

/** A8's three real outcomes for the mutation phase specifically. */
export type MutationStatus = "scored" | "unavailable" | "not_run";

export type MutantBucket = "killed" | "survived" | "inconclusive";

export interface MutationRetryContext {
  assertionMessage: string | null;
  expectedValue: string | null;
  actualValue: string | null;
  violatedContract: string | null;
  retryInstruction: string | null;
}

/** One mutant, placed on a code line — only when A8 fetched its real diff. */
export interface MutationMarker {
  mutantId: string;
  status: MutantBucket;
  line: number;
  /** Real only when A8 fetched this specific mutant's `mutmut show` diff. */
  before: string | null;
  after: string | null;
}

export interface MutationCodeLine {
  line: number;
  text: string;
}

/** One function mutmut attacked, with as much real attribution as A8 fetched. */
export interface MutationFunctionAttack {
  name: string;
  killed: number;
  survived: number;
  inconclusive: number;
  total: number;
  /** Mutants with a real, fetched line — the only ones placed in the gutter. */
  markers: MutationMarker[];
  /** Mutants attributed to this function but without a fetched line. */
  unattributed: number;
  /** Whether the patched source actually parsed and this function was found. */
  codeAvailable: boolean;
  spanStart: number | null;
  spanEnd: number | null;
  lines: MutationCodeLine[];
  /** Lines this patch actually changed (real diff vs. A7's original) — the
   * left-edge "patched" indicator, distinct from a line a mutant attacked. */
  patchedLines: number[];
}

/** A mutant the test suite failed to notice — the finding A8 exists to produce. */
export interface MutationSurvivor {
  mutantId: string;
  function: string | null;
  file: string | null;
  /** `null` when this survivor's diff was never fetched (outside the budget). */
  line: number | null;
  before: string | null;
  after: string | null;
}

/** `GET /api/runs/{runId}/mutation`. */
export interface MutationValidationReport {
  stage: MutationStage;
  pytestAvailable: boolean;
  pytestPassed: boolean;
  targetTestId: string | null;
  targetTestPassed: boolean | null;
  regressionTestsPassed: boolean | null;
  newFailures: string[];
  preExistingFailures: string[];
  mutationStatus: MutationStatus;
  unavailableReason: string | null;
  /** Killed / evaluated, 0-1, or `null` when never scored. */
  mutationScore: number | null;
  mutantSurvived: boolean | null;
  killedMutants: number | null;
  survivedMutants: number | null;
  totalMutants: number | null;
  inconclusiveMutants: number | null;
  mutantsByStatus: Record<string, number>;
  correctnessScore: number | null;
  correctnessThreshold: number;
  patchRetryRequired: boolean;
  pytestReexecutionCommand: string;
  reexecutionCommand: string;
  reexecutionTimeoutSeconds: number | null;
  retryContext: MutationRetryContext | null;
  patchFile: string | null;
  functions: MutationFunctionAttack[];
  survivors: MutationSurvivor[];
  /** Mutants mutmut reported with no function attribution at all (rare). */
  unattributedCounts: Record<MutantBucket, number>;
}
