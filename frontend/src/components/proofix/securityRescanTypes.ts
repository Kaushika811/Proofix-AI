/**
 * Types for A9's security re-scan endpoint
 * (`GET /api/runs/{runId}/security`, `services/ui_projection.py::build_security_rescan`).
 *
 * A9 is not a scan — it is the reconciliation of two scans. A3 produced a
 * pre-patch baseline; A9 re-scans the patched repository and asks only which
 * post-patch findings have no counterpart in that baseline. It compares
 * *counts* per `(tool, file, message)` key and deliberately ignores line
 * numbers, because a patch that inserts a line shifts every finding below it
 * and a shift is not an introduction.
 *
 * `reconciliation` is that comparison, addressable: lanes per scanner, keys
 * per finding identity, and `rails` pairing a baseline occurrence with the
 * post-patch occurrence it became. Nothing here is invented:
 *
 * - No severity. A9's scanner calls stamp every finding with the same constant
 *   (0.7), never a per-issue measurement, so no type here carries one and no
 *   chip may be colour-graded, size-graded or sorted by it.
 * - No rule ID / CWE / category. Neither scanner call captures them today.
 * - No `ruff` lane. `A9._keyed` excludes ruff as style, not security, so ruff
 *   is absent from both sides of the comparison and must not appear as a lane.
 */

/** The three real states A9 can report — never a fourth, invented one. */
export type SecurityVerdict = "not_measured" | "new_findings" | "clean";

/** One finding A9 found in the patched repo with no counterpart in A3's baseline. */
export interface SecurityFinding {
  id: string;
  file: string;
  line: number;
  message: string;
  /** Which scanner(s) reported it — e.g. `["bandit"]`. */
  tools: string[];
}

/** One occurrence of a finding key, in one of the two scans. */
export interface ReconciliationOccurrence {
  /** Stable within the payload — `b-…` for baseline, `p-…` for post-patch. */
  id: string;
  line: number | null;
}

/**
 * A baseline occurrence and the post-patch occurrence it became.
 * `lineDelta` is how far the patch moved it — 0 when it did not move.
 */
export interface ReconciliationRail {
  baselineId: string;
  postId: string;
  lineDelta: number;
}

/**
 * One `(tool, file, message)` identity, reconciled.
 *
 * A post occurrence reachable by no rail is introduced; `introducedIds` names
 * them, and the two agree by construction. Multiplicity is the case that makes
 * this necessary: a baseline with one hardcoded password and a post scan with
 * two produces one rail and one dangling chip, which set difference would miss
 * entirely.
 */
export interface ReconciliationKey {
  id: string;
  tool: string;
  file: string;
  message: string;
  baseline: ReconciliationOccurrence[];
  post: ReconciliationOccurrence[];
  rails: ReconciliationRail[];
  introducedIds: string[];
  /** Baseline occurrences with no post-patch counterpart — the patch removed them. */
  resolvedIds: string[];
}

/**
 * One scanner's side of the ledger. `compared: false` means the scanner did
 * not run this rescan: its baseline is excluded from the comparison entirely,
 * so an empty `keys` there means "not compared", never "found nothing".
 */
export interface ReconciliationLane {
  tool: string;
  compared: boolean;
  keys: ReconciliationKey[];
}

/** A9's own retry brief content for a rejected patch — not A10's decision. */
export interface SecurityRetryContext {
  assertionMessage: string | null;
  securityConstraint: string | null;
}

/** `GET /api/runs/{runId}/security`. */
export interface SecurityRescanReport {
  verdict: SecurityVerdict;
  rejected: boolean;
  /** `null` when no scanner executed this rescan — absent is not zero. */
  securityScore: number | null;
  newFindings: SecurityFinding[];
  /** The comparison itself — one lane per known scanner, always both. */
  reconciliation: ReconciliationLane[];
  /**
   * `false` for a run completed before A9 recorded the comparison. The lanes
   * still carry `compared`, but their `keys` are empty because nothing was
   * stored — not because nothing carried. The panel must say so rather than
   * render zeroes.
   */
  reconciliationAvailable: boolean;
  /** Scanners that actually ran this rescan — subset of `["bandit", "semgrep"]`. */
  scannersRun: string[];
  reexecutionCommand: string;
  reexecutionTimeoutSeconds: number | null;
  retryContext: SecurityRetryContext | null;
}
