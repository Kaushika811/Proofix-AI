"""Git history mining: commit graph, churn, renames, deletions, co-change.

Built against real temporary git repositories rather than mocks — the parsing
being tested is parsing of git's own output, and a mock would only assert that
the fixture matches the code.
"""


import pytest
from git import Actor, Repo

from backend.models.repository_graph import (
    CommitRecord,
    FileEvolution,
    GitHistoryGraph,
)
from backend.services.git_history_graph import (
    build_git_history_graph,
    co_change_score,
    is_fix_commit,
)

AUTHOR = Actor("Ada", "ada@example.com")
OTHER = Actor("Grace", "grace@example.com")


def commit(repo: Repo, message: str, files: dict[str, str], author: Actor = AUTHOR) -> None:
    root = repo.working_tree_dir
    from pathlib import Path

    for name, content in files.items():
        path = Path(root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        repo.index.add([name])
    repo.index.commit(message, author=author, committer=author)


@pytest.fixture
def git_repo(tmp_path):
    repo = Repo.init(tmp_path)
    commit(repo, "Initial commit", {"a.py": "A1\n", "b.py": "B1\n"})
    commit(repo, "fix: correct a and b", {"a.py": "A2\n", "b.py": "B2\n"})
    commit(repo, "fix: another a bug", {"a.py": "A3\n"})
    commit(repo, "docs: readme", {"README.md": "hi\n"}, author=OTHER)
    return repo


def test_is_fix_commit_matches_summary_only():
    assert is_fix_commit("fix: broken login")
    assert is_fix_commit("Revert bad change")
    assert not is_fix_commit("feat: new endpoint")
    # Only the summary line counts.
    assert not is_fix_commit("feat: new endpoint\n\nthis will fix things later")


def test_commits_are_recorded_with_author_and_files(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir)
    assert len(graph.commits) == 4
    latest = graph.commits[0]
    assert latest.author == "Grace"
    assert "README.md" in latest.files


def test_fix_commits_are_flagged(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir)
    assert sum(1 for c in graph.commits if c.is_fix) == 2


def test_file_evolution_counts_commits_and_fixes(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir)
    a = graph.evolution["a.py"]
    assert a.commit_count == 3
    assert a.fix_commit_count == 2
    assert a.first_seen is not None and a.last_seen is not None


def test_churn_is_normalized_to_the_busiest_file(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir)
    assert graph.churn_for("a.py") == 1.0
    assert 0 < graph.churn_for("b.py") < 1.0
    assert graph.churn_for("README.md") == 0.0


def test_churn_for_unknown_file_is_zero(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir)
    assert graph.churn_for("nope.py") == 0.0


def test_co_change_records_files_committed_together(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir)
    assert graph.co_change["a.py"]["b.py"] == 2
    assert graph.co_changed_with("a.py")[0] == ("b.py", 2)


def test_co_change_score_is_relative_to_the_source_file(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir)
    # a.py has 3 commits, 2 of them alongside b.py.
    assert co_change_score(graph, "a.py", "b.py") == pytest.approx(2 / 3, abs=0.01)
    assert co_change_score(graph, "a.py", "README.md") == 0.0
    assert co_change_score(graph, "unknown.py", "b.py") == 0.0


def test_huge_commits_are_excluded_from_co_change(tmp_path):
    repo = Repo.init(tmp_path)
    files = {f"f{i}.py": "x\n" for i in range(40)}
    commit(repo, "chore: vendor everything", files)
    graph = build_git_history_graph(tmp_path)
    assert graph.co_change == {}


def test_rename_is_detected_and_recorded(tmp_path):
    repo = Repo.init(tmp_path)
    commit(repo, "Initial", {"old.py": "content\n"})
    repo.index.move(["old.py", "new.py"])
    repo.index.commit("refactor: move module", author=AUTHOR, committer=AUTHOR)

    graph = build_git_history_graph(tmp_path)
    assert graph.renames.get("old.py") == "new.py"
    assert "old.py" in graph.evolution["new.py"].previous_paths


def test_deletion_is_recorded(tmp_path):
    repo = Repo.init(tmp_path)
    commit(repo, "Initial", {"gone.py": "x\n", "kept.py": "y\n"})
    repo.index.remove(["gone.py"], working_tree=True)
    repo.index.commit("chore: drop module", author=AUTHOR, committer=AUTHOR)

    graph = build_git_history_graph(tmp_path)
    assert "gone.py" in graph.deletions
    assert graph.evolution["gone.py"].deleted


def test_non_repository_returns_empty_graph_without_raising(tmp_path):
    graph = build_git_history_graph(tmp_path / "not-a-repo")
    assert graph.commits == []
    assert graph.evolution == {}


def test_window_excludes_commits_outside_it(git_repo):
    """A cutoff in the future excludes everything.

    `window_days=0` would be ambiguous: git's `--since` resolves to whole
    seconds, so commits created in the same second as the cutoff still match.
    """
    graph = build_git_history_graph(git_repo.working_tree_dir, window_days=-1)
    assert graph.commits == []
    assert graph.evolution == {}


def test_window_is_recorded_on_the_graph(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir, window_days=45)
    assert graph.window_days == 45
    assert len(graph.commits) == 4


def test_max_commits_caps_the_walk(git_repo):
    graph = build_git_history_graph(git_repo.working_tree_dir, max_commits=2)
    assert len(graph.commits) == 2


def test_models_default_cleanly():
    graph = GitHistoryGraph()
    graph.evolution["x.py"] = FileEvolution(file="x.py")
    graph.commits.append(CommitRecord(sha="abc"))
    assert graph.churn_for("x.py") == 0.0
    assert graph.co_changed_with("x.py") == []
