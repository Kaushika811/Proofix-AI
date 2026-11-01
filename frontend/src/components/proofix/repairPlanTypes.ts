/**
 * Types for A6's repair plan endpoint
 * (`GET /api/runs/{runId}/repair-plan`, `services/ui_projection.py::build_repair_plan`).
 *
 * Distinct from `/api/runs/{runId}/plan`, which returns `state.fix_dag`
 * verbatim for API consumers that already depend on that exact shape. This is
 * the projected, joined view the workspace board reads: every step in order,
 * why it exists (joined against A3's findings / A2's CVE records — `FixNode`
 * itself carries no message), which steps it conflicts with over a shared
 * file, and A5.5's acceptance criteria / patch constraints carried forward as
 * *their* evidence, not recomputed or claimed as A6's own.
 *
 * **A6's plan is not what A7 executes.** `executionAuthority` states this
 * explicitly — A7 reads only `execution_order[0]` as a label and derives its
 * real patch targets from A5's blast scope and A4's root cause. Any UI built
 * on this data must keep that fact visible, not bury it.
 *
 * There is no confidence field anywhere in this contract, on purpose: nothing
 * in A6 computes one, and this UI must never invent it.
 */

export type OrderingSource = "llm" | "deterministic" | null;

export interface DependencyEdgeRef {
  fromIssue: string;
  reason: string;
}

/** Why a step exists — joined from A3 or A2, never authored by A6 itself. */
export type RepairStepWhy =
  | {
      kind: "static_finding";
      message: string | null;
      /** A3's 0..1 severity, only when a tool genuinely measured one. */
      severity: number | null;
      severityMeasured: boolean;
      tools: string[];
    }
  | {
      kind: "cve";
      package: string | null;
      /** OSV's free-form severity string — sometimes a CVSS score, sometimes a word. */
      severity: string | null;
      installedVersion: string | null;
      reachPath: string[] | null;
    }
  | null;

export interface RepairStep {
  evidence?: {
    execution: "applied" | "partially_applied" | "unavailable";
    appliedFiles: string[];
    changes: {
      file: string;
      targetFunction: string | null;
      expectedOutcome: string;
      diff: string;
    }[];
    validation: "passed" | "failed" | "unavailable";
    validationScope: "patch_bundle" | null;
    targetTestPassed: boolean | null;
    regressionTestsPassed: boolean | null;
    patchHash: string | null;
    note: string;
  };
  issueId: string;
  /** 1-based position in the plan — the LLM ordering path may omit nodes, in
   * which case omitted steps are appended here rather than dropped. */
  position: number;
  /** False for a node the LLM path built but did not name in its order. */
  ordered: boolean;
  files: string[];
  dependsOn: string[];
  incomingEdges: DependencyEdgeRef[];
  /** Other issue IDs that share a file with this step — not "blocked", a
   * coordination fact: these fixes cannot be applied independently. */
  conflictsWith: string[];
  why: RepairStepWhy;
  /** True for exactly the step named by `execution_order[0]` — the one
   * value `a7_code_generation.py` actually reads from this entire plan.
   * False for every other step, and for everyone when `execution_order`
   * is empty. */
  isHandoffTarget: boolean;
}

export interface ExecutionAuthority {
  consumedBy: string;
  field: string;
  note: string;
}

export interface CarriedForwardEvidence {
  acceptanceCriteria: string[];
  patchConstraints: string[];
  /** A5.5's own resolved target function for this run — A6 has no
   * function-level analysis of its own. `null` when A5.5 ran but resolved
   * none for the target file (a real, honest outcome, not an omission). */
  targetFunction: string | null;
}

/** `GET /api/runs/{runId}/repair-plan`. */
export interface RepairPlan {
  steps: RepairStep[];
  conflictBatches: string[][];
  /** `null` on a run predating this field — never defaulted to "deterministic". */
  orderingSource: OrderingSource;
  /** The LLM's own account of its ordering choice. Empty when the
   * deterministic path ran, or when the LLM gave none. */
  orderingRationale: string;
  /** `topological_execution_order(nodes, dependency_edges)` — computed by A6
   * unconditionally, independent of which path produced `execution_order`
   * above. Equal to `execution_order` step-for-step when `orderingSource ===
   * "deterministic"`. Empty on a run predating this field. This is what makes
   * the LLM-vs-graph comparison a real fact rather than a client guess: never
   * recompute a topological order in this UI, only ever read this one. */
  deterministicOrder: string[];
  totalDependencyEdges: number;
  executionAuthority: ExecutionAuthority;
  /** `null` when A5.5 has not produced a context package for this run —
   * distinct from an empty-arrays package, which means it ran and found none. */
  carriedForward: CarriedForwardEvidence | null;
}
