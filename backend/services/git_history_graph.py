"""Mine local git history into a commit graph, file evolution, and co-change.

Local git only — no GitHub API, no network. Every figure here is derived from
`git log` on the clone the pipeline already made.

Co-change frequency is the signal worth the effort: files that are repeatedly
modified in the same commit are coupled in a way neither the import graph nor
the call graph shows, and that coupling is exactly what a repair is at risk of
breaking.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from git import Repo

from backend.models.repository_graph import (
    CommitRecord,
    FileEvolution,
    GitHistoryGraph,
)

# Commit-message markers that identify a bug-fix commit. Matched case-insensitively
# against the summary line.
FIX_KEYWORDS = ("fix", "bug", "patch", "hotfix", "repair", "regression", "revert")

DEFAULT_WINDOW_DAYS = 180

# Commits touching more than this many files are refactors, releases or vendored
# imports. Counting them as co-change would couple everything to everything.
MAX_FILES_FOR_CO_CHANGE = 25

# Hard cap on commits walked, so a repository with deep history cannot dominate
# the pipeline's wall-clock budget.
MAX_COMMITS = 2000


def is_fix_commit(message: str) -> bool:
    summary = (message or "").strip().splitlines()[0].lower() if message else ""
    return any(keyword in summary for keyword in FIX_KEYWORDS)


def _summary(message: str) -> str:
    return (message or "").strip().splitlines()[0][:200] if message else ""


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromtimestamp(float(value))
    except (TypeError, ValueError, OSError):
        return None


def build_git_history_graph(
    repo_path: Path,
    window_days: int = DEFAULT_WINDOW_DAYS,
    max_commits: int = MAX_COMMITS,
) -> GitHistoryGraph:
    """Walk recent history into a `GitHistoryGraph`.

    Returns an empty graph rather than raising when the path is not a repository
    — a repository under repair may legitimately have no git metadata.
    """
    graph = GitHistoryGraph(window_days=window_days)

    try:
        repo = Repo(repo_path)
        since = datetime.now() - timedelta(days=window_days)
        commits = list(repo.iter_commits(since=since.isoformat(), max_count=max_commits))
    except Exception:  # noqa: BLE001 — absent or broken git is a normal input
        return graph

    fix_counts: dict[str, int] = {}
    commit_counts: dict[str, int] = {}

    for commit in commits:
        try:
            files = sorted(commit.stats.files)
        except Exception:  # noqa: BLE001 — unreadable commit, skip it
            continue

        committed_at = _as_datetime(getattr(commit, "committed_datetime", None))
        is_fix = is_fix_commit(commit.message)

        graph.commits.append(
            CommitRecord(
                sha=commit.hexsha,
                author=str(getattr(commit.author, "name", "") or ""),
                committed_at=committed_at,
                message_summary=_summary(commit.message),
                files=files,
                is_fix=is_fix,
            )
        )

        for path in files:
            normalized = str(path).replace("\\", "/")
            commit_counts[normalized] = commit_counts.get(normalized, 0) + 1
            if is_fix:
                fix_counts[normalized] = fix_counts.get(normalized, 0) + 1

            entry = graph.evolution.get(normalized)
            if entry is None:
                entry = FileEvolution(file=normalized)
                graph.evolution[normalized] = entry
            entry.commit_count += 1
            if is_fix:
                entry.fix_commit_count += 1
            if committed_at:
                if entry.last_seen is None or committed_at > entry.last_seen:
                    entry.last_seen = committed_at
                if entry.first_seen is None or committed_at < entry.first_seen:
                    entry.first_seen = committed_at

        if 1 < len(files) <= MAX_FILES_FOR_CO_CHANGE:
            _record_co_change(graph, [str(f).replace("\\", "/") for f in files])

    _normalize_churn(graph, fix_counts)
    _detect_renames_and_deletions(repo_path, graph)
    return graph


def _record_co_change(graph: GitHistoryGraph, files: list[str]) -> None:
    for left in files:
        partners = graph.co_change.setdefault(left, {})
        for right in files:
            if left == right:
                continue
            partners[right] = partners.get(right, 0) + 1


def _normalize_churn(graph: GitHistoryGraph, fix_counts: dict[str, int]) -> None:
    """Churn is bug-fix commit density, normalized to the busiest file.

    Deliberately the same definition `git_service.get_churn_weights` uses, so the
    SIG and the history graph never disagree about which files are churny.
    """
    if not fix_counts:
        return
    peak = max(fix_counts.values())
    if peak <= 0:
        return
    for file, count in fix_counts.items():
        entry = graph.evolution.get(file)
        if entry is not None:
            entry.churn = round(min(1.0, count / peak), 4)


def _detect_renames_and_deletions(repo_path: Path, graph: GitHistoryGraph) -> None:
    """Record rename chains and deletions using git's own rename detection."""
    try:
        repo = Repo(repo_path)
        raw = repo.git.log(
            "--diff-filter=RD",
            "--name-status",
            "-M",
            f"--max-count={MAX_COMMITS}",
            "--format=",
        )
    except Exception:  # noqa: BLE001
        return

    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0].strip()

        if status.startswith("R") and len(parts) >= 3:
            old_path = parts[1].replace("\\", "/")
            new_path = parts[2].replace("\\", "/")
            graph.renames[old_path] = new_path
            entry = graph.evolution.get(new_path)
            if entry is not None and old_path not in entry.previous_paths:
                entry.previous_paths.append(old_path)
        elif status.startswith("D"):
            deleted = parts[1].replace("\\", "/")
            if deleted not in graph.deletions:
                graph.deletions.append(deleted)
            entry = graph.evolution.get(deleted)
            if entry is not None:
                entry.deleted = True


def co_change_score(graph: GitHistoryGraph, file: str, other: str) -> float:
    """0..1 — how often two files change together, relative to `file`'s history.

    Answers "when this file changes, how often does that one change with it?"
    """
    partners = graph.co_change.get(file, {})
    if not partners:
        return 0.0
    together = partners.get(other, 0)
    if together <= 0:
        return 0.0
    entry = graph.evolution.get(file)
    total = entry.commit_count if entry and entry.commit_count else max(partners.values())
    return round(min(1.0, together / total), 4) if total else 0.0
