"""Ownership derived from history: concentration, recency, and confidence damping."""

from datetime import datetime, timedelta

import pytest

from backend.models.repository_graph import (
    CommitRecord,
    FileEvolution,
    GitHistoryGraph,
)
from backend.services.ownership_graph import (
    build_ownership_graph,
    ownership_signal,
)

NOW = datetime(2026, 8, 6)


def history(*commits: CommitRecord, **kwargs) -> GitHistoryGraph:
    graph = GitHistoryGraph(commits=list(commits), **kwargs)
    return graph


def c(author: str, files: list[str], days_ago: int = 1, sha: str = "") -> CommitRecord:
    return CommitRecord(
        sha=sha or f"{author}-{days_ago}-{'-'.join(files)}",
        author=author,
        committed_at=NOW - timedelta(days=days_ago),
        files=files,
    )


def test_single_author_is_primary_with_full_share():
    graph = build_ownership_graph(history(c("Ada", ["a.py"]), c("Ada", ["a.py"])), now=NOW)
    entry = graph.files["a.py"]
    assert entry.primary_author == "Ada"
    assert entry.primary_author_share == 1.0
    assert entry.secondary_author == ""


def test_secondary_author_is_ranked_second():
    graph = build_ownership_graph(
        history(
            c("Ada", ["a.py"], 1),
            c("Ada", ["a.py"], 2),
            c("Grace", ["a.py"], 3),
        ),
        now=NOW,
    )
    entry = graph.files["a.py"]
    assert entry.primary_author == "Ada"
    assert entry.secondary_author == "Grace"
    assert entry.primary_author_share == pytest.approx(2 / 3, abs=0.001)
    assert entry.secondary_author_share == pytest.approx(1 / 3, abs=0.001)


def test_commit_count_and_author_counts():
    graph = build_ownership_graph(
        history(c("Ada", ["a.py"], 1), c("Grace", ["a.py"], 2)), now=NOW
    )
    entry = graph.files["a.py"]
    assert entry.commit_count == 2
    assert entry.author_counts == {"Ada": 1, "Grace": 1}


def test_repository_wide_author_totals():
    graph = build_ownership_graph(
        history(c("Ada", ["a.py"], 1), c("Ada", ["b.py"], 2), c("Grace", ["a.py"], 3)),
        now=NOW,
    )
    assert graph.authors == {"Ada": 2, "Grace": 1}


def test_confidence_is_damped_for_thin_history():
    """One commit is 100% concentrated but proves nothing."""
    thin = build_ownership_graph(history(c("Ada", ["a.py"])), now=NOW)
    thick = build_ownership_graph(
        history(*[c("Ada", ["a.py"], i) for i in range(1, 11)]), now=NOW
    )
    assert thin.confidence_for("a.py") == 0.5
    assert thick.confidence_for("a.py") == 1.0
    assert thin.confidence_for("a.py") < thick.confidence_for("a.py")


def test_split_ownership_scores_lower_than_concentrated():
    concentrated = build_ownership_graph(
        history(*[c("Ada", ["a.py"], i) for i in range(1, 7)]), now=NOW
    )
    split = build_ownership_graph(
        history(*[c(f"Dev{i}", ["a.py"], i) for i in range(1, 7)]), now=NOW
    )
    assert concentrated.confidence_for("a.py") > split.confidence_for("a.py")


def test_confidence_for_unknown_file_is_zero():
    assert build_ownership_graph(history()).confidence_for("nope.py") == 0.0


def test_recent_modifications_respect_the_window():
    graph = build_ownership_graph(
        history(c("Ada", ["a.py"], 1), c("Ada", ["a.py"], 200)), now=NOW
    )
    assert graph.files["a.py"].recent_modifications == 1


def test_last_modified_takes_the_newest_commit():
    graph = build_ownership_graph(
        history(c("Ada", ["a.py"], 50), c("Ada", ["a.py"], 2)), now=NOW
    )
    entry = graph.files["a.py"]
    assert entry.days_since_modified == 2


def test_hot_files_come_from_history_churn():
    hist = history(c("Ada", ["hot.py"]), c("Ada", ["cold.py"]))
    hist.evolution["hot.py"] = FileEvolution(file="hot.py", churn=0.9)
    hist.evolution["cold.py"] = FileEvolution(file="cold.py", churn=0.1)

    graph = build_ownership_graph(hist, now=NOW)
    assert graph.hot_files == ["hot.py"]
    assert graph.files["hot.py"].is_hot
    assert not graph.files["cold.py"].is_hot


def test_missing_author_becomes_unknown():
    graph = build_ownership_graph(
        history(CommitRecord(sha="x", author="", committed_at=NOW, files=["a.py"])), now=NOW
    )
    assert graph.files["a.py"].primary_author == "unknown"


def test_ownership_signal_rewards_recent_concentrated_work():
    active = build_ownership_graph(
        history(*[c("Ada", ["a.py"], i) for i in range(1, 11)]), now=NOW
    )
    stale = build_ownership_graph(
        history(*[c("Ada", ["a.py"], 200 + i) for i in range(1, 11)]), now=NOW
    )
    assert ownership_signal(active, "a.py") > ownership_signal(stale, "a.py")
    assert 0.0 <= ownership_signal(active, "a.py") <= 1.0


def test_ownership_signal_for_unknown_file_is_zero():
    assert ownership_signal(build_ownership_graph(history()), "nope.py") == 0.0


def test_empty_history_produces_empty_graph():
    graph = build_ownership_graph(history())
    assert graph.files == {}
    assert graph.hot_files == []
