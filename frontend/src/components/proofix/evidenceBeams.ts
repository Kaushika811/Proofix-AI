/**
 * Provenance of A4's confidence terms — the model behind the beam field in
 * `EvidenceInvestigationBoard`.
 *
 * A4 publishes its confidence as a named sum
 * (`root_cause_builder.compute_confidence_breakdown`), and the board draws one
 * beam per term. What the backend does *not* publish is whether a term was
 * earned against something it could check, and that is the whole question the
 * VERIFIED ONLY view answers. This module is the one place that judgement is
 * made, so it can be read and tested without a DOM.
 *
 * Nothing here recomputes A4's confidence. `subtotal` sums published points and
 * caps exactly where the backend caps; the board labels the result a subtotal,
 * never a confidence, because only A4 sets that number.
 */
import type { ConfidenceComponent } from "./investigationTypes";

/**
 * Whether a confidence term was earned against something A4 could check.
 *
 * * `verified` — an execution or an anchor. A3.5 ran the suite and observed
 *   the failure; the citation verifier resolved a claim to real source. These
 *   are the terms that survive the VERIFIED ONLY view.
 * * `asserted` — a real tool said something real, but nothing anchored it to
 *   the cited location. A scanner finding, a reachable advisory and the mere
 *   presence of a traceback are all in this class. Asserted is not *wrong* and
 *   is never drawn as failure — it is simply unproven, which is the whole
 *   distinction the toggle exists to show.
 * * `unclassified` — a term this build does not recognise, because the backend
 *   grew one. It survives both views: dropping it would understate a run on
 *   the strength of the frontend being out of date.
 */
export type Provenance = "verified" | "asserted" | "unclassified";

/**
 * Keyed on the component names minted in
 * `root_cause_builder.compute_confidence_breakdown`. `source diversity` is
 * absent deliberately — it is conditional, and `classifyTerms` decides it.
 */
const TERM_PROVENANCE: Record<string, Provenance> = {
  "runtime evidence": "verified",
  "runtime confirmation": "verified",
  "verified citations": "verified",
  "stack trace evidence": "asserted",
  "finding evidence": "asserted",
  "cve evidence": "asserted",
};

/** The bonus term, which is withdrawn or kept as a consequence of the others. */
export const DIVERSITY_TERM = "source diversity";

/** How many distinct sources the backend requires before it pays the bonus. */
const DIVERSITY_MINIMUM = 3;

export interface Beam extends ConfidenceComponent {
  provenance: Provenance;
  /** Why this beam retracts under VERIFIED ONLY. `null` when it survives. */
  withdrawnBecause: string | null;
}

/**
 * Classify every published term, and resolve the diversity bonus.
 *
 * The bonus is paid for `>= DIVERSITY_MINIMUM` independent evidence sources
 * agreeing. Strip the asserted sources and that count can fall below the
 * threshold, at which point the backend would not have paid it either — so the
 * bonus is withdrawn with the sources that earned it, rather than left behind
 * as free points. Every other term keeps its own provenance.
 */
export function classifyTerms(breakdown: ConfidenceComponent[]): Beam[] {
  const sourceTerms = breakdown.filter((c) => c.component.endsWith("evidence"));
  const verifiedSources = sourceTerms.filter(
    (c) => TERM_PROVENANCE[c.component] === "verified",
  ).length;

  return breakdown.map((component) => {
    if (component.component === DIVERSITY_TERM) {
      const paid = verifiedSources >= DIVERSITY_MINIMUM;
      return {
        ...component,
        provenance: paid ? "verified" : "asserted",
        withdrawnBecause: paid
          ? null
          : `${verifiedSources} verified source${verifiedSources === 1 ? "" : "s"} remain, ` +
            `below the ${DIVERSITY_MINIMUM} this bonus is paid for`,
      };
    }
    const provenance = TERM_PROVENANCE[component.component] ?? "unclassified";
    return {
      ...component,
      provenance,
      withdrawnBecause: provenance === "asserted" ? "not anchored to the cited location" : null,
    };
  });
}

/** Terms that survive VERIFIED ONLY: everything A4 checked, plus the unknown. */
export const survives = (beam: Beam) => beam.provenance !== "asserted";

/** The backend caps its own sum at 1.0; the subtotal has to cap identically. */
export function subtotal(beams: Beam[]): number {
  return Math.min(
    1,
    beams.reduce((sum, b) => sum + (Number.isFinite(b.points) ? b.points : 0), 0),
  );
}
