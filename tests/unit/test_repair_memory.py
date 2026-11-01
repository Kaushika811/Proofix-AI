"""Repair memory: hashing, classification, recording, and retrieval ranking."""


import pytest

from backend.models.repository_graph import RepairMemory, RepairQuery, RepairRecord
from backend.services.repair_memory import (
    MAX_RECORDS,
    build_repair_record,
    classify_bug_type,
    content_hash,
    file_hash,
    find_similar_repairs,
    function_hash,
    normalize_bug_type,
    record_repair,
    repair_signal,
    repository_id,
    summarize_matches,
)

MODULE = '''def target(value):
    return value + 1


def other():
    return 0
'''


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "mod.py").write_text(MODULE)
    return tmp_path


# -- hashing ---------------------------------------------------------------


def test_content_hash_is_stable_and_distinguishing():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")


def test_repository_id_is_path_independent_but_name_dependent():
    assert repository_id("/tmp/clone-1/vulnapi") == repository_id("/other/vulnapi")
    assert repository_id("/tmp/a") != repository_id("/tmp/b")


def test_file_hash_reads_the_working_tree(repo):
    assert file_hash(repo, "mod.py") == content_hash(MODULE)


def test_file_hash_of_missing_file_is_empty(repo):
    assert file_hash(repo, "nope.py") == ""


def test_function_hash_covers_only_that_function(repo):
    first = function_hash(repo, "mod.py", "target")
    assert first
    assert first != function_hash(repo, "mod.py", "other")


def test_function_hash_changes_when_the_body_changes(repo):
    before = function_hash(repo, "mod.py", "target")
    (repo / "mod.py").write_text(MODULE.replace("value + 1", "value + 2"))
    assert function_hash(repo, "mod.py", "target") != before


def test_function_hash_is_empty_without_a_function_name(repo):
    assert function_hash(repo, "mod.py", None) == ""
    assert function_hash(repo, "mod.py", "missing") == ""


# -- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "exception_type,expected",
    [
        ("AttributeError", "null-dereference"),
        ("KeyError", "missing-key"),
        ("ValueError", "value-validation"),
        ("FileNotFoundError", "io-failure"),
        ("ModuleNotFoundError", "import-failure"),
    ],
)
def test_runtime_exception_drives_the_category(exception_type, expected):
    assert classify_bug_type({"exception_type": exception_type}) == expected


def test_unmapped_exception_keeps_its_own_name():
    assert classify_bug_type({"exception_type": "pkg.CustomError"}) == "exception:CustomError"


def test_runtime_evidence_outranks_static_findings():
    """An observed exception is a fact; a static finding is an inference."""
    assert (
        classify_bug_type(
            {"exception_type": "KeyError"},
            None,
            [{"rule_id": "B105"}],
        )
        == "missing-key"
    )


def test_security_rule_classifies_as_security_finding():
    assert classify_bug_type(None, None, [{"rule_id": "B105"}]) == "security-finding"


def test_citations_without_runtime_evidence_are_a_logic_error():
    assert classify_bug_type(None, {"citations": [{"file": "a.py"}]}, None) == "logic-error"


def test_no_evidence_is_unknown():
    assert classify_bug_type(None, None, None) == "unknown"


def test_normalize_bug_type_slugifies():
    assert normalize_bug_type("Null Dereference!") == "null-dereference"
    assert normalize_bug_type("") == "unknown"


# -- recording -------------------------------------------------------------


def make_record(**overrides) -> RepairRecord:
    defaults = dict(
        repair_id="r1",
        repository_hash="hash-a",
        file="mod.py",
        file_hash="fh1",
        function="target",
        function_hash="qh1",
        bug_type="missing-key",
        validation_passed=True,
    )
    defaults.update(overrides)
    return RepairRecord(**defaults)


def test_build_record_hashes_the_pre_patch_source(repo):
    """The hash must describe the code as a future run will encounter it."""
    patched = MODULE.replace("value + 1", "value + 2")
    (repo / "mod.py").write_text(patched)

    record = build_repair_record(
        run_id="run-1",
        repo_path=repo,
        repository_hash="hash-a",
        file="mod.py",
        function="target",
        bug_type="missing-key",
        original_file_source=MODULE,
    )
    assert record.file_hash == content_hash(MODULE)
    assert record.file_hash != content_hash(patched)
    assert record.function_hash


def test_build_record_falls_back_to_the_working_tree(repo):
    record = build_repair_record(
        run_id="run-1",
        repo_path=repo,
        repository_hash="hash-a",
        file="mod.py",
        function="target",
        bug_type="missing-key",
    )
    assert record.file_hash == content_hash(MODULE)


def test_record_repair_appends():
    memory = RepairMemory(repository_id="repo")
    record_repair(memory, make_record(repair_id="r1"))
    record_repair(memory, make_record(repair_id="r2"))
    assert [r.repair_id for r in memory.records] == ["r1", "r2"]


def test_re_recording_the_same_id_replaces_rather_than_duplicates():
    memory = RepairMemory(repository_id="repo")
    record_repair(memory, make_record(repair_id="r1", retry_count=0))
    record_repair(memory, make_record(repair_id="r1", retry_count=3))
    assert len(memory.records) == 1
    assert memory.records[0].retry_count == 3


def test_memory_is_bounded_dropping_oldest_first():
    memory = RepairMemory(repository_id="repo")
    for i in range(MAX_RECORDS + 10):
        record_repair(memory, make_record(repair_id=f"r{i}"))
    assert len(memory.records) == MAX_RECORDS
    assert memory.records[-1].repair_id == f"r{MAX_RECORDS + 9}"


# -- retrieval -------------------------------------------------------------


def query(**overrides) -> RepairQuery:
    defaults = dict(
        repository_hash="hash-current",
        file="mod.py",
        file_hash="fh1",
        function="target",
        function_hash="qh1",
        bug_type="missing-key",
    )
    defaults.update(overrides)
    return RepairQuery(**defaults)


def test_identical_function_body_is_the_strongest_match():
    memory = RepairMemory(records=[make_record()])
    match = find_similar_repairs(memory, query())[0]
    assert "identical function body" in match.matched_on
    assert match.similarity > 1.0


def test_failed_repairs_are_never_returned():
    memory = RepairMemory(records=[make_record(validation_passed=False)])
    assert find_similar_repairs(memory, query()) == []


def test_records_from_the_current_repository_state_are_excluded():
    """A run must not retrieve its own output — that is circular."""
    memory = RepairMemory(records=[make_record(repository_hash="hash-current")])
    assert find_similar_repairs(memory, query(repository_hash="hash-current")) == []


def test_weak_match_below_the_threshold_is_dropped():
    memory = RepairMemory(
        records=[make_record(file="other.py", file_hash="zz", function="x", function_hash="yy", bug_type="unrelated")]
    )
    assert find_similar_repairs(memory, query()) == []


def test_stronger_match_ranks_first():
    exact = make_record(repair_id="exact")
    weak = make_record(repair_id="weak", file_hash="different", function_hash="different")
    memory = RepairMemory(records=[weak, exact])
    ranked = find_similar_repairs(memory, query())
    assert ranked[0].record.repair_id == "exact"


def test_limit_is_respected():
    memory = RepairMemory(
        records=[make_record(repair_id=f"r{i}") for i in range(5)]
    )
    assert len(find_similar_repairs(memory, query(), limit=2)) == 2


def test_ranking_is_deterministic():
    memory = RepairMemory(records=[make_record(repair_id=f"r{i}") for i in range(5)])
    assert [m.record.repair_id for m in find_similar_repairs(memory, query(), limit=5)] == [
        m.record.repair_id for m in find_similar_repairs(memory, query(), limit=5)
    ]


def test_empty_memory_returns_nothing():
    assert find_similar_repairs(RepairMemory(), query()) == []


# -- signals and summaries -------------------------------------------------


def test_repair_signal_saturates_at_three_prior_repairs():
    memory = RepairMemory(records=[make_record(repair_id=f"r{i}") for i in range(5)])
    assert repair_signal(memory, "mod.py") == 1.0
    assert repair_signal(memory, "other.py") == 0.0


def test_repair_signal_scales_below_saturation():
    memory = RepairMemory(records=[make_record()])
    assert 0 < repair_signal(memory, "mod.py") < 1.0


def test_summary_omits_the_diff():
    """A past patch in a prompt competes with the runtime evidence."""
    memory = RepairMemory(records=[make_record(accepted_patch_diff="--- a\n+++ b\n")])
    summary = summarize_matches(find_similar_repairs(memory, query()))[0]
    assert "accepted_patch_diff" not in summary
    assert "diff" not in str(summary)
    assert summary["file"] == "mod.py"
    assert summary["matched_on"]
