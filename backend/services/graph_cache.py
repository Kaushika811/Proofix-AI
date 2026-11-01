"""Process-local cache for built knowledge graphs.

The graph is derived data: it can always be rebuilt from a `RepositoryIntelligence`
in tens of milliseconds, and storing it in Redis would duplicate the very
structures the RKG exists to unify. So it is cached in-process instead, keyed by
the repository hash the index was built at.

That key is what makes the cache correct. `repository_hash` already folds in HEAD
plus the worktree diff hash, so any file change — including one A7 writes
mid-run — produces a different key and a rebuild. There is no invalidation to
get wrong, because there is no mutation: an entry is either for the current
repository state or it is not reachable.

Bounded to a small number of entries. A server handling many repositories should
hold the few most recently used, not every graph it has ever built.
"""

from __future__ import annotations

from collections import OrderedDict

from backend.models.repository_graph import RepositoryIntelligence
from backend.services.knowledge_graph import (
    RepositoryKnowledgeGraph,
    build_knowledge_graph,
)

MAX_CACHED_GRAPHS = 8

_cache: OrderedDict[str, RepositoryKnowledgeGraph] = OrderedDict()
_hits = 0
_misses = 0


def cache_key(intelligence: RepositoryIntelligence) -> str:
    """Identity of the repository state a graph was built from."""
    return f"{intelligence.repository_id}:{intelligence.repository_hash}"


def get_knowledge_graph(
    intelligence: RepositoryIntelligence,
    rebuild: bool = False,
) -> RepositoryKnowledgeGraph:
    """Return a graph for this index, building it only when needed."""
    global _hits, _misses

    key = cache_key(intelligence)
    if not rebuild and key in _cache:
        _cache.move_to_end(key)
        graph = _cache[key]
        _hits += 1
        graph.metrics.cache_hits = _hits
        graph.metrics.cache_misses = _misses
        return graph

    graph = build_knowledge_graph(intelligence)
    _misses += 1
    graph.metrics.cache_hits = _hits
    graph.metrics.cache_misses = _misses

    _cache[key] = graph
    _cache.move_to_end(key)
    while len(_cache) > MAX_CACHED_GRAPHS:
        _cache.popitem(last=False)

    return graph


def cache_stats() -> dict:
    total = _hits + _misses
    return {
        "entries": len(_cache),
        "hits": _hits,
        "misses": _misses,
        "hit_rate": round(_hits / total, 4) if total else 0.0,
        "capacity": MAX_CACHED_GRAPHS,
    }


def clear_cache() -> None:
    """Drop every entry. For tests and for a deliberate server-side reset."""
    global _hits, _misses
    _cache.clear()
    _hits = 0
    _misses = 0
