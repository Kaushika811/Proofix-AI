"""Trust-gating tests for validation exhaustion, reinvestigation limits, and GitHub flow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import Settings
from backend.models.pr import AxisScores
from backend.orchestrator.edges import after_mutation, after_security, should_reinvestigate
from backend.orchestrator.trust_gating import (
    MAX_REINVESTIGATIONS,
    apply_trust_gates_before_pr,
    derive_reproduction_confidence,
    trust_gates_block_auto_merge,
)
from backend.services.github_pr import GitHubPRService
from backend.state.schema import RunStateModel


def test_run_state_trust_flags_default_false():
    state = RunStateModel(run_id="r1", repo_path="/tmp/repo")
    assert state.validation_exhausted is False
    assert state.reinvestigation_exhausted is False


def test_after_mutation_retries_then_route_pr():
    state = {"mutation_result": {"pytest_passed": False, "patch_retry_required": True}, "retry_count": 0}
    assert after_mutation(state) == "generate_code"

    state["retry_count"] = 3
    assert after_mutation(state) == "route_pr"


def test_after_mutation_regression_only_routes_to_pr_without_retry():
    state = {
        "mutation_result": {
            "pytest_passed": False,
            "patch_retry_required": False,
            "target_test_passed": True,
            "new_failures": ["tests/test_other.py::test_broken"],
        },
        "retry_count": 0,
    }
    assert after_mutation(state) == "route_pr"


def test_after_mutation_pass_routes_to_security():
    state = {"mutation_result": {"pytest_passed": True, "mutant_survived": False}, "retry_count": 0}
    assert after_mutation(state) == "validate_security"


def test_after_security_retries_then_route_pr():
    state = {"security_result": {"rejected": True}, "retry_count": 1}
    assert after_security(state) == "generate_code"

    state["retry_count"] = 3
    assert after_security(state) == "route_pr"


def test_should_reinvestigate_respects_max_and_exhaustion():
    state = {
        "root_cause": {"reinvestigation_required": True, "reinvestigation_count": 1},
    }
    assert should_reinvestigate(state) == "investigate"

    state["root_cause"]["reinvestigation_count"] = MAX_REINVESTIGATIONS
    assert should_reinvestigate(state) == "investigate"

    state["root_cause"]["reinvestigation_count"] = MAX_REINVESTIGATIONS + 1
    assert should_reinvestigate(state) == "recovery_stop"

    state = {"reinvestigation_exhausted": True, "root_cause": {"reinvestigation_required": True}}
    assert should_reinvestigate(state) == "recovery_stop"

    state = {"root_cause": {"evidence_incomplete": True, "reinvestigation_required": True}}
    assert should_reinvestigate(state) == "recovery_stop"


def test_located_citation_does_not_clear_diagnosis_review_gate():
    from backend.orchestrator.trust_gating import draft_reasons

    state = RunStateModel(
        run_id="unproven-diagnosis",
        root_cause={"citations": [{"file": "calculator.py", "line": 1,
                                  "verified": True, "source_located": True,
                                  "claim_support_status": "unverified"}]},
    )
    assert "diagnosis_unverified" in {reason.code for reason in draft_reasons(state)}

def test_apply_trust_gates_validation_exhausted():
    model = RunStateModel(
        run_id="r1",
        repo_path="/tmp",
        retry_count=3,
        mutation_result={"pytest_passed": False},
    )
    result = apply_trust_gates_before_pr(model, max_retries=3)
    assert result.validation_exhausted is True
    assert result.force_draft_pr is True


def test_apply_trust_gates_reinvestigation_exhausted():
    model = RunStateModel(
        run_id="r1",
        repo_path="/tmp",
        root_cause={"evidence_incomplete": True},
    )
    result = apply_trust_gates_before_pr(model, max_retries=3)
    assert result.reinvestigation_exhausted is True
    assert result.force_draft_pr is True


def test_derive_reproduction_confidence_targeted():
    assert derive_reproduction_confidence({"reexecution_is_targeted": True}) == "exact_test"


def test_derive_reproduction_confidence_full_suite():
    assert derive_reproduction_confidence({}) == "full_suite"
    assert derive_reproduction_confidence({"reexecution_is_targeted": False}) == "full_suite"


def test_apply_trust_gates_sets_reproduction_confidence():
    model = RunStateModel(
        run_id="r1",
        repo_path="/tmp",
        reproduction={"reexecution_is_targeted": True},
    )
    result = apply_trust_gates_before_pr(model, max_retries=3)
    assert result.reproduction_confidence == "exact_test"


def test_trust_gates_block_auto_merge():
    exact = RunStateModel(run_id="r1", repo_path="/tmp", reproduction_confidence="exact_test")
    assert trust_gates_block_auto_merge(exact) is False

    full_suite = RunStateModel(run_id="r1", repo_path="/tmp", reproduction_confidence="full_suite")
    assert trust_gates_block_auto_merge(full_suite) is True

    exact.validation_exhausted = True
    assert trust_gates_block_auto_merge(exact) is True


def test_a10_route_validation_exhausted_never_auto_mergeable():
    from backend.agents.a10_routing import route_pr_decision

    state = RunStateModel(
        run_id="r1",
        repo_path="/tmp",
        validation_exhausted=True,
        force_draft_pr=True,
    )
    axis = AxisScores(correctness=100, security=100, fidelity=100, scope_risk=100)
    pr_type, note = route_pr_decision(state, axis, set())
    assert pr_type == "draft"
    assert "Validation retries exhausted" in (note or "")


def test_a10_route_reinvestigation_exhausted_with_validation_pass_routes_diff_only():
    state = RunStateModel(
        run_id="r1",
        repo_path="/tmp",
        reinvestigation_exhausted=True,
        force_draft_pr=True,
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
        root_cause={"evidence_incomplete": True},
    )
    axis = AxisScores(correctness=80, security=100, fidelity=70, scope_risk=90)
    from backend.agents.a10_routing import route_pr_decision

    pr_type, note = route_pr_decision(state, axis, set())
    assert pr_type == "diff_only"
    assert "citation review" in (note or "").lower()


def test_a10_route_high_scores_without_exhaustion_can_auto_merge():
    from backend.agents.a10_routing import route_pr_decision

    state = RunStateModel(
        run_id="r1",
        repo_path="/tmp",
        reproduction={"status": "CONFIRMED", "reexecution_is_targeted": True},
        reproduction_confidence="exact_test",
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "patch_retry_required": False,
            "correctness_score": 100.0,
        },
        security_result={"rejected": False, "security_score": 100.0},
    )
    axis = AxisScores(correctness=100, security=100, fidelity=100, scope_risk=100)
    pr_type, _ = route_pr_decision(state, axis, set())
    assert pr_type == "auto_mergeable"


def test_full_suite_never_auto_mergeable():
    """Regression: full_suite proof must cap at diff_only, never auto_mergeable."""
    from backend.agents.a10_routing import route_pr_decision

    state = RunStateModel(
        run_id="r1",
        repo_path="/tmp",
        reproduction={"status": "CONFIRMED"},
        reproduction_confidence="full_suite",
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "patch_retry_required": False,
            "correctness_score": 100.0,
        },
        security_result={"rejected": False, "security_score": 100.0},
    )
    axis = AxisScores(correctness=100, security=100, fidelity=100, scope_risk=100)
    pr_type, note = route_pr_decision(state, axis, set())
    assert pr_type != "auto_mergeable"
    assert pr_type == "diff_only"
    assert note is not None
    assert "full-suite" in note.lower()


def test_publish_fix_branch_commit_push_then_pr():
    settings = Settings(github_dry_run=True)
    svc = GitHubPRService(settings)

    with patch.object(svc, "create_branch_and_commit", return_value=True) as branch_commit:
        with patch.object(svc, "create_pr", return_value="https://example.com/pr/1") as create_pr:
            url = svc.publish_fix(
                repo_path="/tmp/repo",
                branch="proofix-fix-abc",
                patch_files={"src/app.py": "patched\n"},
                commit_message="fix: security",
                title="fix",
                body="body",
                draft=True,
            )

    branch_commit.assert_called_once_with(
        "/tmp/repo",
        "proofix-fix-abc",
        {"src/app.py": "patched\n"},
        "fix: security",
        amend_with_proof=True,
    )
    create_pr.assert_called_once_with(
        title="fix",
        body="body",
        branch="proofix-fix-abc",
        draft=True,
    )
    assert url == "https://example.com/pr/1"


def test_publish_fix_aborts_when_branch_push_fails():
    settings = Settings(github_dry_run=False, github_token="token")
    svc = GitHubPRService(settings)

    with patch.object(svc, "create_branch_and_commit", return_value=False):
        with patch.object(svc, "create_pr") as create_pr:
            url = svc.publish_fix(
                repo_path="/tmp/repo",
                branch="proofix-fix-abc",
                patch_files={"src/app.py": "patched\n"},
                commit_message="fix",
                title="fix",
                body="body",
            )

    create_pr.assert_not_called()
    assert url is None


@pytest.mark.asyncio
async def test_a4_persists_reinvestigation_count():
    from pathlib import Path

    from backend.agents.a4_evidence_investigator import A4EvidenceInvestigatorAgent

    store = MagicMock()
    store.append_event = AsyncMock()
    settings = Settings(stub_mode=True)
    agent = A4EvidenceInvestigatorAgent(store, settings)

    state = RunStateModel(
        run_id="r1",
        repo_path=str(Path(__file__).parent.parent.parent / "vulnapi"),
        repo_clone_path=str(Path(__file__).parent.parent.parent / "vulnapi"),
        static_report={"prioritized": [{"file": "nonexistent.py", "line": 1, "message": "x"}]},
        reproduction={"stack_trace": ""},
        root_cause={"reinvestigation_count": 1},
    )

    result = await agent.run(state)
    root = result.root_cause or {}
    assert root.get("reinvestigation_count") == 2
    assert root.get("reinvestigation_required") is True


@pytest.mark.asyncio
async def test_a4_reinvestigation_exhaustion_sets_flags():
    from pathlib import Path

    from backend.agents.a4_evidence_investigator import A4EvidenceInvestigatorAgent

    store = MagicMock()
    store.append_event = AsyncMock()
    settings = Settings(stub_mode=True)
    agent = A4EvidenceInvestigatorAgent(store, settings)

    state = RunStateModel(
        run_id="r1",
        repo_path=str(Path(__file__).parent.parent.parent / "vulnapi"),
        repo_clone_path=str(Path(__file__).parent.parent.parent / "vulnapi"),
        static_report={"prioritized": [{"file": "nonexistent.py", "line": 1, "message": "x"}]},
        reproduction={"stack_trace": ""},
        root_cause={"reinvestigation_count": MAX_REINVESTIGATIONS},
    )

    result = await agent.run(state)
    root = result.root_cause or {}
    assert root.get("evidence_incomplete") is True
    assert root.get("reinvestigation_required") is False
    assert result.reinvestigation_exhausted is True

    # A4 records the observation and stops there — `force_draft_pr` is
    # `trust_gating`'s to write, from exactly this evidence (B-B10). The agent
    # setting it too was one of three writers with no single moment of truth.
    assert result.force_draft_pr is False
    assert apply_trust_gates_before_pr(result, max_retries=3).force_draft_pr is True
