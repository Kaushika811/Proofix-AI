/**
 * Types for A10's mergeability decision endpoint
 * (`GET /api/runs/{runId}/decision`, `services/ui_projection.py::build_mergeability_decision`).
 *
 * A10 does not re-verify correctness or security: `axes` for `correctness`
 * and `security` are A8's and A9's own scores, passed through unchanged. A10
 * verifies exactly one thing itself (the MCI description-vs-diff phantom
 * check, `phantomChangesDetected`) and computes two axes of its own
 * (`fidelity`, `scope_risk`). Everything in `hardGates` and
 * `routingModifiers` is A10 reading evidence other agents produced, in the
 * same order the real router evaluates it — nothing here is re-derived with
 * different logic than the backend's own `a10_routing.gate_checks`.
 */

/** The three real PR routing outcomes. Never a fourth, invented one. */
export type PRType = "auto_mergeable" | "diff_only" | "draft";

export interface MergeabilityAxis {
  name: "correctness" | "security" | "fidelity" | "scope_risk";
  label: string;
  /** `null` when the axis was never measured — never a substituted `0`. */
  value: number | null;
  measured: boolean;
  lowThreshold: number;
  /** `null` when `value` is `null` — an unmeasured axis has not failed. */
  meetsLowThreshold: boolean | null;
  /**
   * Security only: the stricter bar `technical_validation_passed` actually
   * applies for auto-merge eligibility (90, vs the generic 80 above). A
   * score can clear `lowThreshold` and still block auto-merge here — both
   * facts are real and neither should be hidden behind the other.
   */
  autoMergeThreshold?: number;
  meetsAutoMergeThreshold?: boolean | null;
}

/**
 * One gate from A10's real ten-gate short-circuit chain, in the router's own
 * order. `checked: false` means an earlier gate already fired and this one
 * was never evaluated — a fact distinct from "passed", and the panel must
 * never draw a `checked: false` gate as if it cleared.
 */
export interface HardGate {
  code: string;
  label: string;
  checked: boolean;
  /** `null` when `checked` is `false`. */
  passed: boolean | null;
  detail: string | null;
}

/**
 * Only meaningful once every hard gate is clear (`hardGatesClear: true`) — a
 * run that hard-blocked earlier never reached these facts, so they are
 * `null` rather than a guess at what they would have been.
 */
export interface RoutingModifiers {
  hardGatesClear: boolean;
  citationReviewNeeded: boolean | null;
  reproductionConfidence: "exact_test" | "full_suite" | null;
  securityMeetsAutoMergeThreshold: boolean | null;
}

export interface VerificationStep {
  name: string;
  command: string;
  baseCommit: string;
  patchCommit: string;
  expectedResult: string;
  timeoutSeconds: number;
  isTargeted: boolean;
}

export interface ProofBundle {
  bundleHash: string | null;
  reproductionConfidence: string | null;
  steps: VerificationStep[];
}

/**
 * A scale-invariant summary of the evidence A10's decision rests on — real
 * counts re-read from A1's SIG, A5's blast radius, and A7's patch bundle, not
 * a fabricated repository-size metric. The panel renders these as aggregate
 * chips regardless of whether the repository has 5 files or 100,000; the
 * file/module lists behind `changedFiles` and `blastScopeFiles` only render
 * once a reviewer explicitly expands them.
 *
 * A count is `null` only when the agent that would have produced it never
 * ran (`filesAnalyzed` before A1, `dependencyEdgeCount` before A5) — never a
 * substituted `0` standing in for "not measured".
 */
export interface RepositoryEvidence {
  filesAnalyzed: number | null;
  changedFiles: string[];
  affectedModules: string[];
  blastScopeFiles: string[];
  dependencyEdgeCount: number | null;
}

/** `GET /api/runs/{runId}/decision`. */
export interface MergeabilityDecision {
  prType: PRType;
  decisionLabel: string;
  reviewNote: string | null;
  /** Mean of the measured axes only, 0-1 scale — the same number shown as
   * "Trust score" elsewhere. Descriptive, not itself the routing gate: the
   * real decision comes from the axis thresholds and hard gates below, not
   * from this composite clearing a bar. */
  trust: number | null;
  axes: MergeabilityAxis[];
  hardGates: HardGate[];
  routingModifiers: RoutingModifiers;
  phantomChangesDetected: boolean;
  /** Real PR URL, a dry-run placeholder URL, or `null` if nothing was
   * published (no configured target, or an exception during publish). */
  prUrl: string | null;
  descriptionWhy: string;
  descriptionWhat: string;
  proofBundle: ProofBundle | null;
  /** `null` before any of A1/A5/A7 has produced anything to summarize. */
  repositoryEvidence: RepositoryEvidence | null;
}
