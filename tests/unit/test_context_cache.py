"""Context cache: key identity, reuse, metrics, and fail-open behaviour."""

import pytest

from backend.models.context import ContextMetrics, ContextPackage
from backend.services.context_cache import (
    CacheMetrics,
    ContextCache,
    build_cache_key,
    root_cause_id,
    sig_hash,
)


class FakeStore:
    """Minimal stand-in for the run store's JSON access."""

    def __init__(self, *, fail: bool = False):
        self.data: dict[str, dict] = {}
        self.fail = fail

    async def get_json(self, run_id, suffix):
        if self.fail:
            raise RuntimeError("redis down")
        return self.data.get(f"{run_id}:{suffix}")

    async def set_json(self, run_id, suffix, payload):
        if self.fail:
            raise RuntimeError("redis down")
        self.data[f"{run_id}:{suffix}"] = payload


def package(**kwargs) -> ContextPackage:
    return ContextPackage(
        target_file="pkg/mod.py",
        focused_context="code",
        metrics=ContextMetrics(build_time_ms=12),
        **kwargs,
    )


KEY_ARGS = dict(
    repo_hash="repo1",
    sig_digest="sig1",
    target_file="pkg/mod.py",
    target_function="target",
    root_cause_digest="rc1",
    attempt=0,
)


# -- key identity ----------------------------------------------------------


def test_identical_inputs_produce_identical_keys():
    assert build_cache_key(**KEY_ARGS) == build_cache_key(**KEY_ARGS)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repo_hash", "repo2"),
        ("sig_digest", "sig2"),
        ("target_file", "pkg/other.py"),
        ("target_function", "other"),
        ("root_cause_digest", "rc2"),
        ("attempt", 1),
    ],
)
def test_every_input_participates_in_the_key(field, value):
    """A change to any of these must invalidate the entry."""
    assert build_cache_key(**{**KEY_ARGS, field: value}) != build_cache_key(**KEY_ARGS)


def test_missing_target_function_is_still_keyable():
    assert build_cache_key(**{**KEY_ARGS, "target_function": None})


def test_sig_hash_is_stable_and_order_independent():
    a = {"files": {"b.py": {}, "a.py": {}}, "source_roots": ["y/", "x/"]}
    b = {"files": {"a.py": {}, "b.py": {}}, "source_roots": ["x/", "y/"]}
    assert sig_hash(a) == sig_hash(b)


def test_sig_hash_changes_with_file_set():
    assert sig_hash({"files": {"a.py": {}}}) != sig_hash({"files": {"a.py": {}, "b.py": {}}})


def test_sig_hash_without_sig():
    assert sig_hash(None) == "no-sig"


def test_root_cause_id_tracks_citations():
    base = {"root_cause": "off by one", "citations": [{"file": "a.py", "line": 1, "verified": True}]}
    changed = {"root_cause": "off by one", "citations": [{"file": "a.py", "line": 2, "verified": True}]}
    assert root_cause_id(base) != root_cause_id(changed)


def test_root_cause_id_ignores_citation_order():
    one = {"citations": [{"file": "a.py", "line": 1}, {"file": "b.py", "line": 2}]}
    two = {"citations": [{"file": "b.py", "line": 2}, {"file": "a.py", "line": 1}]}
    assert root_cause_id(one) == root_cause_id(two)


def test_root_cause_id_without_root_cause():
    assert root_cause_id(None) == "no-root-cause"


# -- reuse -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_miss_then_hit():
    cache = ContextCache(FakeStore())
    key = build_cache_key(**KEY_ARGS)

    assert await cache.get("run1", key) is None
    await cache.set("run1", key, package())

    hit = await cache.get("run1", key)
    assert hit is not None
    assert hit.metrics.cache_hit is True


@pytest.mark.asyncio
async def test_different_key_is_a_miss():
    cache = ContextCache(FakeStore())
    await cache.set("run1", build_cache_key(**KEY_ARGS), package())
    other = build_cache_key(**{**KEY_ARGS, "attempt": 1})
    assert await cache.get("run1", other) is None


@pytest.mark.asyncio
async def test_entries_are_scoped_per_run():
    store = FakeStore()
    cache = ContextCache(store)
    key = build_cache_key(**KEY_ARGS)
    await cache.set("run1", key, package())
    assert await cache.get("run2", key) is None


@pytest.mark.asyncio
async def test_cached_payload_excludes_the_complete_file():
    """A hit must never be able to resurrect content that was left out."""
    store = FakeStore()
    cache = ContextCache(store)
    key = build_cache_key(**KEY_ARGS)
    await cache.set("run1", key, package(original_complete_file="SECRET SOURCE"))
    stored = next(iter(store.data.values()))
    assert "original_complete_file" not in stored


@pytest.mark.asyncio
async def test_empty_key_is_never_stored_or_read():
    store = FakeStore()
    cache = ContextCache(store)
    await cache.set("run1", "", package())
    assert store.data == {}
    assert await cache.get("run1", "") is None


# -- metrics ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_count_hits_and_misses():
    cache = ContextCache(FakeStore())
    key = build_cache_key(**KEY_ARGS)

    await cache.get("run1", key)          # miss (not recorded — nothing built yet)
    await cache.set("run1", key, package())  # records the build
    await cache.get("run1", key)          # hit

    assert cache.metrics.hits == 1
    assert cache.metrics.misses == 1
    assert cache.metrics.hit_rate == 0.5
    assert cache.metrics.average_build_time_ms == 12


def test_metrics_serialise():
    metrics = CacheMetrics()
    metrics.record_miss(20)
    metrics.record_hit()
    assert metrics.to_dict() == {
        "hits": 1,
        "misses": 1,
        "hit_rate": 0.5,
        "average_build_time_ms": 20,
    }


def test_metrics_with_no_activity():
    assert CacheMetrics().to_dict()["hit_rate"] == 0.0


# -- resilience ------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_failure_degrades_to_a_miss():
    """A cache fault must never fail a repair run."""
    cache = ContextCache(FakeStore(fail=True))
    key = build_cache_key(**KEY_ARGS)
    assert await cache.get("run1", key) is None
    await cache.set("run1", key, package())  # must not raise


@pytest.mark.asyncio
async def test_corrupt_entry_is_treated_as_a_miss():
    store = FakeStore()
    cache = ContextCache(store)
    key = build_cache_key(**KEY_ARGS)
    store.data[f"run1:{ContextCache.suffix(key)}"] = {"ranked_files": "not-a-list"}
    assert await cache.get("run1", key) is None
