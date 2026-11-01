"""A10 PR routing helpers and decision logic."""

from __future__ import annotations

from backend.models.pr import AxisScores, GateCheck
from backend.orchestrator.trust_gating import full_suite_review_note, reproduction_draft_reason
from backend.services.measurement import Score, below_threshold, meets_threshold
from backend.state.schema import RunStateModel


def axis_pairs(axis: AxisScores) -> list[tuple[str, Score]]:
    """The four axes as `(name, score)`, in the order they are reported."""
    return [
        ("correctness", axis.correctness),
        ("security", axis.security),
        ("fidelity", axis.fidelity),
        ("scope_risk", axis.scope_risk),
    ]

SCORE_THRESHOLD = 80.0
SECURITY_TECHNICAL_THRESHOLD = 90.0
CITATION_REVIEW_FIDELITY = 70.0
CITATION_REVIEW_NOTE = (
    "Citation verification incomplete after maximum reinvestigations. "
    "Manual citation review recommended before merge."
)
DIAGNOSIS_REVIEW_NOTE = (
    "Root-cause claims lack independent support. "
    "Manual diagnosis review required before merge."
)


def citation_review_needed(state: RunStateModel) -> bool:
    root = state.root_cause or {}
    return bool(state.reinvestigation_exhausted or root.get("evidence_incomplete"))


def diagnosis_review_needed(state: RunStateModel) -> bool:
    root = state.root_cause or {}
    citations = root.get("citations") or []
    return bool(citations) and (
        root.get("diagnosis_status") != "supported"
        or any(c.get("claim_support_status") != "supported" for c in citations)
    )


def technical_validation_passed(
    state: RunStateModel,
    mutation: dict,
    security: dict,
    phantoms: set[str],
) -> bool:
    repro = state.reproduction or {}
    if repro.get("status") != "CONFIRMED":
        return False
    if state.validation_exhausted:
        return False
    if mutation.get("patch_retry_required"):
        return False
    if mutation.get("target_test_passed") is not True:
        return False
    if mutation.get("regression_tests_passed") is not True:
        return False
    if mutation.get("pytest_passed") is not True:
        return False
    if security.get("rejected"):
        return False

    # `meets_threshold` is false for an unmeasured score, which is the correct
    # answer here: a gate exists to require evidence, and absent evidence cannot
    # satisfy it. The previous `or 0.0` reached the same verdict by accident,
    # via a fabricated zero that then leaked into the user-facing scores.
    if not meets_threshold(security.get("security_score"), SECURITY_TECHNICAL_THRESHOLD):
        return False
    if not meets_threshold(mutation.get("correctness_score"), SCORE_THRESHOLD):
        return False
    if phantoms:
        return False
    return True


def reproduction_gate_failed(state: RunStateModel) -> tuple[bool, str | None]:
    """Whether reproduction blocks a merge, and the note explaining it.

    The four status sentences used to be written out here as well as in
    `trust_gating`, so the note A10 attached to the PR and the reason the gate
    recorded were two independent copies of the same wording. They are one
    string now, authored where the decision is made.
    """
    if not state.force_draft_pr:
        return False, None

    reason = reproduction_draft_reason(state.reproduction or {})
    if reason is None:
        return False, None
    return True, reason.detail


def hard_draft_reason(
    state: RunStateModel,
    axis: AxisScores,
    phantoms: set[str],
) -> tuple[bool, str | None]:
    mutation = state.mutation_result or {}
    security = state.security_result or {}

    if state.validation_exhausted:
        return True, "Validation retries exhausted. Manual verification required before merge."

    if mutation.get("patch_retry_required"):
        return True, "Target test validation failed after patch. Manual verification required before merge."

    if mutation.get("target_test_passed") is False:
        return True, "Target reproduced test still failing after patch. Manual verification required before merge."

    if mutation.get("regression_tests_passed") is False:
        return True, "Patch introduced new test regressions. Manual verification required before merge."

    if security.get("rejected"):
        return True, "Security re-scan rejected the patch. Manual verification required before merge."

    if phantoms:
        return True, (
            "Phantom changes detected between PR description and diff. "
            "Manual verification required before merge."
        )

    # Measured-and-low is an accusation and reads as one. Unmeasured is handled
    # below, where the note says what actually happened instead of naming a
    # score the pipeline never computed.
    if below_threshold(axis.correctness, SCORE_THRESHOLD):
        return True, f"Low axis scores: correctness={axis.correctness:.0f}. Manual review required."

    if below_threshold(axis.security, SCORE_THRESHOLD):
        return True, f"Low axis scores: security={axis.security:.0f}. Manual review required."

    unmeasured = [name for name, value in axis_pairs(axis) if value is None]
    if unmeasured:
        return True, (
            f"Not measured: {', '.join(unmeasured)}. "
            "The pipeline did not produce these scores, so merge readiness could not be "
            "established. Manual verification required before merge."
        )

    repro_failed, repro_note = reproduction_gate_failed(state)
    if repro_failed:
        return True, repro_note

    return False, None


def gate_checks(
    state: RunStateModel,
    axis: AxisScores,
    phantoms: set[str],
) -> list[GateCheck]:
    """The same ten hard gates `hard_draft_reason` checks, as a full trace.

    `hard_draft_reason` returns only the *first* gate that fires, because
    that is all routing needs — the decision is already made once the first
    `True` is found. A UI explaining *why* needs every gate up to that point,
    and needs to say that nothing after it was evaluated at all, rather than
    implying those later gates passed. This function changes no routing
    behaviour: it duplicates the same conditions, in the same order, purely
    to observe them. `test_a10_gate_trace.py` asserts textual parity between
    this function's firing gate and `hard_draft_reason`'s own returned note,
    so the two cannot silently diverge.
    """
    mutation = state.mutation_result or {}
    security = state.security_result or {}

    repro_failed, repro_note = reproduction_gate_failed(state)
    unmeasured = [name for name, value in axis_pairs(axis) if value is None]

    definitions: list[tuple[str, str, bool, str]] = [
        (
            "validation_exhausted",
            "Validation not exhausted",
            bool(state.validation_exhausted),
            "Validation retries exhausted. Manual verification required before merge.",
        ),
        (
            "patch_retry_required",
            "Target test did not require another patch attempt",
            bool(mutation.get("patch_retry_required")),
            "Target test validation failed after patch. Manual verification required before merge.",
        ),
        (
            "target_test_failed",
            "Target reproduced test passes after patch",
            mutation.get("target_test_passed") is False,
            "Target reproduced test still failing after patch. Manual verification required before merge.",
        ),
        (
            "regression_failed",
            "No new test regressions",
            mutation.get("regression_tests_passed") is False,
            "Patch introduced new test regressions. Manual verification required before merge.",
        ),
        (
            "security_rejected",
            "Security re-scan accepted the patch",
            bool(security.get("rejected")),
            "Security re-scan rejected the patch. Manual verification required before merge.",
        ),
        (
            "phantoms_detected",
            "PR description matches the diff",
            bool(phantoms),
            "Phantom changes detected between PR description and diff. "
            "Manual verification required before merge.",
        ),
        (
            "correctness_low",
            f"Correctness ≥ {SCORE_THRESHOLD:.0f}",
            below_threshold(axis.correctness, SCORE_THRESHOLD),
            f"Low axis scores: correctness={axis.correctness:.0f}. Manual review required."
            if below_threshold(axis.correctness, SCORE_THRESHOLD)
            else None,
        ),
        (
            "security_low",
            f"Security ≥ {SCORE_THRESHOLD:.0f}",
            below_threshold(axis.security, SCORE_THRESHOLD),
            f"Low axis scores: security={axis.security:.0f}. Manual review required."
            if below_threshold(axis.security, SCORE_THRESHOLD)
            else None,
        ),
        (
            "axes_measured",
            "All four axes measured",
            bool(unmeasured),
            (
                f"Not measured: {', '.join(unmeasured)}. "
                "The pipeline did not produce these scores, so merge readiness could not be "
                "established. Manual verification required before merge."
            )
            if unmeasured
            else None,
        ),
        (
            "reproduction_confirmed",
            "Bug reproduction confirmed",
            repro_failed,
            repro_note,
        ),
    ]

    checks: list[GateCheck] = []
    fired = False
    for code, label, triggers_draft, detail in definitions:
        if fired:
            checks.append(GateCheck(code=code, label=label, checked=False))
            continue
        checks.append(
            GateCheck(
                code=code,
                label=label,
                checked=True,
                passed=not triggers_draft,
                detail=detail if triggers_draft else None,
            )
        )
        if triggers_draft:
            fired = True
    return checks


def route_pr_decision(
    state: RunStateModel,
    axis: AxisScores,
    phantoms: set[str],
) -> tuple[str, str | None]:
    mutation = state.mutation_result or {}
    security = state.security_result or {}

    hard, note = hard_draft_reason(state, axis, phantoms)
    if hard:
        return "draft", note

    if technical_validation_passed(state, mutation, security, phantoms):
        if diagnosis_review_needed(state):
            return "diff_only", DIAGNOSIS_REVIEW_NOTE
        if citation_review_needed(state):
            return "diff_only", CITATION_REVIEW_NOTE
        if state.reproduction_confidence == "full_suite":
            return "diff_only", full_suite_review_note()
        return "auto_mergeable", None

    failing = [
        f"{name}={value:.0f}"
        for name, value in axis_pairs(axis)
        if below_threshold(value, SCORE_THRESHOLD)
    ]
    absent = [name for name, value in axis_pairs(axis) if value is None]

    if failing:
        return "draft", f"Low axis scores: {', '.join(failing)}. Manual review required."
    if absent:
        return "draft", (
            f"Not measured: {', '.join(absent)}. Manual review required."
        )

    if diagnosis_review_needed(state):
        return "diff_only", DIAGNOSIS_REVIEW_NOTE
    return "diff_only", full_suite_review_note()


def compute_fidelity_score(fidelity_ok: bool, state: RunStateModel) -> float:
    fidelity = 100.0 if fidelity_ok else 50.0
    if citation_review_needed(state) or diagnosis_review_needed(state):
        fidelity = min(fidelity, CITATION_REVIEW_FIDELITY)
    return fidelity


def compute_scope_risk(blast: dict, state: RunStateModel) -> float:
    human = len(blast.get("human_review_required", []))
    if human == 0:
        return 90.0
    return max(20.0, 90.0 - human * 15)
