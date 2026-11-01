"""Tri-state measurement semantics.

The invariant under test throughout: **a measured zero and an absent
measurement are different facts.** Conflating them is what reported a skipped
security re-scan as a failed one and averaged the fabricated zero into the score
that gates merges.
"""

import pytest

from backend.models.pr import AxisScores
from backend.services.measurement import (
    below_threshold,
    format_score,
    is_measured,
    measured_mean,
    measured_only,
    meets_threshold,
)


class TestIsMeasured:
    def test_zero_is_measured(self):
        """The whole point: 0.0 is a result, not an absence."""
        assert is_measured(0.0) is True

    def test_none_is_not_measured(self):
        assert is_measured(None) is False

    @pytest.mark.parametrize("value", [100.0, 0.1, -5.0])
    def test_numbers_are_measured(self, value):
        assert is_measured(value) is True


class TestMeasuredOnly:
    def test_drops_none_keeps_zero(self):
        assert measured_only([0.0, None, 90.0, None]) == [0.0, 90.0]

    def test_all_absent(self):
        assert measured_only([None, None]) == []

    def test_empty(self):
        assert measured_only([]) == []


class TestMeasuredMean:
    def test_denominator_is_measurements_not_slots(self):
        """The averaging bug: 4 slots, 2 measured, divided by 4."""
        # 100 and 80 measured; two axes never ran.
        assert measured_mean([100.0, 80.0, None, None]) == 90.0
        # The old behaviour would have been (100 + 80 + 0 + 0) / 4 == 45.0.

    def test_measured_zero_lowers_the_mean(self):
        """A real zero must still count against the score."""
        assert measured_mean([100.0, 0.0]) == 50.0

    def test_all_absent_is_none(self):
        assert measured_mean([None, None, None, None]) is None

    def test_empty_is_none(self):
        assert measured_mean([]) is None

    def test_single_measurement(self):
        assert measured_mean([None, 70.0, None]) == 70.0


class TestThresholds:
    def test_unmeasured_never_clears_a_gate(self):
        """Absence of evidence cannot satisfy a gate that requires evidence."""
        assert meets_threshold(None, 80.0) is False

    def test_unmeasured_is_not_a_failure_either(self):
        """The asymmetry that matters: it has not scored badly, it has not scored."""
        assert below_threshold(None, 80.0) is False

    def test_measured_zero_is_a_failure(self):
        assert below_threshold(0.0, 80.0) is True
        assert meets_threshold(0.0, 80.0) is False

    def test_exact_threshold_clears(self):
        assert meets_threshold(80.0, 80.0) is True
        assert below_threshold(80.0, 80.0) is False

    def test_both_false_for_none_is_intentional(self):
        """`below_threshold` is not the negation of `meets_threshold`."""
        assert meets_threshold(None, 80.0) is False
        assert below_threshold(None, 80.0) is False


class TestFormatScore:
    def test_absent_says_so(self):
        assert format_score(None) == "not measured"

    def test_measured_zero_prints_zero(self):
        assert format_score(0.0) == "0"

    def test_unit_and_places(self):
        assert format_score(90.5, unit="%", places=1) == "90.5%"


class TestAxisScores:
    def test_defaults_are_unmeasured_not_zero(self):
        """Defaulting to 0.0 is what made fabrication possible."""
        axis = AxisScores()
        assert axis.correctness is None
        assert axis.security is None
        assert axis.trust is None

    def test_trust_ignores_unmeasured_axes(self):
        axis = AxisScores(correctness=100.0, fidelity=80.0)
        # Only two axes measured -> mean of those two, not of four.
        assert axis.trust == 90.0

    def test_trust_counts_measured_zero(self):
        axis = AxisScores(correctness=100.0, security=0.0)
        assert axis.trust == 50.0

    def test_fully_measured(self):
        axis = AxisScores(correctness=100.0, security=100.0, fidelity=50.0, scope_risk=90.0)
        assert axis.trust == 85.0

    def test_stored_zeros_still_deserialize(self):
        """Runs persisted before this change carry explicit zeros."""
        axis = AxisScores.model_validate(
            {"correctness": 0.0, "security": 0.0, "fidelity": 0.0, "scope_risk": 0.0}
        )
        # Explicit zeros are measurements and must be treated as such.
        assert axis.trust == 0.0
        assert axis.correctness == 0.0
