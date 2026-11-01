"""Derive file ownership from local git history.

No GitHub API, no CODEOWNERS parsing, no network. Ownership here means "who has
actually been changing this file lately", which is what matters when deciding
whether a repair touches well-understood or orphaned code.

`ownership_confidence` measures how *concentrated* ownership is, not how
trustworthy the author is: a file with one author across many commits scores
high; a file touched once by each of six people scores low. Low confidence is a
review signal, not a defect signal.
"""

from __future__ import annotations

from datetime import datetime

from backend.models.repository_graph import (
    FileOwnership,
    GitHistoryGraph,
    OwnershipGraph,
)

# Commits within this window count as "recent modifications".
RECENT_WINDOW_DAYS = 30

# A file needs at least this many commits before concentration means anything.
MIN_COMMITS_FOR_CONFIDENCE = 2

# Files in the top share of fix-commit churn are flagged hot.
HOT_CHURN_THRESHOLD = 0.5


def build_ownership_graph(
    history: GitHistoryGraph,
    now: datetime | None = None,
    recent_window_days: int = RECENT_WINDOW_DAYS,
) -> OwnershipGraph:
    """Aggregate per-file authorship from an already-built history graph.

    Takes the history graph rather than a repository path so the git log is
    walked exactly once per index build.
    """
    reference = now or datetime.utcnow()
    graph = OwnershipGraph(window_days=history.window_days)

    author_counts: dict[str, dict[str, int]] = {}
    recent_counts: dict[str, int] = {}
    last_modified: dict[str, datetime] = {}

    for commit in history.commits:
        author = commit.author or "unknown"
        graph.authors[author] = graph.authors.get(author, 0) + 1
        is_recent = (
            commit.committed_at is not None
            and (reference - commit.committed_at).days <= recent_window_days
        )

        for file in commit.files:
            counts = author_counts.setdefault(file, {})
            counts[author] = counts.get(author, 0) + 1
            if is_recent:
                recent_counts[file] = recent_counts.get(file, 0) + 1
            if commit.committed_at is not None:
                current = last_modified.get(file)
                if current is None or commit.committed_at > current:
                    last_modified[file] = commit.committed_at

    for file, counts in author_counts.items():
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(counts.values())

        primary, primary_count = ranked[0]
        secondary, secondary_count = ranked[1] if len(ranked) > 1 else ("", 0)

        modified_at = last_modified.get(file)
        evolution = history.evolution.get(file)

        graph.files[file] = FileOwnership(
            file=file,
            primary_author=primary,
            primary_author_share=round(primary_count / total, 4) if total else 0.0,
            secondary_author=secondary,
            secondary_author_share=round(secondary_count / total, 4) if total else 0.0,
            commit_count=total,
            author_counts=dict(ranked),
            recent_modifications=recent_counts.get(file, 0),
            last_modified=modified_at,
            days_since_modified=(reference - modified_at).days if modified_at else None,
            is_hot=bool(evolution and evolution.churn >= HOT_CHURN_THRESHOLD),
            ownership_confidence=_confidence(primary_count, total),
        )

    graph.hot_files = sorted(f for f, entry in graph.files.items() if entry.is_hot)
    return graph


def _confidence(primary_count: int, total: int) -> float:
    """How concentrated ownership is, damped for thin history.

    A single commit by a single author is 100% concentrated but proves nothing,
    so the score is scaled by how much history backs it.
    """
    if total <= 0:
        return 0.0
    share = primary_count / total
    if total < MIN_COMMITS_FOR_CONFIDENCE:
        return round(share * 0.5, 4)
    evidence = min(1.0, total / 10.0)
    return round(share * (0.5 + 0.5 * evidence), 4)


def ownership_signal(graph: OwnershipGraph, file: str) -> float:
    """0..1 ranking signal: how well-maintained a file appears.

    Recently touched, concentrated ownership scores high. Used only to break ties
    in context ranking — never to decide whether a file is the defect.
    """
    entry = graph.files.get(file)
    if entry is None:
        return 0.0

    score = entry.ownership_confidence * 0.6
    if entry.recent_modifications:
        score += 0.2
    if entry.days_since_modified is not None and entry.days_since_modified <= RECENT_WINDOW_DAYS:
        score += 0.2
    return round(min(1.0, score), 4)
