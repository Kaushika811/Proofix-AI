/**
 * Types for A5's blast-radius impact endpoint
 * (`GET /api/runs/{runId}/blast`, `services/ui_projection.py::build_blast_impact`).
 *
 * Distinct from A2's dependency-risk endpoint: A2 answers whether a
 * third-party advisory reaches this repository's code; A5 answers what could
 * be affected if the file A4 is investigating changes. Every field mirrors
 * `BlastGraphResult` (`models/blast.py`) field-for-field, joined read-only
 * against A1's SIG (role/criticality/churn) and A3's findings.
 *
 * Vocabulary this UI is careful never to blur:
 *   "changing"          the resolved origin — A5's own resolution, with its
 *                        source and confidence
 *   "could be affected"  hop 1 — a direct import edge exists
 *   "reachable"          hop 2-3 — transitive, decayed by distance
 *   "priority"           NOT "risk" — `risk_score` cannot distinguish "no
 *                        churn measured" from "measured zero churn"
 *                        (`RISK_MEASUREMENT_CAVEAT`), so this UI never claims
 *                        certainty the backend does not have.
 * Nothing here is ever labelled "affected" or "definitely affected".
 */

export type BlastDirection = "forward" | "backward";

/** How a traversal edge's target was resolved from a raw import string —
 * neither branch is a verified import resolution, only string matching. */
export type EdgeBasis = "resolved_suffix" | "name_contains";

export type ResolutionSource =
  "stack_trace" | "root_cause" | "sig_lookup" | "import_mapping" | "fallback" | null;

export interface BlastOrigin {
  originalPath: string;
  normalizedPath: string;
  resolvedPath: string | null;
  source: ResolutionSource;
  /** `null` on a run predating `target_resolution`, or a citation-derived origin. */
  confidence: number | null;
  runtimeConfirmed: boolean | null;
  /** True when A5 forced this path into `auto_patch_scope` regardless of its
   * own propagation-confidence threshold — the reason a file can legitimately
   * appear in both `autoPatchScope` and `humanReviewRequired`. */
  pinned: boolean | null;
}

/** One file A5's traversal reached. */
export interface BlastScopeFile {
  path: string;
  hopCount: number | null;
  /** Every direction this file was actually reached from — a file reached
   * both ways is a real, distinct fact from being reached one way. */
  directions: BlastDirection[];
  /** The file this one was reached through. `null` for the origin (hop 0). */
  reachedVia: string | null;
  /** `null` exactly when `reachedVia` is `null`. */
  edgeBasis: EdgeBasis | null;
  propagationConfidence: number | null;
  /** NOT "risk" — see the module doc's caveat. */
  priorityScore: number | null;
  origin: string;
  /** A1's role for this file. `null` when the SIG could not be joined. */
  role: string | null;
  criticality: number | null;
  churnWeight: number | null;
  autoPatchable: boolean;
  humanReviewRequired: boolean;
  /** Whether A3 independently flagged this file — real correlation, not proof. */
  hasStaticFinding: boolean;
}

/** One real traversal step A5 recorded. */
export interface BlastEdge {
  from: string;
  to: string;
  direction: BlastDirection;
  basis: EdgeBasis;
  hopCount: number;
}

export interface BlastCapability {
  name: string;
  slug: string;
  /** `null` for the synthetic "Unclassified" bucket. */
  confidence: number | null;
  filesInScope: string[];
  /** `null` for "Unclassified" — there is no capability to size. */
  totalFilesInCapability: number | null;
  why: string;
}

/** `GET /api/runs/{runId}/blast`. */
export interface BlastImpact {
  /** `null` when A5 ran but resolved no origin — no SIG, or no citations. */
  origin: BlastOrigin | null;
  origins: string[];
  scope: BlastScopeFile[];
  edges: BlastEdge[];
  maxHop: number;
  autoPatchScope: string[];
  humanReviewRequired: string[];
  /** Files in both lists at once — real and expected, not a bug. */
  patchAuthorityOverlap: string[];
  riskMeasurementCaveat: string;
  /** `null` when A0.5 has not produced an index for this run — distinct from
   * an empty array, which would mean "ran and found zero capabilities". */
  capabilities: BlastCapability[] | null;
}
