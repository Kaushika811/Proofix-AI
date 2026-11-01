from typing import Literal

from pydantic import BaseModel

from backend.services.measurement import Score, measured_mean


class AxisScores(BaseModel):
    """The four merge axes.

    **`None` means the axis was never measured, and is not the same as `0.0`.**
    These previously defaulted to `0.0`, so an axis whose agent never ran was
    indistinguishable from one that ran and scored the worst possible result —
    and the fabricated zero was then averaged into the trust score and printed
    to the user as "security=0". A measured zero still means zero; an absent
    measurement now says so.
    """

    correctness: Score = None
    security: Score = None
    fidelity: Score = None
    scope_risk: Score = None

    def as_list(self) -> list[Score]:
        return [self.correctness, self.security, self.fidelity, self.scope_risk]

    @property
    def trust(self) -> Score:
        """Mean of the axes that were actually measured, or `None`.

        Never a fixed denominator: dividing by four when two axes were measured
        reported a run at half its true score.
        """
        return measured_mean(self.as_list())


class PRRoutingDecision(BaseModel):
    pr_type: Literal["auto_mergeable", "diff_only", "draft"] = "draft"
    axis_scores: AxisScores = AxisScores()
    pr_url: str | None = None
    description_why: str = ""
    description_what: str = ""
    review_note: str | None = None
    phantom_changes_detected: bool = False
    publication: dict | None = None


class PublicationResult(BaseModel):
    status: Literal["ready", "publishing", "published", "failed", "disabled", "dry_run"]
    prepared_head_sha: str | None = None
    repository: str | None = None
    draft: bool | None = None
    tested_patch_sha: str | None = None
    published_head_sha: str | None = None
    proof_artifact_hash: str | None = None
    pr_url: str | None = None
    reason: str | None = None


class GateCheck(BaseModel):
    """One hard gate from `a10_routing.hard_draft_reason`'s short-circuit chain.

    Read-only and explanatory — `a10_routing.gate_checks` re-evaluates the same
    conditions in the same order for display, but the routing decision itself
    is still made exactly once, by `hard_draft_reason`. `checked=False` means
    an earlier gate already fired and this one was never reached, which is a
    different fact from "passed" and must not be drawn as one.
    """

    code: str
    label: str
    checked: bool
    #: `None` when `checked` is `False` — a gate that was never reached has
    #: neither passed nor failed.
    passed: bool | None = None
    detail: str | None = None
