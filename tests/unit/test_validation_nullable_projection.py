"""Nullable validation projection (R5).

`target_test_passed` and `regression_tests_passed` are `bool | None`: `None`
means the check never ran. The projection collapsed that into the failing
branch, so an unmeasured run reported failures the pipeline never observed.
Three states must stay three states.
"""

import pytest

from backend.services.ui_projection import NOT_MEASURED, _evidence_for, _tristate
from backend.state.schema import RunStateModel

RUN_ID = "c0ffee00-0000-0000-0000-000000000002"


def _state(**mutation) -> RunStateModel:
    return RunStateModel(
        run_id=RUN_ID, repo_path="vulnapi", status="completed", mutation_result=mutation
    )


def _fields(state: RunStateModel) -> dict[str, str]:
    return {f["label"]: f["value"] for f in _evidence_for("mutation", state)["fields"]}


# -- the helper ------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected", [(True, "yes"), (False, "no"), (None, NOT_MEASURED)]
)
def test_tristate_keeps_three_states(value, expected):
    assert _tristate(value, "yes", "no") == expected


def test_not_measured_is_distinct_from_both_outcomes():
    assert NOT_MEASURED not in {"yes", "no", "pass", "fail", "none", "detected"}


def test_tristate_does_not_treat_falsy_as_missing():
    # `False` is a measurement; only `None` is an absence.
    assert _tristate(False, "pass", "fail") == "fail"


# -- projected evidence fields ---------------------------------------------


@pytest.mark.parametrize(
    "value,expected", [(True, "pass"), (False, "fail"), (None, NOT_MEASURED)]
)
def test_target_test_projects_three_states(value, expected):
    assert _fields(_state(target_test_passed=value))["Target test"] == expected


@pytest.mark.parametrize(
    "value,expected", [(True, "none"), (False, "detected"), (None, NOT_MEASURED)]
)
def test_regressions_project_three_states(value, expected):
    assert _fields(_state(regression_tests_passed=value))["Regressions"] == expected


@pytest.mark.parametrize(
    "value,expected", [(True, "yes"), (False, "no"), (None, NOT_MEASURED)]
)
def test_mutant_survived_projects_three_states(value, expected):
    assert _fields(_state(mutant_survived=value))["Mutant survived"] == expected


def test_absent_fields_are_not_measured_rather_than_failed():
    # A result recorded before these fields existed, or a validation that never
    # reached the regression phase.
    fields = _fields(_state(pytest_passed=True))
    assert fields["Target test"] == NOT_MEASURED
    assert fields["Regressions"] == NOT_MEASURED


def test_measured_failure_still_reads_as_failure():
    fields = _fields(_state(target_test_passed=False, regression_tests_passed=False))
    assert fields["Target test"] == "fail"
    assert fields["Regressions"] == "detected"


def test_measured_success_still_reads_as_success():
    fields = _fields(_state(target_test_passed=True, regression_tests_passed=True))
    assert fields["Target test"] == "pass"
    assert fields["Regressions"] == "none"


# -- contract ---------------------------------------------------------------


def test_evidence_field_shape_is_unchanged():
    evidence = _evidence_for("mutation", _state(target_test_passed=None))
    assert set(evidence) >= {"title", "subtitle", "fields"}
    for field in evidence["fields"]:
        assert set(field) == {"label", "value", "mono"}
        assert isinstance(field["value"], str)
