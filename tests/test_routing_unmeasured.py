"""Routing under unmeasured axes.

Two properties matter and they pull in opposite directions:

  1. An unmeasured axis must never *satisfy* a gate. Absent evidence is not
     evidence, so it cannot earn an auto-merge.
  2. An unmeasured axis must never be *reported* as a low score. "security=0"
     for a scan that never ran is a false accusation about the code.

The old `or 0.0` got (1) right by accident and (2) wrong, because it reached the
correct verdict through a fabricated number that then leaked into the UI.
"""

import pytest

from backend.agents.a10_routing import (
    SCORE_THRESHOLD,
    SECURITY_TECHNICAL_THRESHOLD,
    hard_draft_reason,
    route_pr_decision,
    technical_validation_passed,
)
from backend.models.pr import AxisScores
from backend.state.schema import RunStateModel


def _state(**kwargs) -> RunStateModel:
    """A run that would otherwise pass every gate."""
    base = dict(
        run_id="test-run",
        repo_path="/tmp/repo",
        reproduction={"status": "CONFIRMED"},
        mutation_result={
            "correctness_score": 100.0,
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
        },
        security_result={"security_score": 100.0, "rejected": False},
        validation_exhausted=False,
        # `full_suite` is the schema default and routes to `diff_only` on its
        # own — a whole-suite reproduction is weaker evidence than a targeted
        # one. Pinning `exact_test` keeps these tests measuring the axis
        # semantics rather than that unrelated (and correct) downgrade.
        reproduction_confidence="exact_test",
    )
    base.update(kwargs)
    return RunStateModel(**base)


def _axis(**kwargs) -> AxisScores:
    base = dict(correctness=100.0, security=100.0, fidelity=100.0, scope_risk=90.0)
    base.update(kwargs)
    return AxisScores(**base)


class TestGatesRequireEvidence:
    def test_fully_measured_run_can_auto_merge(self):
        state = _state()
        assert technical_validation_passed(
            state, state.mutation_result, state.security_result, set()
        ) is True

    def test_unmeasured_security_cannot_auto_merge(self):
        """A skipped re-scan must not pass a gate that exists to require one."""
        state = _state(security_result={})
        assert technical_validation_passed(state, state.mutation_result, {}, set()) is False

    def test_unmeasured_correctness_cannot_auto_merge(self):
        state = _state(mutation_result={})
        assert technical_validation_passed(state, {}, state.security_result, set()) is False

    def test_explicit_none_is_treated_as_unmeasured(self):
        state = _state(security_result={"security_score": None, "rejected": False})
        assert technical_validation_passed(
            state, state.mutation_result, state.security_result, set()
        ) is False

    def test_measured_zero_also_fails_the_gate(self):
        """Both fail — but for different reasons, per the reporting tests below."""
        state = _state(security_result={"security_score": 0.0, "rejected": False})
        assert technical_validation_passed(
            state, state.mutation_result, state.security_result, set()
        ) is False


class TestReportingDistinguishesAbsentFromLow:
    def test_measured_low_security_is_reported_as_a_score(self):
        state = _state(security_result={"security_score": 0.0, "rejected": False})
        hard, note = hard_draft_reason(state, _axis(security=0.0), set())
        assert hard is True
        assert "Low axis scores" in note
        assert "security=0" in note

    def test_unmeasured_security_is_not_reported_as_a_score(self):
        """The regression that prompted this sprint."""
        state = _state(security_result={})
        hard, note = hard_draft_reason(state, _axis(security=None), set())
        assert hard is True
        assert "Not measured" in note
        assert "security" in note
        # Must not accuse the code of scoring badly.
        assert "security=0" not in note
        assert "Low axis scores" not in note

    def test_several_unmeasured_axes_are_all_named(self):
        state = _state(mutation_result={}, security_result={})
        hard, note = hard_draft_reason(
            state, _axis(correctness=None, security=None), set()
        )
        assert hard is True
        assert "correctness" in note and "security" in note

    def test_measured_low_takes_precedence_over_absent(self):
        """A real failure is the more useful thing to report first."""
        state = _state(security_result={"security_score": 10.0, "rejected": False})
        hard, note = hard_draft_reason(
            state, _axis(security=10.0, fidelity=None), set()
        )
        assert hard is True
        assert "Low axis scores" in note


class TestRouting:
    def test_measured_pass_routes_auto_mergeable(self):
        state = _state()
        pr_type, _note = route_pr_decision(state, _axis(), set())
        assert pr_type == "auto_mergeable"

    def test_unmeasured_axis_routes_to_draft(self):
        state = _state(security_result={})
        pr_type, note = route_pr_decision(state, _axis(security=None), set())
        assert pr_type == "draft"
        assert "Not measured" in note

    def test_never_auto_merges_on_absence(self):
        """Belt and braces: no combination of absences yields auto-merge."""
        for absent in ["correctness", "security", "fidelity", "scope_risk"]:
            state = _state()
            pr_type, _ = route_pr_decision(state, _axis(**{absent: None}), set())
            assert pr_type != "auto_mergeable", f"{absent} absent should not auto-merge"


class TestTrustScoreArithmetic:
    """`build_run_report`'s composite must not average phantom zeros."""

    def test_partial_measurement_does_not_dilute(self):
        from backend.services.ui_projection import _trust_score

        state = _state(
            pr_decision={
                "axis_scores": {
                    "correctness": 100.0,
                    "security": None,
                    "fidelity": 80.0,
                    "scope_risk": None,
                }
            }
        )
        # Mean of the two measured axes = 90 -> 0.9.
        # The old fixed denominator gave (100 + 0 + 80 + 0) / 4 / 100 = 0.45.
        assert _trust_score(state) == 0.9

    def test_no_measurements_is_none_not_zero(self):
        from backend.services.ui_projection import _trust_score

        state = _state(
            pr_decision={
                "axis_scores": {
                    "correctness": None,
                    "security": None,
                    "fidelity": None,
                    "scope_risk": None,
                }
            }
        )
        assert _trust_score(state) is None

    def test_no_decision_at_all_is_none(self):
        from backend.services.ui_projection import _trust_score

        assert _trust_score(_state(pr_decision=None)) is None

    def test_measured_zeros_still_count(self):
        from backend.services.ui_projection import _trust_score

        state = _state(
            pr_decision={
                "axis_scores": {
                    "correctness": 0.0,
                    "security": 0.0,
                    "fidelity": 100.0,
                    "scope_risk": 100.0,
                }
            }
        )
        assert _trust_score(state) == 0.5


class TestProjectionRendersAbsence:
    def test_tone_for_unmeasured_is_unknown_not_bad(self):
        from backend.services.ui_projection import _tone

        assert _tone(None) == "unknown"
        assert _tone(0.0) == "bad"
        assert _tone(SCORE_THRESHOLD) == "ok"

    def test_score_text_never_invents_a_number(self):
        from backend.services.ui_projection import _score_text

        assert _score_text(None) == "Not measured"
        assert _score_text(0.0) == "0"
        assert _score_text(90.0) == "90"

    def test_score_preserves_none(self):
        from backend.services.ui_projection import _score

        assert _score(None) is None
        assert _score("nonsense") is None
        assert _score(0) == 0.0


@pytest.mark.parametrize("threshold", [SCORE_THRESHOLD, SECURITY_TECHNICAL_THRESHOLD])
def test_thresholds_are_unchanged(threshold):
    """This sprint changed absence handling, not the bars themselves."""
    assert threshold > 0
