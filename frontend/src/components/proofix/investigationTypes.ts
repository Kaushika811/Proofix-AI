/**
 * Types for A4's evidence investigation
 * (`GET /api/runs/{runId}/investigation`,
 * `services/ui_projection.py::build_investigation`).
 *
 * Every field mirrors `InvestigationReport` (`models/investigation.py`)
 * field-for-field. Nothing here is computed in the browser: which evidence
 * supports the finding, which contradicts it, and how the confidence was
 * reached are all A4's judgements, made against real upstream artifacts.
 *
 * Fields the backend could not measure are `null`, never defaulted. In
 * particular `severity` is `null` for a reproduced runtime failure — no tool
 * assigns a severity to observed behaviour — and `confidence` is `null` when
 * there was no evidence to score, which is not the same as 0%.
 */

/** Which upstream produced an evidence item. One per real backend source. */
export type EvidenceCategory = "scanner" | "reproduction" | "source" | "dependency";

/**
 * What A4 found when it consulted the source.
 *
 * `absent` (ran, nothing to say) and `unavailable` (never ran) are
 * deliberately distinct: the second is not a result.
 */
export type EvidenceStatus = "present" | "absent" | "unavailable" | "error";

/** Only set where A4 had a concrete reason; everything else is `neutral`. */
export type EvidenceStance = "supporting" | "contradicting" | "neutral";

/** What A4 investigated — a reproduced failure, or A3's top-ranked finding. */
export type SubjectKind = "runtime_failure" | "static_finding";

export type InvestigationStatus = "complete" | "partial" | "no_finding" | "error";

/** Whether the LLM or the deterministic builder produced the root cause. */
export type RootCauseSource = "llm" | "deterministic";

export interface EvidenceItem {
  id: string;
  category: EvidenceCategory;
  /** The concrete producer: a scanner name, `pytest`, a `file:line`, an advisory ID. */
  source: string;
  description: string;
  status: EvidenceStatus;
  stance: EvidenceStance;
  /** 0..1, and only where the source actually measured something. */
  strength: number | null;
  /** How `strength` was arrived at. `null` exactly when `strength` is `null`. */
  strengthBasis: string | null;
  /** Real metadata from the source — never padded to a fixed shape. */
  detail: Record<string, unknown>;
}

/** One named term of the confidence sum. */
export interface ConfidenceComponent {
  component: string;
  points: number;
  basis: string;
}

export interface UnavailableSource {
  source: string;
  reason: string;
}

/** Coverage of the four evidence categories — deliberately not a quality score. */
export interface EvidenceCompleteness {
  measuredCategories: number | null;
  totalCategories: number | null;
  ratio: number | null;
  categoryStatus: Partial<Record<EvidenceCategory, EvidenceStatus>>;
}

/** `GET /api/runs/{runId}/investigation`. */
export interface InvestigationReport {
  status: InvestigationStatus;

  subjectKind: SubjectKind | null;
  findingId: string | null;
  title: string | null;
  file: string | null;
  line: number | null;
  /** A3's 0..1 severity, and only when a tool genuinely assigned one. */
  severity: number | null;
  severityMeasured: boolean;

  reproductionStatus: "reproduced" | "not_reproduced" | "unavailable" | "error" | null;

  evidence: EvidenceItem[];

  rootCause: string | null;
  summary: string | null;
  rootCauseSource: RootCauseSource | null;
  /** Independent claim check; absent for reports created before this field existed. */
  diagnosisStatus?: "supported" | "contradicted" | "incomplete" | "unavailable";

  /** A4's evidence-weighted confidence. `null` when nothing was scored. */
  confidence: number | null;
  confidenceBreakdown: ConfidenceComponent[];

  completeness: EvidenceCompleteness;
  unavailableSources: UnavailableSource[];
  /** Failures A4 itself hit — an LLM outage that forced the deterministic path. */
  errors: string[];
}
