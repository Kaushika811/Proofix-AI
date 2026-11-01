"""Cross-run persistence for the Repository Intelligence Layer.

Two things are cached, on deliberately different lifetimes:

* **The index** — repository graph, call graph, ownership, history and docs — is
  keyed by `repository_id` and carries the `repository_hash` it was built at. It
  is *not* keyed by the hash, because an incremental update needs the previous
  index in order to diff against it. The hash lives inside the payload, and the
  indexer compares it to decide between reuse, incremental update, and rebuild.

* **Repair memory** is keyed by `repository_id` alone and outlives any commit.
  A repair made three commits ago is still a fact about this repository.

Every operation degrades to a miss rather than raising: an unavailable or
malformed cache must never fail a run.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models.repository_graph import RepairMemory, RepositoryIntelligence

INDEX_NAMESPACE = "repo_intel"
REPAIR_NAMESPACE = "repair_memory"

# Bump on any change to the stored shape. A version mismatch reads as a miss, so
# a stale payload can never be deserialized into the current models.
CACHE_VERSION = "v1"

# Repair memory is not commit-scoped; give it a year rather than the run TTL.
REPAIR_MEMORY_TTL_SECONDS = 365 * 24 * 3600


@dataclass
class RepositoryCacheMetrics:
    hits: int = 0
    misses: int = 0
    writes: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return round(self.hits / self.total, 4) if self.total else 0.0

    def to_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "hit_rate": self.hit_rate,
        }


class RepositoryCache:
    """Async wrapper over the store's namespaced cache."""

    def __init__(self, store, ttl_seconds: int | None = None, version: str = CACHE_VERSION):
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.version = version
        self.metrics = RepositoryCacheMetrics()

    # -- index ----------------------------------------------------------

    async def load_index(self, repository_id: str) -> RepositoryIntelligence | None:
        if not repository_id:
            return None
        try:
            raw = await self.store.get_cached(INDEX_NAMESPACE, self.version, repository_id)
        except Exception:  # noqa: BLE001 — an unavailable cache is a miss
            self.metrics.misses += 1
            return None
        if not raw:
            self.metrics.misses += 1
            return None
        try:
            index = RepositoryIntelligence.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — a stale shape is a miss, not an error
            self.metrics.misses += 1
            return None
        self.metrics.hits += 1
        return index

    async def save_index(self, index: RepositoryIntelligence) -> None:
        if not index.repository_id:
            return
        try:
            await self.store.set_cached(
                INDEX_NAMESPACE,
                self.version,
                index.repository_id,
                index.model_dump_json(),
                self.ttl_seconds,
            )
        except Exception:  # noqa: BLE001 — best effort
            return
        self.metrics.writes += 1

    async def invalidate_index(self, repository_id: str) -> None:
        try:
            await self.store.delete_cached(INDEX_NAMESPACE, self.version, repository_id)
        except Exception:  # noqa: BLE001
            return

    # -- repair memory --------------------------------------------------

    async def load_repair_memory(self, repository_id: str) -> RepairMemory:
        """Always returns a memory — an empty one when nothing is stored."""
        if not repository_id:
            return RepairMemory()
        try:
            raw = await self.store.get_cached(REPAIR_NAMESPACE, self.version, repository_id)
        except Exception:  # noqa: BLE001
            return RepairMemory(repository_id=repository_id)
        if not raw:
            return RepairMemory(repository_id=repository_id)
        try:
            return RepairMemory.model_validate_json(raw)
        except Exception:  # noqa: BLE001
            return RepairMemory(repository_id=repository_id)

    async def save_repair_memory(self, memory: RepairMemory) -> None:
        if not memory.repository_id:
            return
        try:
            await self.store.set_cached(
                REPAIR_NAMESPACE,
                self.version,
                memory.repository_id,
                memory.model_dump_json(),
                REPAIR_MEMORY_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001 — best effort
            return


def index_size_bytes(index: RepositoryIntelligence) -> int:
    """Serialized size, reported as the `index_size` metric."""
    try:
        return len(index.model_dump_json().encode("utf-8"))
    except Exception:  # noqa: BLE001
        return 0
