"""Repository cache: round-trips, versioning, and fail-soft behaviour.

Every degradation test asserts the same contract: an unavailable or corrupt cache
reads as a miss, never as an error, because a cache fault must not fail a run.
"""

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.models.repository_graph import (
    RepairMemory,
    RepairRecord,
    RepositoryIntelligence,
)
from backend.services.repository_cache import (
    INDEX_NAMESPACE,
    REPAIR_NAMESPACE,
    RepositoryCache,
    index_size_bytes,
)
from backend.state.redis_store import RedisStore


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisStore(client, Settings(stub_mode=True))
    await client.aclose()


@pytest.fixture
def index() -> RepositoryIntelligence:
    return RepositoryIntelligence(
        repository_id="repo-1",
        repository_hash="hash-a",
        head_sha="abc123",
        source_roots=["pkg/"],
    )


class BrokenStore:
    async def get_cached(self, *args, **kwargs):
        raise RuntimeError("redis down")

    async def set_cached(self, *args, **kwargs):
        raise RuntimeError("redis down")

    async def delete_cached(self, *args, **kwargs):
        raise RuntimeError("redis down")


# -- generic store primitives ---------------------------------------------


@pytest.mark.asyncio
async def test_namespaced_key_shape(store):
    assert store.namespaced_key("ns", "v1", "key") == "ns:v1:key"


@pytest.mark.asyncio
async def test_set_and_get_cached_round_trip(store):
    await store.set_cached("ns", "v1", "key", "payload")
    assert await store.get_cached("ns", "v1", "key") == "payload"


@pytest.mark.asyncio
async def test_delete_cached_removes_the_entry(store):
    await store.set_cached("ns", "v1", "key", "payload")
    await store.delete_cached("ns", "v1", "key")
    assert await store.get_cached("ns", "v1", "key") is None


# -- index -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_round_trips(store, index):
    cache = RepositoryCache(store)
    await cache.save_index(index)
    loaded = await cache.load_index("repo-1")
    assert loaded is not None
    assert loaded.repository_hash == "hash-a"
    assert loaded.head_sha == "abc123"


@pytest.mark.asyncio
async def test_missing_index_is_a_miss(store):
    cache = RepositoryCache(store)
    assert await cache.load_index("absent") is None
    assert cache.metrics.misses == 1
    assert cache.metrics.hits == 0


@pytest.mark.asyncio
async def test_hit_and_write_metrics(store, index):
    cache = RepositoryCache(store)
    await cache.save_index(index)
    await cache.load_index("repo-1")
    assert cache.metrics.hits == 1
    assert cache.metrics.writes == 1
    assert cache.metrics.hit_rate == 1.0


@pytest.mark.asyncio
async def test_hit_rate_with_no_lookups_is_zero(store):
    assert RepositoryCache(store).metrics.hit_rate == 0.0


@pytest.mark.asyncio
async def test_a_different_version_cannot_read_the_entry(store, index):
    await RepositoryCache(store, version="v1").save_index(index)
    assert await RepositoryCache(store, version="v2").load_index("repo-1") is None


@pytest.mark.asyncio
async def test_corrupt_payload_reads_as_a_miss(store):
    await store.set_cached(INDEX_NAMESPACE, "v1", "repo-1", "{not json")
    cache = RepositoryCache(store)
    assert await cache.load_index("repo-1") is None
    assert cache.metrics.misses == 1


@pytest.mark.asyncio
async def test_index_without_an_id_is_not_saved(store):
    cache = RepositoryCache(store)
    await cache.save_index(RepositoryIntelligence())
    assert cache.metrics.writes == 0


@pytest.mark.asyncio
async def test_invalidate_removes_the_index(store, index):
    cache = RepositoryCache(store)
    await cache.save_index(index)
    await cache.invalidate_index("repo-1")
    assert await cache.load_index("repo-1") is None


@pytest.mark.asyncio
async def test_unavailable_store_degrades_to_a_miss(index):
    cache = RepositoryCache(BrokenStore())
    assert await cache.load_index("repo-1") is None
    await cache.save_index(index)  # must not raise
    await cache.invalidate_index("repo-1")  # must not raise


# -- repair memory ---------------------------------------------------------


@pytest.mark.asyncio
async def test_repair_memory_round_trips(store):
    cache = RepositoryCache(store)
    memory = RepairMemory(
        repository_id="repo-1",
        records=[RepairRecord(repair_id="r1", file="mod.py", validation_passed=True)],
    )
    await cache.save_repair_memory(memory)

    loaded = await cache.load_repair_memory("repo-1")
    assert len(loaded.records) == 1
    assert loaded.records[0].repair_id == "r1"


@pytest.mark.asyncio
async def test_absent_repair_memory_is_empty_not_none(store):
    memory = await RepositoryCache(store).load_repair_memory("nobody")
    assert isinstance(memory, RepairMemory)
    assert memory.records == []
    assert memory.repository_id == "nobody"


@pytest.mark.asyncio
async def test_corrupt_repair_memory_degrades_to_empty(store):
    await store.set_cached(REPAIR_NAMESPACE, "v1", "repo-1", "garbage")
    memory = await RepositoryCache(store).load_repair_memory("repo-1")
    assert memory.records == []


@pytest.mark.asyncio
async def test_repair_memory_without_an_id_is_not_saved(store):
    cache = RepositoryCache(store)
    await cache.save_repair_memory(RepairMemory())
    assert await cache.load_repair_memory("") == RepairMemory()


@pytest.mark.asyncio
async def test_repair_memory_survives_an_index_invalidation(store, index):
    """Memory outlives any commit — invalidating the index must not clear it."""
    cache = RepositoryCache(store)
    await cache.save_index(index)
    await cache.save_repair_memory(
        RepairMemory(repository_id="repo-1", records=[RepairRecord(repair_id="r1")])
    )
    await cache.invalidate_index("repo-1")

    assert await cache.load_index("repo-1") is None
    assert len((await cache.load_repair_memory("repo-1")).records) == 1


def test_index_size_reports_serialized_bytes(index):
    assert index_size_bytes(index) > 0
