"""A8 wiring: real mutation evidence reaches the correctness axis, or nothing does.

`correctness_score` feeds `a10_routing.SCORE_THRESHOLD` (80.0). Previously a
fabricated mutation score of 0.5 produced exactly 80.0 and cleared the gate, so
these tests assert on the score that A10 will actually read.
"""

import json
from configparser import ConfigParser
from unittest.mock import MagicMock

import pytest

from backend.agents.a8_mutation_validator import (
    CORRECTNESS_MUTANT_SURVIVED,
    CORRECTNESS_MUTATION_UNAVAILABLE,
    A8MutationValidatorAgent,
)
from backend.agents.a10_routing import SCORE_THRESHOLD
from backend.config import Settings
from backend.services.scoped_validation import ScopedValidationOutcome
from backend.state.schema import RunStateModel

RESULTS_ALL_KILLED = "    x_auth__mutmut_1: killed\n    x_auth__mutmut_2: killed\n"
RESULTS_ONE_SURVIVED = "    x_auth__mutmut_1: killed\n    x_auth__mutmut_2: survived\n"

PATCH_BUNDLE = {
    "patches": [{"file": "pkg/auth.py", "original": "a", "patched": "b"}],
    "contracts": [{"assertion": "expired tokens rejected", "location": "pkg/auth.py"}],
}


def passing_scope() -> ScopedValidationOutcome:
    return ScopedValidationOutcome(
        target_test_passed=True,
        regression_tests_passed=True,
        pytest_passed=True,
        patch_retry_required=False,
        new_failures=[],
        pre_existing_failures=[],
        validation_failure=None,
        failure_brief_needed=False,
        pytest_reexecution_command="pytest tests/test_auth.py",
    )


def failing_scope() -> ScopedValidationOutcome:
    from backend.models.validation import ValidationFailure

    return ScopedValidationOutcome(
        target_test_passed=False,
        regression_tests_passed=None,
        pytest_passed=False,
        patch_retry_required=True,
        new_failures=[],
        pre_existing_failures=[],
        validation_failure=ValidationFailure(
            failing_test="tests/test_auth.py::test_expired",
            assertion_message="assert False",
        ),
        failure_brief_needed=True,
        pytest_reexecution_command="pytest tests/test_auth.py",
    )


async def _noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_unestablished_causality_stops_before_mutation_and_security(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, scope=passing_scope())
    state = make_state(tmp_path)
    state.acceptance_test = {
        "path": "tests/test_auth.py", "nodeid": "tests/test_auth.py::test_expired",
        "sha256": "pinned",
    }

    async def inconclusive(*args, **kwargs):
        return {
            "behavioral_change_detected": True, "patch_relevance_established": True,
            "causal_effect_supported": None, "acceptance_test_passed": True,
            "reason": "Counterfactual timed out",
        }

    monkeypatch.setattr("backend.agents.a8_mutation_validator.evaluate_patch_causality", inconclusive)
    agent._run_mutmut = MagicMock(side_effect=AssertionError("mutation must not run"))
    await agent.run(state)

    assert state.mutation_result["patch_causality"]["causal_effect_supported"] is None
    assert state.mutation_result["correctness_score"] is None
    assert state.recovery_decision["route"] == "recovery_stop"
    assert state.recovery_blocked_reason == "Counterfactual timed out"


def make_agent(monkeypatch, *, scope, mutmut_results=None, mutmut_exit=0):
    """Build A8 with scoped validation and mutmut subprocess calls stubbed."""

    async def fake_scoped(*args, **kwargs):
        return scope

    calls = {"n": 0}

    async def fake_run_command(cmd, cwd=None, timeout=120, env=None):
        calls["n"] += 1
        if mutmut_exit == -1:
            return -1, "", "command not found: mutmut"
        if "results" in cmd:
            return 0, mutmut_results or "", ""
        return 0, "", ""

    monkeypatch.setattr(
        "backend.agents.a8_mutation_validator.run_scoped_validation", fake_scoped
    )
    monkeypatch.setattr(
        "backend.agents.a8_mutation_validator.run_command", fake_run_command
    )

    store = MagicMock()
    store.set_json = _noop
    store.append_event = _noop
    agent = A8MutationValidatorAgent(store, Settings(stub_mode=True))
    agent.emit_status = _noop
    return agent


def make_state(tmp_path) -> RunStateModel:
    return RunStateModel(
        run_id="r1",
        repo_path=str(tmp_path),
        repo_clone_path=str(tmp_path),
        patch_bundle=PATCH_BUNDLE,
        reproduction={
            "status": "CONFIRMED",
            "failing_test": "tests/test_auth.py::test_expired",
            "pre_existing_failures": [],
        },
    )


def test_mutmut_copies_import_root_and_selects_the_target_test(tmp_path):
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = example\n")
    A8MutationValidatorAgent._configure_mutmut_paths(
        tmp_path, "shopcore/auth.py", "tests/test_auth.py::test_expired",
        ["tests/test_inventory.py::test_oversell", "tests/test_auth.py::test_expired"],
    )

    config = ConfigParser()
    config.read(tmp_path / "setup.cfg")
    assert config.get("metadata", "name") == "example"
    assert config.get("mutmut", "source_paths") == "shopcore"
    assert config.get("mutmut", "paths_to_mutate") == "shopcore/auth.py"
    assert config.get("mutmut", "only_mutate") == "shopcore/auth.py"
    selection = config.get("mutmut", "pytest_add_cli_args_test_selection")
    assert "--deselect=tests/test_inventory.py::test_oversell" in selection
    assert "test_auth.py" not in selection


@pytest.mark.asyncio
async def test_all_mutants_killed_scores_full_correctness(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, scope=passing_scope(), mutmut_results=RESULTS_ALL_KILLED)
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["mutation_status"] == "scored"
    assert result["mutation_score"] == 1.0
    assert result["killed_mutants"] == 2
    assert result["survived_mutants"] == 0
    assert result["total_mutants"] == 2
    assert result["correctness_score"] == 100.0


@pytest.mark.asyncio
async def test_surviving_mutant_forces_retry_with_real_counts(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, scope=passing_scope(), mutmut_results=RESULTS_ONE_SURVIVED)
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["mutant_survived"] is True
    assert result["survived_mutants"] == 1
    assert result["killed_mutants"] == 1
    assert result["mutation_score"] == 0.5
    assert result["correctness_score"] == CORRECTNESS_MUTANT_SURVIVED
    assert result["patch_retry_required"] is True
    assert "1 of 2 mutants survived" in result["validation_failure"]["assertion_message"]


@pytest.mark.asyncio
async def test_unavailable_mutation_reports_no_score(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, scope=passing_scope(), mutmut_exit=-1)
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["mutation_status"] == "unavailable"
    assert result["mutation_score"] is None
    assert result["killed_mutants"] is None
    assert result["mutation_unavailable_reason"]
    assert result["mutant_survived"] is False


@pytest.mark.asyncio
async def test_unavailable_mutation_cannot_clear_the_merge_threshold(monkeypatch, tmp_path):
    """The core regression: absent evidence must not score as passing evidence."""
    agent = make_agent(monkeypatch, scope=passing_scope(), mutmut_exit=-1)
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["correctness_score"] == CORRECTNESS_MUTATION_UNAVAILABLE
    assert result["correctness_score"] < SCORE_THRESHOLD


@pytest.mark.asyncio
async def test_failed_tests_never_reach_mutation_testing(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, scope=failing_scope())
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["mutation_status"] == "not_run"
    assert result["mutation_score"] is None
    assert result["correctness_score"] == 0.0
    assert result["patch_retry_required"] is True


@pytest.mark.asyncio
async def test_import_failure_stops_recovery_without_behavioral_score(monkeypatch, tmp_path):
    from backend.models.validation import ValidationFailure

    scope = failing_scope()
    scope.validation_failure = ValidationFailure(
        failing_test="tests/test_auth.py::test_expired",
        traceback="ModuleNotFoundError: No module named 'pkg.auth'",
    )
    agent = make_agent(monkeypatch, scope=scope)
    state = await agent.run(make_state(tmp_path))
    assert state.mutation_result["correctness_score"] is None
    assert state.recovery_attempts[0]["failure_category"] == "environment_failure"
    assert state.recovery_decision["route"] == "recovery_stop"


@pytest.mark.asyncio
async def test_empty_patch_bundle_is_unavailable_not_scored(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, scope=passing_scope())
    state = make_state(tmp_path)
    state.patch_bundle = {"patches": [], "contracts": []}

    result = (await agent.run(state)).mutation_result

    assert result["mutation_status"] == "unavailable"
    assert result["mutation_unavailable_reason"] == "no patches to mutate"
    assert result["mutation_score"] is None
    # Nor is correctness scored. With no patch there is nothing to be correct
    # about; the previous 70 was a grade for work never attempted, and it was
    # averaged into the trust score that gates merges.
    assert result["correctness_score"] is None


@pytest.mark.asyncio
async def test_mutation_evidence_is_emitted_for_downstream_consumers(monkeypatch, tmp_path):
    """A10 and the UI read these keys; they must carry parsed counts."""
    agent = make_agent(monkeypatch, scope=passing_scope(), mutmut_results=RESULTS_ONE_SURVIVED)
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["mutants_by_status"] == {"killed": 1, "survived": 1}
    assert json.dumps(result)  # must stay JSON-serialisable for Redis


def pytest_missing_scope() -> ScopedValidationOutcome:
    """pytest never ran: the target repository has no pytest installed.

    `pytest_passed` is False exactly as it is for a genuine test failure, which
    is why the two were conflated.
    """
    from backend.models.validation import ValidationFailure

    return ScopedValidationOutcome(
        target_test_passed=False,
        regression_tests_passed=None,
        pytest_passed=False,
        patch_retry_required=True,
        new_failures=[],
        pre_existing_failures=[],
        validation_failure=ValidationFailure(
            failing_test=None,
            assertion_message=None,
            traceback="/usr/bin/python3: No module named pytest\n",
        ),
        failure_brief_needed=True,
        pytest_reexecution_command="pytest tests/test_auth.py",
        pytest_available=False,
    )


@pytest.mark.asyncio
async def test_unrunnable_pytest_is_unmeasured_not_a_zero(monkeypatch, tmp_path):
    """A patch nothing could execute is unmeasured, not failed.

    A cloned repository whose dependencies were never installed cannot run a
    single test. Scoring that as CORRECTNESS_TESTS_FAILED published a hard 0 —
    a failing grade for a patch nothing had examined — and the report then
    printed "correctness 0 / 80" directly above its own sentence saying that
    validation never ran.
    """
    agent = make_agent(monkeypatch, scope=pytest_missing_scope())
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["correctness_score"] is None
    assert result["pytest_available"] is False
    assert result["pytest_passed"] is False


@pytest.mark.asyncio
async def test_a_real_test_failure_still_scores_zero(monkeypatch, tmp_path):
    """The opposite case must keep its measurement.

    pytest ran, the suite failed, and that is a verdict about the patch. If this
    also became `None` the fix would have replaced one wrong answer with another.
    """
    agent = make_agent(monkeypatch, scope=failing_scope())
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert result["correctness_score"] == 0.0
    assert result["pytest_available"] is True


@pytest.mark.asyncio
async def test_unrunnable_pytest_cannot_clear_the_merge_threshold(monkeypatch, tmp_path):
    """An unmeasured axis must not read as a passing one either."""
    from backend.services.measurement import meets_threshold

    agent = make_agent(monkeypatch, scope=pytest_missing_scope())
    result = (await agent.run(make_state(tmp_path))).mutation_result

    assert not meets_threshold(result["correctness_score"], SCORE_THRESHOLD)
