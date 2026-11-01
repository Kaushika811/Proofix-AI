"""Trust gates applied before PR routing.

**This module is the only writer of `force_draft_pr`.**

It used to be three: A3.5 set it when reproduction failed, A4 set it when
citations stayed unverified, and this module set it for exhausted validation.
Answering "why is this a draft?" meant reading three files and knowing which
ran, and nothing guaranteed they agreed — a flag written from three places has
no single moment at which it is true.

Both agent writes were derivable from state those agents already published:
A3.5's from `reproduction.status`, A4's from `root_cause.evidence_incomplete`.
So the flag is now *computed* from that evidence, once, immediately before
routing. The agents record what they observed; this module decides what it
means. That split is the same one the rest of the pipeline follows — agents
produce evidence, gates make decisions — and it makes the reasons enumerable,
which is what puts them on screen.
"""

from dataclasses import dataclass

from backend.config import get_settings
from backend.models.proof import ReproductionConfidence
from backend.state.schema import RunStateModel

MAX_REINVESTIGATIONS = 2


@dataclass(frozen=True)
class DraftReason:
    """One reason a run cannot be auto-merged.

    `code` is stable and machine-readable; `detail` is the sentence a user
    reads, authored here rather than composed by a client. A UI that phrases
    its own explanation is a second source of truth for the decision.
    """

    code: str
    detail: str


def reproduction_draft_reason(reproduction: dict) -> DraftReason | None:
    """A run that could not reproduce its bug has nothing to prove a fix against."""
    status = reproduction.get("status") or ""
    if not status or status == "CONFIRMED":
        return None

    if status == "INFRA_ERROR":
        detail = reproduction.get("infra_detail") or "pytest infrastructure failure"
        return DraftReason(
            "reproduction_infra_error",
            f"A3.5 Reproduction Gate: infrastructure error during test run ({detail}). "
            "Manual verification required before merge.",
        )
    if status == "NO_TESTS":
        return DraftReason(
            "reproduction_no_tests",
            "A3.5 Reproduction Gate: no tests available to confirm the vulnerability. "
            "Manual verification required before merge.",
        )
    return DraftReason(
        "reproduction_unconfirmed",
        "A3.5 Reproduction Gate: bug could not be reproduced in test environment. "
        "Manual verification required before merge.",
    )


def draft_reasons(model: RunStateModel, max_retries: int | None = None) -> list[DraftReason]:
    """Every reason this run must be a draft, in the order they are reported.

    Derived, not read from flags: the same inputs always produce the same
    answer, so the reasons on screen and the flag that routes the PR cannot
    disagree.
    """
    if max_retries is None:
        max_retries = get_settings().max_retries

    reasons: list[DraftReason] = []

    mutation = model.mutation_result or {}
    security = model.security_result or {}
    if model.retry_count >= max_retries and (
        mutation_validation_failed(mutation) or security_validation_failed(security)
    ):
        reasons.append(
            DraftReason(
                "validation_exhausted",
                "Validation retries exhausted without a patch that passed. "
                "Manual verification required before merge.",
            )
        )

    root = model.root_cause or {}
    if root.get("evidence_incomplete") or model.reinvestigation_exhausted:
        reasons.append(
            DraftReason(
                "citations_unverified",
                "Citation verification incomplete after the maximum reinvestigations. "
                "Manual citation review recommended before merge.",
            )
        )

    if root.get("citations") and (root.get("diagnosis_status") != "supported" or not all(
        citation.get("claim_support_status") == "supported"
        for citation in root["citations"]
    )):
        reasons.append(DraftReason(
            "diagnosis_unverified",
            "Source locations do not establish that the root-cause explanation is true. "
            "Manual diagnosis review required before merge.",
        ))

    repro_reason = reproduction_draft_reason(model.reproduction or {})
    if repro_reason:
        reasons.append(repro_reason)

    return reasons


def derive_reproduction_confidence(reproduction: dict) -> ReproductionConfidence:
    if reproduction.get("reexecution_is_targeted"):
        return "exact_test"
    return "full_suite"


def full_suite_review_note() -> str:
    return (
        "Lower-confidence full-suite reproduction proof. "
        "Manual review required — not eligible for auto-merge."
    )


def mutation_validation_failed(mutation: dict) -> bool:
    if not mutation:
        return False
    return not mutation.get("pytest_passed") or bool(mutation.get("mutant_survived"))


def security_validation_failed(security: dict) -> bool:
    if not security:
        return False
    return bool(security.get("rejected"))


def apply_trust_gates_before_pr(
    model: RunStateModel,
    max_retries: int | None = None,
) -> RunStateModel:
    """Set exhaustion flags and `force_draft_pr` before A10 routes the PR.

    The single write of `force_draft_pr` in the codebase. It is assigned, not
    or-ed in: the reasons are computed from evidence, so a stale `True` from an
    earlier pass must not survive a re-evaluation that no longer finds a reason.
    """
    if max_retries is None:
        max_retries = get_settings().max_retries

    model.reproduction_confidence = derive_reproduction_confidence(model.reproduction or {})

    reasons = draft_reasons(model, max_retries)
    codes = {r.code for r in reasons}

    # The exhaustion flags stay: they are read elsewhere and carry a narrower
    # meaning than "this is a draft". They are now derived from the same
    # computation rather than set independently beside it.
    if "validation_exhausted" in codes:
        model.validation_exhausted = True
    if "citations_unverified" in codes:
        model.reinvestigation_exhausted = True

    model.force_draft_pr = bool(reasons)
    return model


def trust_gates_block_auto_merge(model: RunStateModel) -> bool:
    return bool(
        model.force_draft_pr
        or model.validation_exhausted
        or model.reinvestigation_exhausted
        or model.reproduction_confidence == "full_suite"
    )
