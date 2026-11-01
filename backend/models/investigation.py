"""A4's evidence investigation — the structured answer to "why do we believe
this finding is real".

Distinct from `RootCauseBrief` (`models/root_cause.py`), which is A4's *claim*:
a summary, a root cause and the citations backing it. This model is the
*audit* of that claim — every upstream source A4 could consult, whether it had
anything to say, and whether what it said supports or contradicts the claim.

Three rules govern every field here, and the tests enforce them:

1. **Unmeasured is `None`, never a default.** A severity nobody assigned, a
   confidence with no evidence behind it and a reproduction that never ran are
   all absent values. `0.0` and `"LOW"` are measurements, and writing one where
   nothing was measured is the failure mode the whole workspace exists to
   prevent.
2. **Absence of evidence is not evidence of absence.** A scanner that could not
   run contributes an `unavailable` item, not a `contradicting` one. A3.5's
   full-suite gate does not target a specific A3 finding
   (`orchestrator/nodes.py::reproduction_gate` never reads `static_report`), so
   a suite that passed never contradicts a static finding.
3. **Every stance is earned.** `supporting` and `contradicting` are only set
   where A4 has a concrete reason; everything else is `neutral`.
"""

from typing import Literal

from pydantic import BaseModel, Field

#: Which upstream produced an evidence item. One per real backend source —
#: there is no category here that no agent populates.
EvidenceCategory = Literal["scanner", "reproduction", "source", "dependency"]

#: What A4 found when it consulted the source.
#:
#: * ``present``      — the source ran and produced something about this subject
#: * ``absent``       — the source ran and had nothing to say (a real result)
#: * ``unavailable``  — the source never ran, so it has no opinion either way
#: * ``error``        — the source ran and failed
EvidenceStatus = Literal["present", "absent", "unavailable", "error"]

#: Whether the item argues for the finding being real, against it, or neither.
EvidenceStance = Literal["supporting", "contradicting", "neutral"]

#: What A4 investigated. A3.5 confirming a runtime failure makes that failure
#: the subject; otherwise A4 investigates A3's highest-ranked finding. When
#: neither exists there is no subject and the report says so rather than
#: inventing one.
SubjectKind = Literal["runtime_failure", "static_finding"]

#: ``complete``   — a subject, a root cause, and every source either answered or
#:                  is honestly reported as unavailable. This measures coverage,
#:                  not whether the diagnosis is true; see diagnosis_status.
#: ``partial``    — a subject and a root cause, but one or more sources could
#:                  not be consulted
#: ``no_finding`` — nothing to investigate: neither A3 nor A3.5 produced a subject
#: ``error``      — A4 recorded a failure while investigating
InvestigationStatus = Literal["complete", "partial", "no_finding", "error"]

#: Where the root-cause text came from. The deterministic builder and the LLM
#: are genuinely different provenance and the UI is entitled to say which ran.
RootCauseSource = Literal["llm", "deterministic"]


class EvidenceItem(BaseModel):
    """One thing A4 learned (or failed to learn) from one upstream source."""

    id: str
    category: EvidenceCategory
    #: The concrete producer: a scanner name, ``pytest``, a ``file:line``, an
    #: advisory ID. Never a category label dressed up as a source.
    source: str
    description: str
    status: EvidenceStatus
    stance: EvidenceStance = "neutral"
    #: 0..1, and only when the underlying source actually measured something —
    #: `None` for every scanner that assigns a constant severity, for a
    #: citation with no weight behind it, and for anything unavailable.
    strength: float | None = None
    #: How `strength` was arrived at, so a number on screen can be traced.
    #: `None` exactly when `strength` is `None`.
    strength_basis: str | None = None
    #: Real metadata from the source — exit codes, tool lists, line numbers.
    #: Only measured values; never padded to a fixed shape.
    detail: dict = Field(default_factory=dict)


class ConfidenceComponent(BaseModel):
    """One term of the confidence sum, named and attributed."""

    component: str
    points: float
    basis: str


class UnavailableSource(BaseModel):
    """A source A4 wanted and did not get, with the reason it was missing."""

    source: str
    reason: str


class EvidenceCompleteness(BaseModel):
    """How much of the available evidence surface A4 actually reached.

    `measured_categories / total_categories` — a coverage ratio, deliberately
    not a quality score. Four categories exist; a run where three answered is
    0.75 covered regardless of what they said.
    """

    measured_categories: int = 0
    total_categories: int = 0
    ratio: float | None = None
    category_status: dict[str, EvidenceStatus] = Field(default_factory=dict)


class InvestigationReport(BaseModel):
    schema_version: str = "1.0"
    status: InvestigationStatus = "no_finding"

    # ---- subject identity (all `None` when there is nothing to investigate)
    subject_kind: SubjectKind | None = None
    finding_id: str | None = None
    title: str | None = None
    file: str | None = None
    line: int | None = None
    #: A3's 0..1 severity, and only when a tool genuinely assigned one —
    #: `severity_measured` is A3's own flag, carried through unchanged.
    severity: float | None = None
    severity_measured: bool = False

    #: A3.5's outcome in the UI's vocabulary, or `None` when A3.5 never ran.
    reproduction_status: (
        Literal["reproduced", "not_reproduced", "unavailable", "error"] | None
    ) = None

    evidence: list[EvidenceItem] = Field(default_factory=list)

    root_cause: str | None = None
    summary: str | None = None
    root_cause_source: RootCauseSource | None = None
    diagnosis_status: Literal["supported", "contradicted", "incomplete", "unavailable"] = "incomplete"

    #: A4's own evidence-weighted confidence (`root_cause_builder`), never a
    #: constant. `None` when no evidence was available to score.
    confidence: float | None = None
    confidence_breakdown: list[ConfidenceComponent] = Field(default_factory=list)

    completeness: EvidenceCompleteness = Field(default_factory=EvidenceCompleteness)
    unavailable_sources: list[UnavailableSource] = Field(default_factory=list)
    #: Failures A4 itself hit — an LLM outage that forced the deterministic
    #: path, for instance. Present here rather than only in `state.errors` so a
    #: client sees the degradation that produced the report it is reading.
    errors: list[str] = Field(default_factory=list)

    @property
    def supporting(self) -> list[EvidenceItem]:
        return [e for e in self.evidence if e.stance == "supporting"]

    @property
    def contradicting(self) -> list[EvidenceItem]:
        return [e for e in self.evidence if e.stance == "contradicting"]
