"""Build and incrementally maintain the repository index.

The index is expensive in exactly two places: reading and AST-parsing every
source file, and walking git history. Everything else — assembling the repository
graph, resolving the call graph, aggregating ownership — is in-memory work over
data those two steps produced.

So that is what the incremental path skips. Given a cached index it:

1. hashes the current file set and diffs it against the cached hashes,
2. re-parses only added, modified and renamed files,
3. reuses the cached git history and ownership when HEAD has not moved,
4. reassembles the graphs from the merged parsed-module map.

Step 4 is a full reassembly on purpose. Call resolution and cross-file
`references` edges are global: a symbol added to one file can change how an
untouched file's calls resolve. Patching nodes in place would produce an index
that quietly disagrees with a full rebuild, and a stale call graph feeds A5.5's
fan-in ranking. Reassembly costs no I/O, so the saving from steps 1–3 is
preserved and `test_incremental_matches_full_rebuild` holds the guarantee.

Rename detection is content-based: a path that disappeared and a path that
appeared carrying the same content hash are the same file moved. That keeps a
rename off the re-parse list entirely.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from backend.models.repository_graph import (
    IndexDelta,
    RepairMemory,
    RepositoryIntelligence,
    RepositoryIntelligenceMetrics,
)
from backend.services.call_graph import build_call_graph
from backend.services.documentation_index import (
    build_documentation_index,
    discover_documentation_files,
)
from backend.services.git_history_graph import DEFAULT_WINDOW_DAYS, build_git_history_graph
from backend.services.git_service import get_head_sha
from backend.services.ownership_graph import build_ownership_graph
from backend.services.python_ast_parser import ParsedModule, parse_source
from backend.services.repair_memory import repository_id as compute_repository_id
from backend.services.repository_cache import index_size_bytes
from backend.services.repository_graph import (
    build_module_index,
    build_repository_graph,
    discover_python_files,
    is_under,
)
from backend.services.sig_cache import compute_repo_hash
from backend.services.workspace_layout import (
    detect_workspace,
    source_roots_for_workspace,
)

# Above this share of files touched, an incremental pass saves little and costs
# the extra bookkeeping — rebuild instead.
FULL_REBUILD_THRESHOLD = 0.5

# Hard ceiling on indexed files. A repository larger than this is indexed up to
# the cap rather than being allowed to dominate the pipeline's time budget.
DEFAULT_MAX_FILES = 3000


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def hash_files(repo_path: Path, files: list[str]) -> dict[str, str]:
    """Content hash per repo-relative path. Unreadable files are omitted."""
    repo = repo_path.resolve()
    hashes: dict[str, str] = {}
    for rel in files:
        try:
            hashes[rel] = _hash_text((repo / rel).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    return hashes


def diff_file_hashes(previous: dict[str, str], current: dict[str, str]) -> IndexDelta:
    """Classify every change between two hash maps, detecting renames by content."""
    delta = IndexDelta()

    added = sorted(set(current) - set(previous))
    deleted = sorted(set(previous) - set(current))
    common = sorted(set(previous) & set(current))

    delta.modified = [path for path in common if previous[path] != current[path]]
    delta.unchanged = len(common) - len(delta.modified)

    # A vanished path and a new path with identical content is one file moved.
    deleted_by_hash: dict[str, list[str]] = {}
    for path in deleted:
        deleted_by_hash.setdefault(previous[path], []).append(path)

    consumed_old: set[str] = set()
    remaining_added: list[str] = []

    for path in added:
        candidates = deleted_by_hash.get(current[path], [])
        available = [c for c in candidates if c not in consumed_old]
        if available:
            old_path = available[0]
            consumed_old.add(old_path)
            delta.renamed[old_path] = path
        else:
            remaining_added.append(path)

    delta.added = remaining_added
    delta.deleted = [path for path in deleted if path not in consumed_old]
    return delta


def _parse_files(repo_path: Path, files: list[str]) -> dict[str, ParsedModule]:
    repo = repo_path.resolve()
    parsed: dict[str, ParsedModule] = {}
    for rel in files:
        try:
            source = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        module = parse_source(source)
        if module is not None:
            parsed[rel] = module
    return parsed


def _should_rebuild(cached: RepositoryIntelligence | None, delta: IndexDelta, total: int) -> bool:
    if cached is None or not cached.parsed_modules:
        return True
    if total <= 0:
        return True
    touched = len(delta.added) + len(delta.modified) + len(delta.deleted) + len(delta.renamed)
    return (touched / total) > FULL_REBUILD_THRESHOLD


def index_repository(
    repo_path: Path,
    source_roots: list[str],
    cached: RepositoryIntelligence | None = None,
    *,
    history_window_days: int = DEFAULT_WINDOW_DAYS,
    max_files: int = DEFAULT_MAX_FILES,
    repository_id: str | None = None,
) -> RepositoryIntelligence:
    """Produce a current index, reusing `cached` wherever it is still valid.

    Returns a fresh `RepositoryIntelligence` in every case; the caller decides
    whether to persist it. Repair memory is not touched here — it is owned by the
    cache, not by the index build.
    """
    t_start = time.monotonic()
    repo = repo_path.resolve()

    # Layout first: a nested repository must be excluded before anything is
    # discovered, or its files enter the index and its history is attributed
    # to this repository.
    layout = detect_workspace(repo)
    excluded = layout.excluded_directories()
    effective_roots = source_roots_for_workspace(layout, list(source_roots))

    python_files = discover_python_files(repo, effective_roots, exclude_dirs=excluded)[:max_files]
    doc_files = [d for d in discover_documentation_files(repo) if not is_under(d, excluded)]
    tracked = python_files + [d for d in doc_files if d not in set(python_files)]

    current_hashes = hash_files(repo, tracked)
    repo_hash = compute_repo_hash(repo, source_roots)
    head_sha = get_head_sha(repo)
    repo_id = repository_id or compute_repository_id(repo)

    delta = diff_file_hashes(cached.file_hashes if cached else {}, current_hashes)

    metrics = RepositoryIntelligenceMetrics(
        files_added=len(delta.added),
        files_deleted=len(delta.deleted),
        files_modified=len(delta.modified),
        files_renamed=len(delta.renamed),
    )

    # Nothing moved and the repository hash agrees: the cached index is current.
    if cached is not None and delta.is_empty and cached.repository_hash == repo_hash:
        metrics.cache_hits = 1
        metrics.full_rebuild = False
        metrics.total_ms = int((time.monotonic() - t_start) * 1000)
        return _finalize(_reuse(cached, delta, metrics), t_start)

    metrics.cache_misses = 1
    full_rebuild = _should_rebuild(cached, delta, len(python_files))
    metrics.full_rebuild = full_rebuild

    # -- parse -----------------------------------------------------------
    python_set = set(python_files)
    if full_rebuild:
        parsed_modules = _parse_files(repo, python_files)
    else:
        metrics.incremental_updates = 1
        assert cached is not None
        parsed_modules = {
            path: module
            for path, module in cached.parsed_modules.items()
            if path in python_set
        }
        for old_path, new_path in delta.renamed.items():
            moved = parsed_modules.pop(old_path, None)
            if moved is not None and new_path in python_set:
                parsed_modules[new_path] = moved
        for path in delta.deleted:
            parsed_modules.pop(path, None)

        stale = [p for p in (delta.added + delta.modified) if p in python_set]
        missing = [p for p in python_files if p not in parsed_modules]
        parsed_modules.update(_parse_files(repo, sorted(set(stale + missing))))

    # Preserve discovery order so graph assembly is byte-stable.
    parsed_modules = {path: parsed_modules[path] for path in python_files if path in parsed_modules}

    # -- graphs ----------------------------------------------------------
    t_graph = time.monotonic()
    repository_graph, parsed_modules = build_repository_graph(
        repo, effective_roots, parsed_modules=parsed_modules, files=python_files
    )
    metrics.graph_build_ms = int((time.monotonic() - t_graph) * 1000)

    t_call = time.monotonic()
    call_graph = build_call_graph(parsed_modules, build_module_index(python_files))
    metrics.call_graph_ms = int((time.monotonic() - t_call) * 1000)

    # -- history and ownership -------------------------------------------
    reuse_history = (
        cached is not None
        and bool(head_sha)
        and cached.head_sha == head_sha
        and bool(cached.history.commits)
    )
    t_history = time.monotonic()
    if reuse_history:
        assert cached is not None
        history = cached.history
        metrics.history_ms = 0
    else:
        history = build_git_history_graph(repo, window_days=history_window_days)
        metrics.history_ms = int((time.monotonic() - t_history) * 1000)

    t_own = time.monotonic()
    ownership = cached.ownership if reuse_history and cached else build_ownership_graph(history)
    metrics.ownership_ms = 0 if reuse_history else int((time.monotonic() - t_own) * 1000)

    # -- documentation ----------------------------------------------------
    t_docs = time.monotonic()
    documentation = build_documentation_index(repo, python_files, parsed_modules)
    metrics.documentation_ms = int((time.monotonic() - t_docs) * 1000)

    index = RepositoryIntelligence(
        repository_hash=repo_hash,
        repository_id=repo_id,
        head_sha=head_sha,
        source_roots=list(effective_roots),
        workspace=layout,
        repository_graph=repository_graph,
        call_graph=call_graph,
        ownership=ownership,
        history=history,
        documentation=documentation,
        # Carried through untouched: repair memory is owned by the cache, and the
        # agent overwrites this with the authoritative copy after the build.
        repair_memory=cached.repair_memory if cached else RepairMemory(repository_id=repo_id),
        parsed_modules=parsed_modules,
        file_hashes=current_hashes,
        delta=delta,
        metrics=metrics,
    )
    return _finalize(index, t_start)


def _reuse(
    cached: RepositoryIntelligence,
    delta: IndexDelta,
    metrics: RepositoryIntelligenceMetrics,
) -> RepositoryIntelligence:
    """Return the cached index with this pass's delta and metrics attached."""
    index = cached.model_copy(deep=True)
    index.delta = delta
    index.metrics = metrics
    return index


def _finalize(index: RepositoryIntelligence, t_start: float) -> RepositoryIntelligence:
    """Fill in the counting metrics every path shares."""
    metrics = index.metrics
    metrics.repository_nodes = len(index.repository_graph.nodes)
    metrics.repository_edges = len(index.repository_graph.edges)
    metrics.call_graph_nodes = len(index.call_graph.nodes)
    metrics.call_graph_edges = len(index.call_graph.call_sites)
    metrics.ownership_entries = len(index.ownership.files)
    metrics.documentation_entries = len(index.documentation.entries)
    metrics.repair_memory_entries = len(index.repair_memory.records)
    metrics.git_commits_indexed = len(index.history.commits)
    metrics.index_size = index_size_bytes(index)
    metrics.total_ms = int((time.monotonic() - t_start) * 1000)
    return index
