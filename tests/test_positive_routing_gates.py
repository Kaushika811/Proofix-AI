"""Security-axis behaviour on both sides of the auto-merge gate.

The end-to-end positive run (`statskit`, §Positive Routing Validation in
PRODUCTION_CERTIFICATION.md) executed A9 for real and measured `security=100`.
These tests pin the surrounding contract that a single passing run cannot show:

  - an **unmeasured** A9 is `null`, never `0`, stays out of the trust
    denominator, and cannot satisfy the gate;
  - a **measured** A9 enters the denominator, and a *failing* measurement blocks
    auto-merge while a passing one permits it.

The failing-security case is asserted at the routing layer rather than
end-to-end: producing it live would require A7 to write a patch that introduces
a new bandit/semgrep finding, which is not something a fixture can specify. That
limitation is stated in the certification rather than papered over.
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
from backend.services.ui_projection import _score, _score_text, _tone, _trust_score
from backend.state.schema import RunStateModel


def _passing_state(**overrides) -> RunStateModel:
    """A run matching the real `statskit` auto-merge run's shape."""
    base = dict(
        run_id="r",
        repo_path="/tmp/x",
        reproduction={"status": "CONFIRMED", "reexecution_is_targeted": True},
        reproduction_confidence="exact_test",
        mutation_result={
            "correctness_score": 100.0,
            "mutation_score": 1.0,
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "mutant_survived": False,
        },
        security_result={"security_score": 100.0, "rejected": False, "new_findings": []},
        patch_bundle={"patches": [{"file": "statskit.py", "patched": "x"}]},
        validation_exhausted=False,
    )
    base.update(overrides)
    return RunStateModel(**base)


def _axis(**overrides) -> AxisScores:
    base = dict(correctness=100.0, security=100.0, fidelity=100.0, scope_risk=75.0)
    base.update(overrides)
    return AxisScores(**base)


class TestUnmeasuredSecurity:
    """A9 skipped — the common path when mutation validation fails."""

    def test_axis_is_null_not_zero(self):
        axis = AxisScores(correctness=100.0, fidelity=100.0, scope_risk=90.0)
        assert axis.security is None
        assert axis.security != 0

    def test_never_displayed_as_zero(self):
        assert _score(None) is None
        assert _score_text(None) == "Not measured"
        # A measured zero is still shown as zero — the distinction is the point.
        assert _score_text(0.0) == "0"

    def test_not_painted_as_a_failure(self):
        assert _tone(None) == "unknown"
        assert _tone(0.0) == "bad"

    def test_excluded_from_the_trust_denominator(self):
        """Matches the real cfgkit run: trust 0.95 = mean(100, 90), not /4."""
        state = _passing_state(
            pr_decision={
                "axis_scores": {
                    "correctness": None,
                    "security": None,
                    "fidelity": 100.0,
                    "scope_risk": 90.0,
                }
            }
        )
        assert _trust_score(state) == 0.95

    def test_cannot_satisfy_the_auto_merge_gate(self):
        state = _passing_state(security_result={})
        assert technical_validation_passed(
            state, state.mutation_result, {}, set()
        ) is False

    def test_routes_to_draft_naming_absence_not_a_low_score(self):
        state = _passing_state(security_result={})
        pr_type, note = route_pr_decision(state, _axis(security=None), set())
        assert pr_type == "draft"
        assert "Not measured" in note
        assert "security=0" not in note


class TestMeasuredSecurity:
    """A9 executed — proven live by the statskit run."""

    def test_enters_the_trust_denominator(self):
        """Matches the real statskit run: 0.94 = mean(100, 100, 100, 75)."""
        state = _passing_state(
            pr_decision={
                "axis_scores": {
                    "correctness": 100.0,
                    "security": 100.0,
                    "fidelity": 100.0,
                    "scope_risk": 75.0,
                }
            }
        )
        assert _trust_score(state) == 0.94

    def test_passing_score_permits_auto_merge(self):
        state = _passing_state()
        assert technical_validation_passed(
            state, state.mutation_result, state.security_result, set()
        ) is True
        pr_type, _note = route_pr_decision(state, _axis(), set())
        assert pr_type == "auto_mergeable"

    @pytest.mark.parametrize("score", [0.0, 25.0, 50.0, 75.0, 89.9])
    def test_failing_score_blocks_auto_merge(self, score):
        """Anything under the 90 technical threshold must not auto-merge."""
        state = _passing_state(
            security_result={"security_score": score, "rejected": False}
        )
        assert technical_validation_passed(
            state, state.mutation_result, state.security_result, set()
        ) is False
        pr_type, _note = route_pr_decision(state, _axis(security=score), set())
        assert pr_type != "auto_mergeable"

    def test_a_measured_zero_is_reported_as_a_score_not_an_absence(self):
        state = _passing_state(
            security_result={"security_score": 0.0, "rejected": False}
        )
        _hard, note = hard_draft_reason(state, _axis(security=0.0), set())
        assert "security=0" in note
        assert "Not measured" not in note

    def test_rejected_rescan_blocks_regardless_of_score(self):
        state = _passing_state(
            security_result={"security_score": 100.0, "rejected": True}
        )
        assert technical_validation_passed(
            state, state.mutation_result, state.security_result, set()
        ) is False

    def test_exactly_at_the_threshold_passes(self):
        state = _passing_state(
            security_result={
                "security_score": SECURITY_TECHNICAL_THRESHOLD,
                "rejected": False,
            }
        )
        assert technical_validation_passed(
            state, state.mutation_result, state.security_result, set()
        ) is True


class TestCorrectnessGate:
    """The mutmut fix made this axis measurable; the threshold is unchanged."""

    def test_mutation_score_drives_correctness(self):
        from backend.agents.a8_mutation_validator import (
            CORRECTNESS_MUTATION_BASE,
            CORRECTNESS_MUTATION_RANGE,
        )

        # The real statskit run: mutation_score 1.0 -> correctness 100.
        assert CORRECTNESS_MUTATION_BASE + 1.0 * CORRECTNESS_MUTATION_RANGE == 100.0
        # A weak suite scores below the gate — which is the point of measuring.
        assert CORRECTNESS_MUTATION_BASE + 0.25 * CORRECTNESS_MUTATION_RANGE == 70.0
        assert 70.0 < SCORE_THRESHOLD

    def test_half_the_mutants_killed_is_exactly_the_threshold(self):
        from backend.agents.a8_mutation_validator import (
            CORRECTNESS_MUTATION_BASE,
            CORRECTNESS_MUTATION_RANGE,
        )

        assert CORRECTNESS_MUTATION_BASE + 0.5 * CORRECTNESS_MUTATION_RANGE == SCORE_THRESHOLD

    def test_unmeasured_correctness_blocks_auto_merge(self):
        state = _passing_state(mutation_result={})
        assert technical_validation_passed(
            state, {}, state.security_result, set()
        ) is False


class TestReproductionConfidenceSeparatesThePositivePaths:
    """The single predicate between `diff_only` and `auto_mergeable`."""

    def test_exact_test_permits_auto_merge(self):
        state = _passing_state(reproduction_confidence="exact_test")
        pr_type, _note = route_pr_decision(state, _axis(), set())
        assert pr_type == "auto_mergeable"

    def test_full_suite_downgrades_to_diff_only(self):
        """Same evidence, weaker proof — a real routing distinction."""
        state = _passing_state(reproduction_confidence="full_suite")
        pr_type, note = route_pr_decision(state, _axis(), set())
        assert pr_type == "diff_only"
        assert "auto-merge" in note

    def test_unverified_citations_downgrade_to_diff_only(self):
        state = _passing_state(
            reproduction_confidence="exact_test",
            reinvestigation_exhausted=True,
        )
        pr_type, note = route_pr_decision(state, _axis(), set())
        assert pr_type == "diff_only"
        assert "Citation" in note
