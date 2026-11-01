/**
 * Types for A2's dependency/CVE reachability endpoint
 * (`GET /api/runs/{runId}/dependency-risk`, `services/ui_projection.py::build_dependency_risk`).
 *
 * This is A2's own artifact, distinct from A1's semantic roles
 * (`semanticGraphTypes.ts`) and A0.5's structural graph
 * (`knowledgeGraphTypes.ts`): A2 answers "what does this repository depend
 * on, and is any of that reachable" — OSV advisories narrowed by A1's import
 * graph. `classification` is A2's own reachability verdict, not a CVSS
 * severity band — `severity` is the free-form string OSV reported (sometimes
 * a CVSS score, sometimes the literal word "HIGH") and is never rebucketed
 * client-side.
 */

/** A2's reachability verdict for one advisory. Never a CVSS severity band. */
export type DependencyClassification = "Critical" | "Informational" | "Unknown";

/** One advisory A2 matched against a declared dependency. */
export interface DependencyFinding {
  package: string;
  cveId: string;
  /** Raw OSV string — a CVSS score or the literal word "HIGH". Never rebucketed. */
  severity: string;
  /** The version A2 queried OSV with, or `null` if the manifest omitted one. */
  installedVersion: string | null;
  /** A2 does not resolve which symbol is affected — always `null` today. */
  affectedSymbol: string | null;
  /** `true`/`false` once A1's SIG was available, `null` if reachability was never determined. */
  reachable: boolean | null;
  /** Production files that import this package, when `reachable` is `true`. `null` otherwise. */
  reachPath: string[] | null;
  classification: DependencyClassification;
}

/** `GET /api/runs/{runId}/dependency-risk`. */
export interface DependencyRiskReport {
  /** The manifest A2 parsed (e.g. "requirements.txt"), or `null` if none was found. */
  manifest: string | null;
  ecosystem: string | null;
  /** Every package A2 parsed from the manifest — not only the ones with advisories. */
  totalDependencies: number;
  advisoryCount: number;
  reachableCount: number;
  informationalCount: number;
  unknownCount: number;
  findings: DependencyFinding[];
}
