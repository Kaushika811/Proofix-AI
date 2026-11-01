/**
 * Types for A3's static-analysis findings endpoint
 * (`GET /api/runs/{runId}/static-findings`, `services/ui_projection.py::build_static_findings`).
 *
 * This is A3's own artifact, distinct from A0.5's structural graph, A1's
 * semantic roles, and A2's dependency risk: A3 answers "what did the
 * scanners find in this repository's own source, and why does one finding
 * outrank another". Every field mirrors `StaticAnalysisReport`
 * (`models/findings.py`) field-for-field — nothing here is invented, and
 * fields the backend cannot measure are `null`/absent, never defaulted.
 */

/** Per-scanner outcome A3 itself computed — never derived from which tools appear on findings. */
export type ScannerOutcome = "ok" | "ok_no_findings" | "unavailable" | "stubbed";

/** One ranked finding. Order is exactly the backend's ranking — never re-sorted client-side. */
export interface StaticFinding {
  id: string;
  /** 1-based position in the backend's own `blast_radius_score` ordering. */
  rank: number;
  file: string;
  line: number;
  message: string;
  tools: string[];
  severity: number;
  /**
   * `true` only when every tool contributing the displayed `severity` derived
   * it from a real per-finding measurement (today: bandit alone). `false`
   * means `severity` is a scanner-wide constant (semgrep: 0.7, ruff: 0.4) —
   * the UI must render "Not measured", never a severity band built from it.
   */
  severityMeasured: boolean;
  consensus: boolean;
  blastRadiusScore: number;
  /** The file's criticality factor in `blast_radius_score`, from A1's SIG when available. */
  criticality: number;
  /** The file's churn-weight factor in `blast_radius_score`. */
  churnWeight: number;
}

/** `GET /api/runs/{runId}/static-findings`. */
export interface StaticFindingsReport {
  scannerStatus: Record<string, ScannerOutcome>;
  /** Findings before clustering — always ≥ `prioritizedCount`. */
  rawCount: number;
  /** `prioritized.length` — capped at 8 by the backend. */
  prioritizedCount: number;
  findings: StaticFinding[];
}
