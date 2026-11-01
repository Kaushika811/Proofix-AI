"""Mutation output parsing — the evidence behind the correctness axis score.

The regression these lock down: A8 previously reported a hardcoded 0.5 whenever
mutmut output was unavailable, which landed exactly on the auto-merge threshold.
No parse path here may invent a number.
"""

import pytest

from backend.services.mutation_parser import (
    MutationCounts,
    counts_to_outcome,
    parse_legacy_sections,
    parse_mutation_output,
    parse_results_output,
    parse_run_summary,
)

# mutmut 3.x `mutmut results --all true`
RESULTS_MIXED = """
    x_validate_token__mutmut_1: killed
    x_validate_token__mutmut_2: survived
    x_validate_token__mutmut_3: killed
    x_validate_token__mutmut_4: killed
"""

RESULTS_ALL_KILLED = """
    x_auth__mutmut_1: killed
    x_auth__mutmut_2: killed
"""

RESULTS_INCONCLUSIVE = """
    x_auth__mutmut_1: no tests
    x_auth__mutmut_2: skipped
"""

RUN_SUMMARY = "⠹ 10/10  🎉 8 🫥 0  ⏰ 0  🤔 0  🙁 2  🔇 0  🧙 0"

LEGACY_RESULTS = """
Survived 🙁 (2)

---- vulnapi/auth.py (2) ----

1-2

Timed out ⏰ (0)
Killed 🎉 (8)
"""


# -- per-mutant listing ----------------------------------------------------


def test_parses_per_mutant_statuses():
    counts = parse_results_output(RESULTS_MIXED)
    assert counts is not None
    assert counts.killed == 3
    assert counts.survived == 1
    assert counts.evaluated == 4
    assert counts.score() == 0.75


def test_perfect_run_scores_one():
    outcome = counts_to_outcome(parse_results_output(RESULTS_ALL_KILLED))
    assert outcome.status == "scored"
    assert outcome.mutation_score == 1.0
    assert outcome.killed_mutants == 2
    assert outcome.survived_mutants == 0
    assert outcome.mutant_survived is False


def test_inconclusive_only_is_unavailable_not_zero():
    """No conclusive mutants must not read as a score of 0.0."""
    outcome = counts_to_outcome(parse_results_output(RESULTS_INCONCLUSIVE))
    assert outcome.status == "unavailable"
    assert outcome.mutation_score is None
    assert outcome.inconclusive_mutants == 2
    assert "inconclusive" in outcome.unavailable_reason


def test_timeout_and_type_check_count_as_detected():
    counts = parse_results_output(
        "    a: timeout\n    b: caught by type check\n    c: survived\n"
    )
    assert counts.killed == 2
    assert counts.survived == 1
    assert counts.score() == pytest.approx(2 / 3, abs=1e-4)


def test_inconclusive_excluded_from_denominator():
    counts = parse_results_output(
        "    a: killed\n    b: survived\n    c: skipped\n    d: no tests\n"
    )
    assert counts.evaluated == 2
    assert counts.inconclusive == 2
    assert counts.score() == 0.5


def test_no_mutant_lines_returns_none():
    assert parse_results_output("nothing to see here") is None
    assert parse_results_output("") is None


def test_unknown_status_word_is_not_parsed_as_a_mutant():
    assert parse_results_output("    a: exploded\n") is None


# -- run summary -----------------------------------------------------------


def test_parses_emoji_run_summary():
    counts = parse_run_summary(RUN_SUMMARY)
    assert counts.killed == 8
    assert counts.survived == 2
    assert counts.score() == 0.8


def test_summary_uses_final_progress_line():
    """A run rewrites the line in place; only the last one is complete."""
    text = "⠹ 5/10  🎉 4 🫥 0  ⏰ 0  🤔 0  🙁 1  🔇 0\r⠹ 10/10  🎉 9 🫥 0  ⏰ 0  🤔 0  🙁 1  🔇 0"
    counts = parse_run_summary(text)
    assert counts.killed == 9
    assert counts.survived == 1


def test_summary_without_type_check_column():
    counts = parse_run_summary("3/4  🎉 3 🫥 0  ⏰ 0  🤔 0  🙁 0  🔇 0")
    assert counts.killed == 3
    assert counts.by_status.get("not checked") == 1


def test_no_summary_returns_none():
    assert parse_run_summary("mutmut: command not found") is None


# -- legacy 2.x sections ---------------------------------------------------


def test_parses_legacy_sections():
    counts = parse_legacy_sections(LEGACY_RESULTS)
    assert counts.survived == 2
    assert counts.killed == 8
    assert counts.score() == 0.8


# -- end-to-end dispatch ---------------------------------------------------


def test_execution_failure_is_explicitly_unavailable():
    outcome = parse_mutation_output(
        run_exit_code=-1,
        run_stderr="command not found: mutmut",
    )
    assert outcome.status == "unavailable"
    assert outcome.mutation_score is None
    assert outcome.killed_mutants is None
    assert "command not found" in outcome.unavailable_reason


def test_timeout_is_unavailable_not_scored():
    outcome = parse_mutation_output(run_exit_code=-1, run_stderr="timeout")
    assert outcome.status == "unavailable"
    assert outcome.mutation_score is None


def test_unparseable_output_is_unavailable():
    outcome = parse_mutation_output(
        run_exit_code=0,
        run_stdout="something entirely unexpected",
        results_exit_code=0,
        results_stdout="",
    )
    assert outcome.status == "unavailable"
    assert outcome.mutation_score is None
    assert "no parseable" in outcome.unavailable_reason


def test_mutmut_collection_error_reports_missing_sibling_module():
    outcome = parse_mutation_output(
        run_exit_code=1,
        run_stdout=(
            "ModuleNotFoundError: No module named 'shopcore.inventory'\n"
            "failed to collect stats. runner returned 1\n"
        ),
        results_exit_code=0,
        results_stdout="    shopcore.auth.x_is_session_active__mutmut_1: not checked\n",
    )
    assert outcome.status == "unavailable"
    assert outcome.mutation_score is None
    assert outcome.unavailable_reason == (
        "mutmut test collection failed before mutation testing "
        "(missing module shopcore.inventory)"
    )


def test_prefers_per_mutant_listing_over_summary():
    """The listing is authoritative; the summary is only a fallback."""
    outcome = parse_mutation_output(
        run_exit_code=0,
        run_stdout=RUN_SUMMARY,  # would score 0.8
        results_exit_code=0,
        results_stdout=RESULTS_MIXED,  # scores 0.75
    )
    assert outcome.mutation_score == 0.75


def test_falls_back_to_summary_when_results_command_fails():
    outcome = parse_mutation_output(
        run_exit_code=0,
        run_stdout=RUN_SUMMARY,
        results_exit_code=-1,
        results_stderr="no such option: --all",
    )
    assert outcome.status == "scored"
    assert outcome.mutation_score == 0.8


def test_survivors_are_reported_with_real_counts():
    outcome = parse_mutation_output(
        run_exit_code=0,
        results_exit_code=0,
        results_stdout=RESULTS_MIXED,
    )
    assert outcome.mutant_survived is True
    assert outcome.survived_mutants == 1
    assert outcome.killed_mutants == 3
    assert outcome.total_mutants == 4
    assert outcome.by_status == {"killed": 3, "survived": 1}


def test_no_hardcoded_half_score_anywhere():
    """Guards the specific regression: 0.5 must only ever be a real ratio."""
    outcome = parse_mutation_output(run_exit_code=-1, run_stderr="boom")
    assert outcome.mutation_score != 0.5
    outcome = parse_mutation_output(run_exit_code=0, run_stdout="", results_stdout="")
    assert outcome.mutation_score != 0.5


def test_counts_ignore_non_positive_additions():
    counts = MutationCounts()
    counts.add("killed", 0)
    counts.add("survived", -3)
    assert counts.recorded == 0
    assert counts.score() is None
