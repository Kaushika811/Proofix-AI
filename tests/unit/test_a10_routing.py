"""Unit tests for A10 PR routing decision logic."""

from backend.agents.a10_routing import (
    CITATION_REVIEW_NOTE,
    DIAGNOSIS_REVIEW_NOTE,
    compute_fidelity_score,
    route_pr_decision,
    technical_validation_passed,
)
from backend.models.pr import AxisScores
from backend.state.schema import RunStateModel


def _validated_state(**overrides) -> RunStateModel:
    base = RunStateModel(
        run_id="run-1",
        repo_path="/tmp/repo",
        reproduction={"status": "CONFIRMED", "reexecution_is_targeted": True},
        reproduction_confidence="exact_test",
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "patch_retry_required": False,
            "correctness_score": 80.0,
        },
        security_result={"rejected": False, "security_score": 100.0},
        root_cause={"summary": "Token expiry not checked"},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _axis(**kwargs) -> AxisScores:
    defaults = {
        "correctness": 80.0,
        "security": 100.0,
        "fidelity": 100.0,
        "scope_risk": 90.0,
    }
    defaults.update(kwargs)
    return AxisScores(**defaults)


def test_all_validation_passes_with_incomplete_citations_routes_diff_only():
    state = _validated_state(
        reinvestigation_exhausted=True,
        force_draft_pr=True,
        root_cause={"summary": "Token expiry not checked", "evidence_incomplete": True},
    )
    pr_type, note = route_pr_decision(state, _axis(fidelity=70.0), set())
    assert pr_type == "diff_only"
    assert note == CITATION_REVIEW_NOTE


def test_unverified_diagnosis_cannot_be_routed_as_auto_mergeable():
    state = _validated_state(root_cause={
        "summary": "Expired session accepted",
        "diagnosis_status": "incomplete",
        "citations": [{"claim": "is_session_active ignores now",
                       "claim_support_status": "incomplete"}],
    })
    assert compute_fidelity_score(True, state) == 70.0

    pr_type, note = route_pr_decision(state, _axis(fidelity=70.0), set())

    assert pr_type == "diff_only"
    assert note == DIAGNOSIS_REVIEW_NOTE


def test_target_validation_failure_routes_draft():
    state = _validated_state(
        mutation_result={
            "pytest_passed": False,
            "target_test_passed": False,
            "regression_tests_passed": True,
            "patch_retry_required": True,
            "correctness_score": 0.0,
        }
    )
    pr_type, note = route_pr_decision(state, _axis(correctness=0.0), set())
    assert pr_type == "draft"
    assert note is not None
    assert "Target test validation failed" in note


def test_security_rejection_routes_draft():
    state = _validated_state(
        security_result={"rejected": True, "security_score": 0.0},
    )
    pr_type, note = route_pr_decision(state, _axis(security=0.0), set())
    assert pr_type == "draft"
    assert "Security re-scan rejected" in (note or "")


def test_phantom_changes_routes_draft():
    state = _validated_state()
    pr_type, note = route_pr_decision(state, _axis(), {"auth.py"})
    assert pr_type == "draft"
    assert "Phantom changes detected" in (note or "")


def test_retry_exhausted_routes_draft():
    state = _validated_state(validation_exhausted=True, retry_count=3)
    pr_type, note = route_pr_decision(state, _axis(), set())
    assert pr_type == "draft"
    assert "Validation retries exhausted" in (note or "")


def test_correctness_below_threshold_routes_draft():
    state = _validated_state(
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "patch_retry_required": False,
            "correctness_score": 40.0,
        }
    )
    pr_type, note = route_pr_decision(state, _axis(correctness=40.0), set())
    assert pr_type == "draft"
    assert "correctness=40" in (note or "")


def test_regression_failure_routes_draft():
    state = _validated_state(
        mutation_result={
            "pytest_passed": False,
            "target_test_passed": True,
            "regression_tests_passed": False,
            "patch_retry_required": False,
            "correctness_score": 80.0,
        }
    )
    pr_type, note = route_pr_decision(state, _axis(), set())
    assert pr_type == "draft"
    assert "new test regressions" in (note or "")


def test_clean_run_without_citation_issues_can_auto_merge():
    state = _validated_state()
    pr_type, note = route_pr_decision(state, _axis(), set())
    assert pr_type == "auto_mergeable"
    assert note is None


def test_technical_validation_passed_requires_confirmed_reproduction():
    state = _validated_state(reproduction={"status": "UNCONFIRMED"})
    assert technical_validation_passed(
        state,
        state.mutation_result or {},
        state.security_result or {},
        set(),
    ) is False


def test_a10_agent_uses_diff_only_for_citation_only_exhaustion():
    state = _validated_state(
        reinvestigation_exhausted=True,
        force_draft_pr=True,
        root_cause={"summary": "Token expiry", "evidence_incomplete": True},
        blast_graph={"human_review_required": []},
        patch_bundle={"diff_text": "--- a/vulnapi/auth.py\n+++ b/vulnapi/auth.py\n", "patches": []},
    )
    pr_type, note = route_pr_decision(
        state,
        AxisScores(correctness=80.0, security=100.0, fidelity=70.0, scope_risk=90.0),
        set(),
    )
    assert pr_type == "diff_only"
    assert "citation review" in (note or "").lower()
