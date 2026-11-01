"""Recovery decisions come from observed validation evidence, not retry count alone."""

from __future__ import annotations

import pytest

from backend.config import Settings
from backend.orchestrator.edges import (
    after_mutation, after_planning, after_recovery_increment, should_reinvestigate,
)
from backend.orchestrator.graph import build_graph
from backend.services.evidence_recovery import (
    contract_hash, patch_hash, record_attempt, record_rejected_duplicate,
    record_security_failure,
    repeats_failed_patch,
)
from backend.state.schema import RunStateModel


def _state(*, message="AssertionError: assert False is True", target="pkg/auth.py"):
    return RunStateModel(
        run_id="recovery", issue_contract={"description": "reject expired tokens",
                                           "target_test": "tests/test_auth.py::test_expired",
                                           "target_file": "pkg/auth.py"},
        generated_test={"code": "def test_reported_issue(): assert False", "path": "tests/test_issue.py"},
        reproduction={"status": "CONFIRMED", "failing_test": "tests/test_auth.py::test_expired"},
        root_cause={"root_cause": "expiry guard absent"},
        blast_graph={"target_resolution": {"resolved_path": target}},
        patch_bundle={"patches": [{"file": "pkg/auth.py", "original": "old", "patched": "new"}]},
        mutation_result={"pytest_passed": False, "pytest_available": True,
                         "target_test_passed": False, "patch_retry_required": True,
                         "validation_failure": {"failing_test": "tests/test_auth.py::test_expired",
                                                "assertion_message": message,
                                                "traceback": message}},
    )


@pytest.mark.parametrize("category,route,change", [
    ("implementation_failure", "generate_code", None),
    ("wrong_target", "blast_scope", {"blast_graph": {"target_resolution": {"resolved_path": "pkg/other.py"}}}),
    ("incorrect_diagnosis", "investigate", {"investigation": {"evidence": [
        {"category": "source", "status": "present", "stance": "contradicting"}]}}),
    ("environment_failure", "recovery_stop", {"message": "ModuleNotFoundError: No module named pytest"}),
    ("insufficient_evidence", "investigate", {"root_cause": {"root_cause": "unknown", "evidence_incomplete": True}}),
])
def test_failure_categories_route_by_evidence(category, route, change):
    state = _state(message=(change or {}).get("message", "AssertionError: assert False is True"))
    for key, value in (change or {}).items():
        if key != "message":
            setattr(state, key, value)
    pinned = contract_hash(state)
    record_attempt(state, max_retries=3)
    assert state.recovery_attempts[0]["failure_category"] == category
    assert after_mutation(state.model_dump()) == route
    assert state.recovery_contract_hash == pinned
    if route not in {"recovery_stop", "validate_security"}:
        assert after_recovery_increment(state.model_dump()) == route


@pytest.mark.parametrize("error", [
    "ImportError: cannot import name validate_token",
    "TimeoutExpired: pytest timed out",
    "sqlite3.OperationalError: no such table: users",
])
def test_exceptions_are_not_behavioral_assertions(error):
    state = _state(message=error)
    record_attempt(state, 3)
    assert state.recovery_attempts[0]["failure_category"] == "environment_failure"
    assert after_mutation(state.model_dump()) == "recovery_stop"


def test_attempt_history_records_delta_and_stops_duplicate():
    state = _state()
    record_attempt(state, 3)
    assert repeats_failed_patch(state.patch_bundle, state.recovery_attempts)
    state.retry_count = 1
    record_attempt(state, 3)
    assert state.recovery_attempts[1]["patch_hash"] == patch_hash(state.patch_bundle)
    assert state.recovery_attempts[1]["changed_from_previous"] == {
        "target": False, "diagnosis": False, "patch": False,
    }
    assert state.recovery_attempts[1]["recovery_decision"] == "recovery_stop"


def test_rejected_duplicate_is_recorded_without_claiming_execution():
    state = _state()
    record_attempt(state, 3)
    record_rejected_duplicate(state, state.patch_bundle)
    assert len(state.recovery_attempts) == 2
    assert state.recovery_attempts[1]["validation_status"] == "not_executed"
    assert state.recovery_attempts[1]["failure"] is None
    assert state.recovery_attempts[1]["patch_hash"] == state.recovery_attempts[0]["patch_hash"]
    assert state.recovery_attempts[1]["changed_from_previous"] == {
        "target": False, "diagnosis": False, "patch": False,
    }


def test_contract_fingerprint_covers_issue_and_unchanged_test_source():
    state = _state()
    original = contract_hash(state)
    state.generated_test["content"] = "def test_reported_issue(): assert True"
    assert contract_hash(state) != original
    state.generated_test.pop("content")
    state.issue_contract["expected_behavior"] = "accept all tokens"
    assert contract_hash(state) != original


@pytest.mark.asyncio
async def test_a7_rejects_changed_contract_before_generation(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from backend.agents.a7_code_generation import A7CodeGenerationAgent

    state = _state()
    state.recovery_contract_hash = contract_hash(state)
    state.issue_contract["description"] = "weakened behavior"
    store = MagicMock()
    agent = A7CodeGenerationAgent(store, Settings())
    monkeypatch.setattr(agent, "emit_status", AsyncMock())
    result = await agent.run(state)
    assert result.recovery_blocked_reason == "Issue contract or acceptance test changed during recovery"
    store.acquire_lock.assert_not_called()


def test_retry_budget_stops_even_with_new_patch():
    state = _state()
    state.retry_count = 3
    record_attempt(state, 3)
    assert after_mutation(state.model_dump()) == "recovery_stop"


def test_success_preserves_security_route():
    state = _state()
    state.mutation_result = {"pytest_passed": True, "target_test_passed": True,
                             "regression_tests_passed": True}
    record_attempt(state, 3)
    assert after_mutation(state.model_dump()) == "validate_security"


def test_security_rejection_marks_same_patch_failed_for_duplicate_guard():
    state = _state()
    state.mutation_result = {"pytest_passed": True, "target_test_passed": True,
                             "regression_tests_passed": True}
    record_attempt(state, 3)
    state.security_result = {"rejected": True, "validation_failure": {
        "assertion_message": "New high-severity finding"}}
    record_security_failure(state, 3)
    assert state.recovery_attempts[0]["failure_category"] == "implementation_failure"
    assert state.recovery_attempts[0]["failure"] == "New high-severity finding"
    assert repeats_failed_patch(state.patch_bundle, state.recovery_attempts)


def test_untargeted_run_with_no_patch_keeps_existing_review_route():
    state = _state()
    state.issue_contract = None
    state.patch_bundle = {"patches": []}
    record_attempt(state, 3)
    assert after_mutation(state.model_dump()) == "route_pr"


def test_targeted_run_with_no_patch_stops_for_review():
    state = _state()
    state.patch_bundle = {"patches": []}
    record_attempt(state, 3)
    assert after_mutation(state.model_dump()) == "recovery_stop"


def test_wrong_target_graph_path_reenters_planning():
    graph = build_graph(None, Settings()).compile().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("validate_mutation", "increment_retry") in edges
    assert ("increment_retry", "blast_scope") in edges
    assert ("blast_scope", "engineer_context") in edges
    assert ("engineer_context", "plan_fixes") in edges
    assert ("plan_fixes", "generate_code") in edges
    assert ("plan_fixes", "recovery_stop") in edges
    assert ("increment_retry", "investigate") in edges


def test_failed_target_resolution_stops_after_planning():
    assert after_planning({"recovery_blocked_reason": "No safe target"}) == "recovery_stop"
    assert after_planning({"recovery_blocked_reason": None}) == "generate_code"


@pytest.mark.parametrize("category", ["incorrect_diagnosis", "insufficient_evidence"])
def test_unresolved_investigation_stops_for_review(category):
    assert should_reinvestigate({
        "recovery_decision": {"category": category},
        "root_cause": {"evidence_incomplete": True},
    }) == "recovery_stop"
