"""Incremental indexing: delta classification, reuse, and rebuild equivalence.

The load-bearing test in this file is
`test_incremental_result_matches_full_rebuild`. Incremental indexing is only
safe if it cannot diverge from a rebuild — a stale call graph silently corrupts
A5.5's fan-in ranking, and nothing downstream would notice.
"""

import pytest

from backend.services.repository_indexer import (
    FULL_REBUILD_THRESHOLD,
    diff_file_hashes,
    hash_files,
    index_repository,
)

MOD_A = '''"""Module A."""


def alpha(v):
    return beta(v)


def beta(v):
    return v + 1
'''

MOD_B = '''from pkg.a import alpha


def gamma(v):
    return alpha(v)
'''

ROOTS = ["pkg/"]


@pytest.fixture
def repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text(MOD_A)
    (pkg / "b.py").write_text(MOD_B)
    (tmp_path / "README.md").write_text("# Repo\n\nUses `pkg/a.py`.\n")
    return tmp_path


def fingerprint(index):
    """Everything an incremental pass could plausibly get wrong."""
    return {
        "nodes": sorted(index.repository_graph.nodes),
        "edges": sorted((e.source, e.target, e.kind) for e in index.repository_graph.edges),
        "files": sorted(index.repository_graph.files),
        "callables": sorted(index.call_graph.nodes),
        "call_sites": sorted((s.caller, s.callee) for s in index.call_graph.call_sites),
        "fan": {n.id: (n.fan_in, n.fan_out) for n in index.call_graph.nodes.values()},
        "hashes": index.file_hashes,
        "parsed": sorted(index.parsed_modules),
    }


# -- hashing and delta -----------------------------------------------------


def test_hash_files_skips_unreadable_paths(repo):
    hashes = hash_files(repo, ["pkg/a.py", "pkg/missing.py"])
    assert "pkg/a.py" in hashes
    assert "pkg/missing.py" not in hashes


def test_delta_against_an_empty_cache_is_all_added():
    delta = diff_file_hashes({}, {"a.py": "1", "b.py": "2"})
    assert delta.added == ["a.py", "b.py"]
    assert not delta.is_empty


def test_identical_hashes_produce_an_empty_delta():
    delta = diff_file_hashes({"a.py": "1"}, {"a.py": "1"})
    assert delta.is_empty
    assert delta.unchanged == 1


def test_modified_file_is_classified():
    delta = diff_file_hashes({"a.py": "1"}, {"a.py": "2"})
    assert delta.modified == ["a.py"]
    assert delta.added == []


def test_deleted_file_is_classified():
    delta = diff_file_hashes({"a.py": "1", "b.py": "2"}, {"a.py": "1"})
    assert delta.deleted == ["b.py"]


def test_rename_is_detected_by_content_not_reported_as_add_plus_delete():
    delta = diff_file_hashes({"old.py": "same"}, {"new.py": "same"})
    assert delta.renamed == {"old.py": "new.py"}
    assert delta.added == []
    assert delta.deleted == []


def test_rename_with_an_edit_is_an_add_and_a_delete():
    """Content changed too, so there is no evidence the files are the same one."""
    delta = diff_file_hashes({"old.py": "before"}, {"new.py": "after"})
    assert delta.renamed == {}
    assert delta.added == ["new.py"]
    assert delta.deleted == ["old.py"]


def test_one_deleted_file_is_consumed_by_only_one_rename():
    delta = diff_file_hashes({"old.py": "same"}, {"n1.py": "same", "n2.py": "same"})
    assert len(delta.renamed) == 1
    assert len(delta.added) == 1


def test_touched_collects_added_modified_and_rename_targets():
    delta = diff_file_hashes({"m.py": "1", "o.py": "x"}, {"m.py": "2", "n.py": "x", "a.py": "9"})
    assert delta.touched == ["a.py", "m.py", "n.py"]


# -- build modes -----------------------------------------------------------


def test_first_build_is_a_full_rebuild(repo):
    index = index_repository(repo, ROOTS)
    assert index.metrics.full_rebuild
    assert index.metrics.cache_misses == 1
    assert index.metrics.incremental_updates == 0


def test_unchanged_repository_is_a_cache_hit_with_no_rebuild(repo):
    first = index_repository(repo, ROOTS)
    second = index_repository(repo, ROOTS, cached=first)
    assert second.metrics.cache_hits == 1
    assert second.metrics.graph_build_ms == 0
    assert second.delta.is_empty


def test_small_change_takes_the_incremental_path(repo):
    first = index_repository(repo, ROOTS)
    (repo / "pkg" / "c.py").write_text("def delta():\n    return 1\n")
    second = index_repository(repo, ROOTS, cached=first)

    assert second.metrics.incremental_updates == 1
    assert not second.metrics.full_rebuild
    assert second.delta.added == ["pkg/c.py"]


def test_large_change_falls_back_to_a_full_rebuild(repo):
    first = index_repository(repo, ROOTS)
    for name in ("a.py", "b.py", "__init__.py"):
        (repo / "pkg" / name).write_text(f"# rewritten {name}\n")
    second = index_repository(repo, ROOTS, cached=first)

    touched = len(second.delta.modified)
    assert touched / len(second.repository_graph.files) > FULL_REBUILD_THRESHOLD
    assert second.metrics.full_rebuild


def test_incremental_result_matches_full_rebuild(repo):
    """The guarantee that makes incremental indexing safe."""
    first = index_repository(repo, ROOTS)
    (repo / "pkg" / "c.py").write_text("def delta():\n    return beta(1)\n")

    incremental = index_repository(repo, ROOTS, cached=first)
    full = index_repository(repo, ROOTS)

    assert incremental.metrics.incremental_updates == 1
    assert fingerprint(incremental) == fingerprint(full)


def test_incremental_after_a_modification_matches_full_rebuild(repo):
    first = index_repository(repo, ROOTS)
    (repo / "pkg" / "a.py").write_text(MOD_A + "\n\ndef added_later():\n    return beta(0)\n")

    incremental = index_repository(repo, ROOTS, cached=first)
    assert fingerprint(incremental) == fingerprint(index_repository(repo, ROOTS))


def test_incremental_after_a_deletion_matches_full_rebuild(repo):
    (repo / "pkg" / "c.py").write_text("def temp():\n    return 1\n")
    first = index_repository(repo, ROOTS)
    (repo / "pkg" / "c.py").unlink()

    incremental = index_repository(repo, ROOTS, cached=first)
    assert incremental.delta.deleted == ["pkg/c.py"]
    assert fingerprint(incremental) == fingerprint(index_repository(repo, ROOTS))


def test_incremental_after_a_rename_matches_full_rebuild(repo):
    first = index_repository(repo, ROOTS)
    (repo / "pkg" / "b.py").rename(repo / "pkg" / "renamed.py")

    incremental = index_repository(repo, ROOTS, cached=first)
    assert incremental.delta.renamed == {"pkg/b.py": "pkg/renamed.py"}
    assert fingerprint(incremental) == fingerprint(index_repository(repo, ROOTS))


def test_deleted_file_drops_out_of_the_parsed_map(repo):
    (repo / "pkg" / "c.py").write_text("def temp():\n    return 1\n")
    first = index_repository(repo, ROOTS)
    assert "pkg/c.py" in first.parsed_modules

    (repo / "pkg" / "c.py").unlink()
    second = index_repository(repo, ROOTS, cached=first)
    assert "pkg/c.py" not in second.parsed_modules


# -- content ---------------------------------------------------------------


def test_index_populates_every_subgraph(repo):
    index = index_repository(repo, ROOTS)
    assert index.repository_graph.nodes
    assert index.call_graph.nodes
    assert index.documentation.entries
    assert index.parsed_modules


def test_metrics_are_counted(repo):
    metrics = index_repository(repo, ROOTS).metrics
    assert metrics.repository_nodes == len(index_repository(repo, ROOTS).repository_graph.nodes)
    assert metrics.call_graph_nodes > 0
    assert metrics.documentation_entries > 0
    assert metrics.index_size > 0
    assert metrics.total_ms >= 0


def test_documentation_is_indexed_alongside_code(repo):
    index = index_repository(repo, ROOTS)
    assert any(e.path == "README.md" for e in index.documentation.entries)


def test_doc_only_change_is_tracked_in_the_delta(repo):
    first = index_repository(repo, ROOTS)
    (repo / "README.md").write_text("# Repo\n\nRewritten.\n")
    second = index_repository(repo, ROOTS, cached=first)
    assert "README.md" in second.delta.modified


def test_max_files_caps_the_index(repo):
    index = index_repository(repo, ROOTS, max_files=1)
    assert len(index.repository_graph.files) == 1


def test_non_git_repository_indexes_without_history(repo):
    index = index_repository(repo, ROOTS)
    assert index.history.commits == []
    assert index.ownership.files == {}
    assert index.repository_graph.nodes


def test_repository_id_is_carried_through(repo):
    index = index_repository(repo, ROOTS, repository_id="explicit-id")
    assert index.repository_id == "explicit-id"


def test_build_is_deterministic(repo):
    assert fingerprint(index_repository(repo, ROOTS)) == fingerprint(index_repository(repo, ROOTS))
