"""Parity tests for `a10_routing.gate_checks`.

`gate_checks` duplicates `hard_draft_reason`'s ten-gate short-circuit chain
purely to make it observable — it changes no routing behaviour. These tests
assert that whichever gate `gate_checks` reports as the first failure always
carries the exact same detail text `hard_draft_reason` itself returns, and
that every gate after it is reported as not reached rather than passed. If
someone edits one function's wording without the other, these tests catch it.
"""

from backend.agents.a10_routing import gate_checks, hard_draft_reason
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


def _assert_parity(state: RunStateModel, axis: AxisScores, phantoms: set[str], expected_code: str):
    hard, note = hard_draft_reason(state, axis, phantoms)
    assert hard is True

    checks = gate_checks(state, axis, phantoms)
    firing = [c for c in checks if c.checked and c.passed is False]
    assert len(firing) == 1
    assert firing[0].code == expected_code
    assert firing[0].detail == note

    index = checks.index(firing[0])
    for c in checks[:index]:
        assert c.checked is True
        assert c.passed is True
        assert c.detail is None
    for c in checks[index + 1 :]:
        assert c.checked is False
        assert c.passed is None
        assert c.detail is None


def test_validation_exhausted_fires_first_gate():
    state = _validated_state(validation_exhausted=True, retry_count=3)
    _assert_parity(state, _axis(), set(), "validation_exhausted")


def test_patch_retry_required_fires_second_gate():
    state = _validated_state(
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "patch_retry_required": True,
            "correctness_score": 80.0,
        }
    )
    _assert_parity(state, _axis(), set(), "patch_retry_required")


def test_target_test_failed_fires_third_gate():
    state = _validated_state(
        mutation_result={
            "pytest_passed": False,
            "target_test_passed": False,
            "regression_tests_passed": True,
            "patch_retry_required": False,
            "correctness_score": 0.0,
        }
    )
    _assert_parity(state, _axis(correctness=0.0), set(), "target_test_failed")


def test_regression_failed_fires_fourth_gate():
    state = _validated_state(
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": False,
            "patch_retry_required": False,
            "correctness_score": 80.0,
        }
    )
    _assert_parity(state, _axis(), set(), "regression_failed")


def test_security_rejected_fires_fifth_gate():
    state = _validated_state(security_result={"rejected": True, "security_score": 0.0})
    _assert_parity(state, _axis(security=0.0), set(), "security_rejected")


def test_phantoms_fire_sixth_gate():
    state = _validated_state()
    _assert_parity(state, _axis(), {"auth.py"}, "phantoms_detected")


def test_correctness_low_fires_seventh_gate():
    state = _validated_state(
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "patch_retry_required": False,
            "correctness_score": 40.0,
        }
    )
    _assert_parity(state, _axis(correctness=40.0), set(), "correctness_low")


def test_security_low_fires_eighth_gate():
    state = _validated_state(security_result={"rejected": False, "security_score": 40.0})
    _assert_parity(state, _axis(security=40.0), set(), "security_low")


def test_unmeasured_axis_fires_ninth_gate():
    state = _validated_state()
    _assert_parity(state, _axis(fidelity=None), set(), "axes_measured")


def test_reproduction_unconfirmed_fires_tenth_gate():
    state = _validated_state(
        reproduction={"status": "UNCONFIRMED"},
        force_draft_pr=True,
    )
    _assert_parity(state, _axis(), set(), "reproduction_confirmed")


def test_clean_run_has_every_gate_checked_and_passed():
    state = _validated_state()
    hard, note = hard_draft_reason(state, _axis(), set())
    assert hard is False
    assert note is None

    checks = gate_checks(state, _axis(), set())
    assert len(checks) == 10
    assert all(c.checked and c.passed and c.detail is None for c in checks)
    assert [c.code for c in checks] == [
        "validation_exhausted",
        "patch_retry_required",
        "target_test_failed",
        "regression_failed",
        "security_rejected",
        "phantoms_detected",
        "correctness_low",
        "security_low",
        "axes_measured",
        "reproduction_confirmed",
    ]
