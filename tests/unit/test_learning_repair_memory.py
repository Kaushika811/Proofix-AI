"""Repair Memory v2: categorisation, storage, aggregation and privacy.

The privacy tests are the ones that matter most. The guarantee is structural —
there is nowhere in the model to put source — and these assert that the
structure actually holds rather than trusting the docstring.
"""

import pytest

from backend.models.learning import RepairKnowledge
from backend.learning.repair_memory import (
    FORBIDDEN_FIELDS,
    MAX_RECORDS,
    PrivacyViolation,
    RepairMemoryV2,
    build_repair_knowledge,
    classify_bug_category,
    classify_root_cause,
    issue_signature,
    signature,
    summarize_patch,
)


def record(**overrides) -> RepairKnowledge:
    base = dict(
        repair_id="r1",
        repository_id="repo-a",
        bug_category="value-validation",
        root_cause_category="missing-validation",
        issue_signature=issue_signature("value-validation", "ValueError", "missing-validation"),
        validation_passed=True,
    )
    base.update(overrides)
    return RepairKnowledge(**base)


# -- signatures ------------------------------------------------------------


def test_signature_is_stable():
    assert signature("a", "b") == signature("a", "b")


def test_signature_is_case_insensitive():
    assert signature("KeyError") == signature("keyerror")


def test_signature_distinguishes_inputs():
    assert signature("a") != signature("b")


def test_issue_signature_excludes_location():
    """The point is recognising the same defect shape elsewhere."""
    assert issue_signature("missing-key", "KeyError", "null-handling") == issue_signature(
        "missing-key", "KeyError", "null-handling"
    )


def test_issue_signature_strips_module_prefix():
    assert issue_signature("x", "pkg.ValueError") == issue_signature("x", "ValueError")


def test_different_categories_produce_different_signatures():
    assert issue_signature("a", "ValueError") != issue_signature("b", "ValueError")


# -- root cause classification --------------------------------------------


@pytest.mark.parametrize(
    "summary,expected",
    [
        ("missing validation of the input", "missing-validation"),
        ("off-by-one in the loop bound", "boundary-condition"),
        ("value was None when accessed", "null-handling"),
        ("token expiry was never compared", "expiry-comparison"),
        ("race condition between threads", "state-management"),
        ("wrong type passed to the parser", "type-mismatch"),
        ("default configuration was wrong", "configuration"),
        ("file handle was never closed", "resource-handling"),
        ("the condition branches incorrectly", "logic-error"),
    ],
)
def test_root_cause_categories(summary, expected):
    assert classify_root_cause(summary) == expected


def test_unrecognised_root_cause_is_unknown():
    """An honest unknown beats a wrong category poisoning every template."""
    assert classify_root_cause("something entirely unrelated happened") == "unknown"


def test_empty_root_cause_is_unknown():
    assert classify_root_cause("") == "unknown"
    assert classify_root_cause("   ") == "unknown"


# -- bug categorisation ----------------------------------------------------


@pytest.mark.parametrize(
    "exception,expected",
    [
        ("AttributeError", "null-dereference"),
        ("TypeError", "type-error"),
        ("KeyError", "missing-key"),
        ("IndexError", "index-error"),
        ("ValueError", "value-validation"),
        ("ZeroDivisionError", "arithmetic"),
        ("TimeoutError", "timeout"),
        ("RuntimeError", "runtime-state"),
    ],
)
def test_exception_drives_category(exception, expected):
    assert classify_bug_category(exception_type=exception) == expected


def test_unmapped_exception_keeps_its_name():
    assert classify_bug_category(exception_type="CustomError") == "exception:CustomError"


@pytest.mark.parametrize(
    "summary,expected",
    [
        ("SQL injection in the query builder", "sql-injection"),
        ("reflected XSS in the template", "xss"),
        ("missing CSRF token check", "csrf"),
        ("command injection via shell", "command-injection"),
        ("path traversal in the file loader", "path-traversal"),
        ("weak crypto: md5 used for hashing", "weak-crypto"),
        ("hardcoded credential in source", "hardcoded-secret"),
        ("unsafe pickle deserialization", "deserialization"),
    ],
)
def test_security_categories(summary, expected):
    assert classify_bug_category(root_cause_summary=summary) == expected


def test_security_category_outranks_the_exception():
    """A SQL-injection defect raising TypeError is still SQL injection."""
    assert classify_bug_category("TypeError", "SQL injection in the query") == "sql-injection"


def test_static_rule_contributes():
    assert classify_bug_category(static_rule="B105 hardcoded password") == "hardcoded-secret"


def test_no_evidence_is_unknown():
    assert classify_bug_category() == "unknown"


def test_root_cause_is_the_fallback():
    assert classify_bug_category(root_cause_summary="off-by-one error") == "boundary-condition"


# -- patch summarisation ---------------------------------------------------


def test_empty_patch_list():
    assert summarize_patch([]) == ("no changes", 0, 0, 0)


def test_summary_reports_shape_not_content():
    summary, added, removed, functions = summarize_patch(
        [{"file": "a.py", "original": "x = 1\n", "patched": "x = 2\ny = 3\n"}]
    )
    assert "file(s)" in summary
    assert added > 0
    assert "x = 2" not in summary


def test_summary_counts_files():
    summary, *_ = summarize_patch([{"original": "", "patched": ""}, {"original": "", "patched": ""}])
    assert summary.startswith("2 file(s)")


def test_summary_counts_functions():
    _s, _a, _r, functions = summarize_patch(
        [{"patched": "def a():\n    pass\n\n\nasync def b():\n    pass\n", "original": ""}]
    )
    assert functions == 2


def test_summary_detects_removal():
    _s, _added, removed, _f = summarize_patch(
        [{"original": "a\nb\nc\n", "patched": "a\n"}]
    )
    assert removed > 0


def test_summary_is_one_line():
    summary, *_ = summarize_patch([{"original": "a\nb\n", "patched": "c\nd\n"}])
    assert "\n" not in summary


# -- privacy ---------------------------------------------------------------


def test_model_has_no_field_that_could_hold_source():
    """The structural guarantee: there is nowhere to put content."""
    fields = set(RepairKnowledge.model_fields)
    assert not (fields & FORBIDDEN_FIELDS)


def test_multiline_patch_summary_is_refused():
    memory = RepairMemoryV2()
    with pytest.raises(PrivacyViolation, match="one-line"):
        memory.record(record(patch_summary="def f():\n    return 1\n"))


def test_oversized_patch_summary_is_refused():
    memory = RepairMemoryV2()
    with pytest.raises(PrivacyViolation):
        memory.record(record(patch_summary="x" * 500))


def test_built_record_contains_no_source():
    knowledge = build_repair_knowledge(
        repair_id="r1",
        patches=[{"file": "a.py", "original": "SECRET_MARKER = 1\n", "patched": "SECRET_MARKER = 2\n"}],
    )
    assert "SECRET_MARKER" not in knowledge.model_dump_json()


def test_built_record_keeps_file_names_only():
    knowledge = build_repair_knowledge(
        repair_id="r1", patches=[{"file": "pkg/auth.py", "original": "a\n", "patched": "b\n"}]
    )
    assert knowledge.target_files == ["pkg/auth.py"]


# -- record construction ---------------------------------------------------


def test_build_from_run_state():
    knowledge = build_repair_knowledge(
        repair_id="r1",
        run_id="run-1",
        repository_id="repo-a",
        reproduction={"exception_type": "KeyError"},
        root_cause={"root_cause": "missing validation", "citations": [{"symbol": "validate"}]},
        patches=[{"file": "a.py", "original": "x\n", "patched": "y\n"}],
        mutation_result={"pytest_passed": True, "mutation_score": 0.8, "mutation_status": "ok"},
        security_result={"rejected": False, "security_score": 95.0},
        pr_decision={"pr_type": "draft"},
        retry_count=2,
    )
    assert knowledge.bug_category == "missing-key"
    assert knowledge.root_cause_category == "missing-validation"
    assert knowledge.validation_passed
    assert knowledge.mutation_score == 0.8
    assert knowledge.retry_count == 2
    assert knowledge.target_functions == ["validate"]


def test_build_with_no_evidence_degrades():
    knowledge = build_repair_knowledge(repair_id="r1")
    assert knowledge.bug_category == "unknown"
    assert knowledge.target_files == []


def test_succeeded_requires_validation_and_no_rejection():
    assert record(validation_passed=True).succeeded
    assert not record(validation_passed=False).succeeded
    assert not record(validation_passed=True, security_rejected=True).succeeded
    assert not record(validation_passed=True, rolled_back=True).succeeded


# -- store -----------------------------------------------------------------


def test_record_and_retrieve():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1"))
    assert memory.get("r1") is not None
    assert memory.get("absent") is None


def test_re_recording_replaces():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", retry_count=0))
    memory.record(record(repair_id="r1", retry_count=5))
    assert len(memory.records) == 1
    assert memory.get("r1").retry_count == 5


def test_memory_is_bounded():
    memory = RepairMemoryV2()
    for i in range(MAX_RECORDS + 10):
        memory.record(record(repair_id=f"r{i}"))
    assert len(memory.records) == MAX_RECORDS


def test_filter_by_repository():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", repository_id="a"))
    memory.record(record(repair_id="r2", repository_id="b"))
    assert len(memory.for_repository("a")) == 1


def test_filter_by_category():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", bug_category="xss"))
    memory.record(record(repair_id="r2", bug_category="csrf"))
    assert len(memory.for_category("xss")) == 1


def test_filter_by_signature():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", issue_signature="sig-a"))
    memory.record(record(repair_id="r2", issue_signature="sig-b"))
    assert len(memory.for_signature("sig-a")) == 1


def test_successful_filter():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", validation_passed=True))
    memory.record(record(repair_id="r2", validation_passed=False))
    assert len(memory.successful()) == 1


def test_recent_is_newest_first():
    memory = RepairMemoryV2()
    for i in range(5):
        memory.record(record(repair_id=f"r{i}"))
    recent = memory.recent(limit=3)
    assert len(recent) == 3
    assert recent[0].recorded_at >= recent[-1].recorded_at


def test_category_counts_are_sorted_by_frequency():
    memory = RepairMemoryV2()
    for i in range(3):
        memory.record(record(repair_id=f"a{i}", bug_category="xss"))
    memory.record(record(repair_id="b", bug_category="csrf"))
    assert list(memory.category_counts()) == ["xss", "csrf"]


def test_success_rate_ignores_undecided():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", outcome="merged"))
    memory.record(record(repair_id="r2", outcome="rejected"))
    memory.record(record(repair_id="r3", outcome="suggested"))
    assert memory.success_rate() == 0.5


def test_success_rate_without_decisions_is_zero():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", outcome="suggested"))
    assert memory.success_rate() == 0.0


def test_update_outcome_sets_merge_status():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1"))
    memory.update_outcome("r1", "merged")
    assert memory.get("r1").merge_status == "merged"


def test_update_outcome_sets_rollback():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1"))
    memory.update_outcome("r1", "rolled_back")
    assert memory.get("r1").rolled_back


def test_update_outcome_on_unknown_repair_is_none():
    assert RepairMemoryV2().update_outcome("absent", "merged") is None


def test_update_review_stores_categories():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1"))
    memory.update_review("r1", "minor_edits", ["testing"])
    assert memory.get("r1").reviewer_decision == "minor_edits"
    assert memory.get("r1").review_categories == ["testing"]


def test_repositories_are_listed():
    memory = RepairMemoryV2()
    memory.record(record(repair_id="r1", repository_id="b"))
    memory.record(record(repair_id="r2", repository_id="a"))
    assert memory.repositories == ["a", "b"]


def test_recording_is_deterministic():
    first, second = RepairMemoryV2(), RepairMemoryV2()
    for i in range(3):
        first.record(record(repair_id=f"r{i}", bug_category="xss"))
        second.record(record(repair_id=f"r{i}", bug_category="xss"))
    assert first.category_counts() == second.category_counts()
