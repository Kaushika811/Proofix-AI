/**
 * Typed payloads consumed by `AgentVisualization`. Each agent's visualization
 * renders entirely from one of these shapes — no inline literals.
 *
 * Backed today by the mock fixtures attached to `AgentEntry.visualization`
 * in `data.ts`. A backend implementation should serialize these shapes
 * verbatim per agent.
 */

export interface ContextPayload {
  /** Non-null when A5.5 resolved no target and handed A7 nothing. */
  skipped: string | null;
  targetFile: string;
  targetFunction: string | null;
  originalTokens: number;
  reducedTokens: number;
  /** Fraction saved (0-1), or `null` when no reduction was measured. */
  tokenReduction: number | null;
  contextFiles: number;
  contextFunctions: number;
  filesRanked: number;
  /**
   * The privacy boundary. `masked` means secrets were found and replaced before
   * the prompt left the process; `failed` means the guard itself errored, so
   * nothing may be assumed about what got through. Never round either to
   * `clean`.
   */
  privacyGuardStatus: "clean" | "masked" | "failed";
  redactions: number;
  degraded: boolean;
}

/** One timed stage of A0.5's index build. Only stages that took time are sent. */
export interface IndexPhase {
  label: string;
  ms: number;
}

export interface IntelligencePayload {
  /** How the index was obtained: "cache hit" | "incremental" | "full rebuild". */
  mode: string;
  /** The backend's own sentence explaining that mode. Rendered verbatim. */
  modeDetail: string;
  phases: IndexPhase[];
  totalMs: number;
  metrics: {
    nodes: number;
    edges: number;
    callables: number;
    commits: number;
    rememberedRepairs: number;
  };
  capabilities: string[];
}

export interface RepoFile {
  name: string;
  ast: number;
}

export interface RepoIntelPayload {
  files: RepoFile[];
  graphNodes: number;
  metrics: {
    files: number;
    imports: number;
    dependencies: number;
    semanticRoles: number;
  };
}

export interface DepNode {
  name: string;
  sub: string;
}
export interface DepDeadNode {
  name: string;
}
export interface DepsPayload {
  path: DepNode[];
  unreachable: DepDeadNode[];
  metrics: {
    reachable: number;
    deadFindings: number;
    attackPaths: number;
  };
}

export type FindingSeverity = "HIGH" | "MEDIUM" | "LOW";
export interface StaticFinding {
  sev: FindingSeverity;
  text: string;
  at: number;
}
export interface StaticPayload {
  scanners: string[];
  findings: StaticFinding[];
  metrics: {
    raw: number;
    deduped: number;
    prioritized: number;
  };
}

export type PytestResult = "PASS" | "FAIL";
export interface PytestEntry {
  name: string;
  result: PytestResult;
}
export interface ReproducePayload {
  command: string;
  tests: PytestEntry[];
  failure: {
    name: string;
    assertion: string;
    expected: string;
    actual: string;
    stack: string[];
  };
  successMessage: string;
}

export interface RootLine {
  code: string;
  probe: string;
}
export interface RootEvidence {
  n: number;
  title: string;
  detail: string;
  conf: number;
}
export interface RootCausePayload {
  lines: RootLine[];
  bugMessage: string;
  evidence: RootEvidence[];
}

export interface BlastModule {
  name: string;
  hitAt: number;
}
export interface BlastPayload {
  source: string;
  modules: BlastModule[];
}

export interface DAGNode {
  id: string;
  label: string;
  x: number;
  y: number;
}
export interface PlannerPayload {
  nodes: DAGNode[];
  edges: [number, number][];
}

/**
 * A7's own generation provenance for one patched file — not the diff itself
 * (that is `PatchBundle`, fetched separately from `GET /runs/{id}/patch`).
 * Every field here is a direct read of `a7_patch_metrics`, the same dict A7
 * computes for its own retry-history feed; nothing is inferred or guessed.
 */
export interface PatchFileProvenance {
  file: string;
  /** A7's own word for how it wrote the file (e.g. "ast_validated_write"), or `null` if unknown. */
  method: string | null;
  /** True only when this file equals the blast graph's resolved origin — real set membership, not a guess. */
  isTarget: boolean;
  /** `null` when A7 could not resolve a function-level target for this file. */
  targetFunction: string | null;
  /** "llm" or "stub" — `null` only if A7 never recorded an attempt for this file. */
  generationSource: "llm" | "stub" | null;
  /** "a5_5_context_package" or "a7_local_extraction", or `null` in stub mode (no prompt was built). */
  contextSource: string | null;
  runtimePrompt: boolean | null;
  retryNumber: number | null;
  retryReason: string | null;
  semanticDiff: boolean | null;
  /**
   * `false` for a plan A7 attempted but never wrote — every retry rejected
   * by the integrity guard, or the LLM call itself came back empty. `true`
   * for every file present in `PatchBundle.patches`. `undefined` only for
   * provenance from a run recorded before this field existed.
   */
  written?: boolean;
}
export interface PatchPayload {
  files: PatchFileProvenance[];
}

/**
 * A8 records an aggregate result, not a per-mutant ledger, so there is no
 * mutant list to render. The previous `mutants: Mutant[]` was synthesised in
 * the projection layer from a boolean.
 */
export interface MutationPayload {
  /** Fraction of mutants killed (0-1), or `null` when mutation testing never ran. */
  score: number | null;
  /** Whether any mutant survived the suite, or `null` when mutation was never scored. */
  survived: boolean | null;
  /** Count of surviving mutants, or `null` when mutation was never scored. */
  survivedMutants: number | null;
  /** Whether the target repo's test suite passed at all. */
  pytestPassed: boolean;
  /** Correctness score (0-100), or `null` when unmeasured. */
  correctness: number | null;
  /** The gate A10 applies to `correctness`. */
  correctnessThreshold: number;
  failureMessage: string;
}

export interface MergeMetric {
  label: string;
  /** `null` when the backend never measured this axis — never a substituted 0. */
  value: number | null;
  ok: boolean;
  /** False for a `null` value; true only for an axis the backend actually scored. */
  measured: boolean;
  /** When supplied, the value renders as a label (e.g. "Low") once the count-up reaches it. */
  scopeLabel?: string;
}
export interface MergePayload {
  metrics: MergeMetric[];
  /**
   * The backend's own composite trust score (0-100), or `null` when nothing
   * was measured. The one authoritative number — never recomputed from
   * `metrics` here, which would be a second trust formula disagreeing with
   * the one shown elsewhere on the page.
   */
  compositeScore: number | null;
  decisionLabel: string;
  reviewNote: string;
}

export type AgentVisualizationPayload =
  | { kind: "intelligence"; data: IntelligencePayload }
  | { kind: "context"; data: ContextPayload }
  | { kind: "repo-intel"; data: RepoIntelPayload }
  | { kind: "deps"; data: DepsPayload }
  | { kind: "static"; data: StaticPayload }
  | { kind: "reproduce"; data: ReproducePayload }
  | { kind: "root"; data: RootCausePayload }
  | { kind: "blast"; data: BlastPayload }
  | { kind: "planner"; data: PlannerPayload }
  | { kind: "patch"; data: PatchPayload }
  | { kind: "mutation"; data: MutationPayload }
  | { kind: "merge"; data: MergePayload };
