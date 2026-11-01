"""Integration: graph cache, agent wiring, A5.5 signals, performance, API.

The performance tests assert the stated targets. They use generous multiples of
the measured figures so they fail on a real regression rather than on a busy CI
machine — a perf test that flakes gets deleted, and then it protects nothing.
"""

import time
import uuid

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.agents.a5_5_context_engineering import A55ContextEngineeringAgent
from backend.agents.repository_intelligence import (
    KNOWLEDGE_STORE_KEY,
    RepositoryIntelligenceAgent,
    load_repository_intelligence,
)
from backend.config import Settings
from backend.models.knowledge_graph import KnowledgeGraphSummary
from backend.services.context_ranker import (
    MAX_REPOSITORY_INTELLIGENCE_CONTRIBUTION,
    RankingInputs,
    rank_files,
)
from backend.services.graph_cache import (
    cache_key,
    cache_stats,
    clear_cache,
    get_knowledge_graph,
)
from backend.services.knowledge_graph import KnowledgeQueryEngine
from backend.services.repository_indexer import index_repository
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel
from tests.unit.kg_fixture import build_index, full_index, write_repo

# Targets from the specification, with headroom for machine variance.
UNCHANGED_TARGET_MS = 50 * 6
MEDIUM_TARGET_MS = 300 * 4
QUERY_TARGET_MS = 50


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


@pytest_asyncio.fixture
async def client():
    conn = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield conn
    await conn.aclose()


@pytest.fixture
def settings():
    return Settings(stub_mode=True, sig_cache_enabled=False)


@pytest_asyncio.fixture
async def store(client, settings):
    return RedisStore(client, settings)


@pytest.fixture
def repo(tmp_path):
    write_repo(tmp_path)
    return tmp_path


def make_state(repo) -> RunStateModel:
    return RunStateModel(
        run_id=str(uuid.uuid4()),
        repo_path=str(repo),
        repo_clone_path=str(repo),
        source_roots=["pkg/"],
    )


# -- graph cache -----------------------------------------------------------


def test_cache_key_combines_id_and_hash(tmp_path):
    index = full_index(tmp_path)
    assert cache_key(index) == f"{index.repository_id}:{index.repository_hash}"


def test_second_build_is_a_cache_hit(tmp_path):
    index = full_index(tmp_path)
    first = get_knowledge_graph(index)
    second = get_knowledge_graph(index)
    assert first is second
    assert cache_stats()["hits"] == 1


def test_changed_repository_hash_forces_a_rebuild(tmp_path):
    index = full_index(tmp_path)
    first = get_knowledge_graph(index)
    index.repository_hash = "different"
    assert get_knowledge_graph(index) is not first


def test_rebuild_flag_bypasses_the_cache(tmp_path):
    index = full_index(tmp_path)
    first = get_knowledge_graph(index)
    assert get_knowledge_graph(index, rebuild=True) is not first


def test_cache_is_bounded(tmp_path):
    from backend.services.graph_cache import MAX_CACHED_GRAPHS

    index = full_index(tmp_path)
    for i in range(MAX_CACHED_GRAPHS + 3):
        index.repository_hash = f"hash-{i}"
        get_knowledge_graph(index)
    assert cache_stats()["entries"] == MAX_CACHED_GRAPHS


def test_clear_cache_resets_counters(tmp_path):
    get_knowledge_graph(full_index(tmp_path))
    clear_cache()
    assert cache_stats() == {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0, "capacity": 8}


def test_hit_rate_is_reported(tmp_path):
    index = full_index(tmp_path)
    get_knowledge_graph(index)
    get_knowledge_graph(index)
    assert cache_stats()["hit_rate"] == 0.5


# -- agent publication -----------------------------------------------------


@pytest.mark.asyncio
async def test_agent_publishes_a_graph_summary(store, settings, repo):
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)

    raw = await store.get_json(state.run_id, KNOWLEDGE_STORE_KEY)
    summary = KnowledgeGraphSummary.model_validate(raw)
    assert summary.metrics.node_count > 0
    assert summary.metrics.edge_count > 0


@pytest.mark.asyncio
async def test_summary_does_not_contain_the_adjacency(store, settings, repo):
    """The graph is derived data — storing it would duplicate the six structures."""
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    raw = await store.get_json(state.run_id, KNOWLEDGE_STORE_KEY)
    assert "nodes" not in raw
    assert "edges" not in raw


@pytest.mark.asyncio
async def test_summary_carries_risks_and_hotspots(store, settings, repo):
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    summary = KnowledgeGraphSummary.model_validate(
        await store.get_json(state.run_id, KNOWLEDGE_STORE_KEY)
    )
    assert summary.top_risks
    assert summary.hotspots
    assert all(r.explanation.evidence for r in summary.top_risks)


@pytest.mark.asyncio
async def test_graph_metrics_reach_the_status_payload(store, settings, repo):
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    events = await store.get_events(state.run_id)
    completed = [e for e in events if e.agent_id == "A0.5" and e.status == "completed"][-1]
    assert completed.payload["knowledge_graph"]["node_count"] > 0
    assert "knowledge graph" in completed.message


@pytest.mark.asyncio
async def test_disabled_graph_publishes_no_summary(client, repo):
    settings = Settings(stub_mode=True, knowledge_graph_enabled=False)
    store = RedisStore(client, settings)
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    assert await store.get_json(state.run_id, KNOWLEDGE_STORE_KEY) is None


@pytest.mark.asyncio
async def test_index_still_publishes_when_the_graph_is_disabled(client, repo):
    settings = Settings(stub_mode=True, knowledge_graph_enabled=False)
    store = RedisStore(client, settings)
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    assert await load_repository_intelligence(store, state.run_id, settings) is not None


# -- A5.5 signals ----------------------------------------------------------


def test_graph_signals_are_inside_the_capped_group():
    """Traversal must never overturn a traceback."""
    inputs = RankingInputs(
        auto_patch_scope=["suspect.py", "related.py"],
        stack_frames=["suspect.py"],
        graph_related={"related.py": 1.0},
        graph_tests={"related.py": 1.0},
        prior_repairs={"related.py": 1.0},
        co_change={"related.py": 1.0},
        history_churn={"related.py": 1.0},
        call_fan_in={"related.py": 100},
        call_fan_out={"related.py": 100},
        ownership={"related.py": 1.0},
        documentation={"related.py": 1.0},
    )
    ranked = {f.file: f for f in rank_files(inputs)}
    assert ranked["suspect.py"].score > ranked["related.py"].score

    group = sum(
        v for k, v in ranked["related.py"].signals.items()
        if k in {
            "graph_related", "graph_validated", "prior_repair", "co_change",
            "history_churn", "call_fan_in", "call_fan_out", "ownership", "documentation",
        }
    )
    assert group <= MAX_REPOSITORY_INTELLIGENCE_CONTRIBUTION + 0.001


def test_graph_related_signal_contributes_and_explains():
    inputs = RankingInputs(auto_patch_scope=["a.py", "b.py"], graph_related={"b.py": 1.0})
    ranked = {f.file: f for f in rank_files(inputs)}
    assert ranked["b.py"].signals["graph_related"] > 0
    assert any("knowledge graph" in e for e in ranked["b.py"].evidence)


def test_graph_tests_signal_contributes():
    inputs = RankingInputs(auto_patch_scope=["a.py", "b.py"], graph_tests={"b.py": 1.0})
    ranked = {f.file: f for f in rank_files(inputs)}
    assert ranked["b.py"].signals["graph_validated"] > 0


def test_empty_graph_signals_change_nothing():
    base = RankingInputs(auto_patch_scope=["a.py", "b.py"], stack_frames=["a.py"])
    with_empty = RankingInputs(
        auto_patch_scope=["a.py", "b.py"],
        stack_frames=["a.py"],
        graph_related={},
        graph_tests={},
    )
    assert [(f.file, f.score) for f in rank_files(base)] == [
        (f.file, f.score) for f in rank_files(with_empty)
    ]


@pytest.mark.asyncio
async def test_a55_emits_graph_context(store, settings, repo):
    state = make_state(repo)
    state.blast_graph = {
        "origins": ["pkg/auth.py"],
        "auto_patch_scope": ["pkg/auth.py"],
        "scope": [{"path": "pkg/auth.py", "propagation_confidence": 1.0, "hop_count": 0}],
    }
    state.reproduction = {"failing_file": "pkg/auth.py", "failing_line": 20}

    await RepositoryIntelligenceAgent(store, settings).run(state)
    await A55ContextEngineeringAgent(store, settings).run(state)

    events = await store.get_events(state.run_id)
    completed = [e for e in events if e.agent_id == "A5.5" and e.status == "completed"][-1]
    context = completed.payload.get("knowledge_graph_context")
    assert context is not None
    assert set(context) >= {"supporting_tests", "documentation", "owners", "call_chain"}


@pytest.mark.asyncio
async def test_a55_runs_without_the_graph(client, repo):
    settings = Settings(stub_mode=True, knowledge_graph_enabled=False, sig_cache_enabled=False)
    store = RedisStore(client, settings)
    state = make_state(repo)
    state.blast_graph = {"origins": ["pkg/auth.py"], "auto_patch_scope": ["pkg/auth.py"]}

    await A55ContextEngineeringAgent(store, settings).run(state)
    events = await store.get_events(state.run_id)
    assert [e for e in events if e.agent_id == "A5.5" and e.status == "completed"]


# -- incremental behaviour -------------------------------------------------


def test_graph_reflects_an_incremental_index_update(tmp_path):
    write_repo(tmp_path)
    first = build_index(tmp_path)
    graph_before = get_knowledge_graph(first)

    (tmp_path / "pkg" / "extra.py").write_text("def brand_new():\n    return 1\n")
    second = index_repository(tmp_path, ["pkg/"], cached=first)
    graph_after = get_knowledge_graph(second)

    assert second.metrics.incremental_updates == 1
    assert graph_after.metrics.node_count > graph_before.metrics.node_count


def test_graph_reflects_a_deletion(tmp_path):
    write_repo(tmp_path)
    (tmp_path / "pkg" / "temp.py").write_text("def temporary():\n    return 1\n")
    first = build_index(tmp_path)
    assert get_knowledge_graph(first).node("function:pkg/temp.py::temporary")

    (tmp_path / "pkg" / "temp.py").unlink()
    second = index_repository(tmp_path, ["pkg/"], cached=first)
    assert get_knowledge_graph(second).node("function:pkg/temp.py::temporary") is None


def test_graph_reflects_a_rename(tmp_path):
    write_repo(tmp_path)
    first = build_index(tmp_path)
    (tmp_path / "pkg" / "util.py").rename(tmp_path / "pkg" / "helpers.py")

    second = index_repository(tmp_path, ["pkg/"], cached=first)
    graph = get_knowledge_graph(second)

    assert second.delta.renamed == {"pkg/util.py": "pkg/helpers.py"}
    assert graph.node("file:pkg/helpers.py")
    assert graph.node("file:pkg/util.py") is None


def test_incremental_graph_matches_a_full_rebuild(tmp_path):
    write_repo(tmp_path)
    first = build_index(tmp_path)
    (tmp_path / "pkg" / "extra.py").write_text("def brand_new():\n    return 1\n")

    incremental = get_knowledge_graph(index_repository(tmp_path, ["pkg/"], cached=first))
    clear_cache()
    full = get_knowledge_graph(index_repository(tmp_path, ["pkg/"]))

    assert sorted(incremental.nodes) == sorted(full.nodes)
    assert sorted((e.source, e.target, e.type) for e in incremental.edges) == sorted(
        (e.source, e.target, e.type) for e in full.edges
    )


# -- performance -----------------------------------------------------------


def test_unchanged_repository_index_meets_its_target(tmp_path):
    write_repo(tmp_path)
    cached = build_index(tmp_path)

    started = time.perf_counter()
    result = index_repository(tmp_path, ["pkg/"], cached=cached)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert result.metrics.cache_hits == 1
    assert elapsed_ms < UNCHANGED_TARGET_MS, f"{elapsed_ms:.1f}ms"


def test_incremental_update_meets_its_target(tmp_path):
    write_repo(tmp_path)
    cached = build_index(tmp_path)
    (tmp_path / "pkg" / "extra.py").write_text("def brand_new():\n    return 1\n")

    started = time.perf_counter()
    index_repository(tmp_path, ["pkg/"], cached=cached)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < MEDIUM_TARGET_MS, f"{elapsed_ms:.1f}ms"


def test_graph_build_is_fast(tmp_path):
    index = full_index(tmp_path)
    started = time.perf_counter()
    get_knowledge_graph(index, rebuild=True)
    assert (time.perf_counter() - started) * 1000 < MEDIUM_TARGET_MS


def test_cached_graph_lookup_is_effectively_free(tmp_path):
    index = full_index(tmp_path)
    get_knowledge_graph(index)
    started = time.perf_counter()
    for _ in range(100):
        get_knowledge_graph(index)
    assert (time.perf_counter() - started) * 1000 < UNCHANGED_TARGET_MS


def test_queries_are_fast(tmp_path):
    engine = KnowledgeQueryEngine(get_knowledge_graph(full_index(tmp_path)))
    started = time.perf_counter()
    for _ in range(50):
        engine.functions_in_file("pkg/auth.py")
        engine.related_functions("pkg/auth.py", "validate")
        engine.historical_bug_hotspots()
    assert (time.perf_counter() - started) * 1000 < QUERY_TARGET_MS * 10


def test_metrics_report_memory_without_counting_the_index(tmp_path):
    index = full_index(tmp_path)
    graph = get_knowledge_graph(index)
    assert 0 < graph.metrics.memory_bytes < index.metrics.index_size


# -- explainability contract ----------------------------------------------


def test_no_recommendation_lacks_evidence(tmp_path):
    """The contract: never output a black-box score."""
    from backend.services.architecture_analyzer import analyze_architecture
    from backend.services.capability_layer import infer_capabilities
    from backend.services.risk_engine import assess_repository

    graph = get_knowledge_graph(full_index(tmp_path))

    for assessment in assess_repository(graph):
        if assessment.risk > 0:
            assert assessment.explanation.evidence
            assert assessment.explanation.signals
    for capability in infer_capabilities(graph):
        assert capability.explanation.evidence
    for hotspot in analyze_architecture(graph):
        assert hotspot.explanation.evidence


def test_evidence_describes_itself_readably(tmp_path):
    from backend.services.risk_engine import assess_file

    graph = get_knowledge_graph(full_index(tmp_path))
    for evidence in assess_file(graph, "pkg/auth.py").evidence:
        described = evidence.describe()
        assert evidence.signal in described
        assert evidence.detail in described


def test_explanation_aggregates_signals_and_edges(tmp_path):
    from backend.services.risk_engine import assess_file

    graph = get_knowledge_graph(full_index(tmp_path))
    explanation = assess_file(graph, "pkg/auth.py").explanation
    assert explanation.signals
    assert explanation.reasons()
    assert isinstance(explanation.edges, list)


# -- API surface -----------------------------------------------------------


def served_paths() -> set[str]:
    from backend.main import create_app

    return set(create_app().openapi()["paths"])


def test_knowledge_routes_are_registered():
    paths = served_paths()
    assert "/api/knowledge/{run_id}/metrics" in paths
    assert "/api/knowledge/{run_id}/risk" in paths
    assert "/api/knowledge/{run_id}/capabilities" in paths
    assert "/api/knowledge/{run_id}/hotspots" in paths
    assert "/api/knowledge/{run_id}/query/{name}" in paths
    assert "/api/knowledge/{run_id}/export/{view}" in paths


def test_existing_routes_are_untouched():
    """Phase 4 is additive: nothing that existed may have moved."""
    paths = served_paths()
    assert "/api/runs" in paths
    assert any(p.startswith("/api/runs/") for p in paths)


def test_every_knowledge_route_is_namespaced():
    """Additive by construction: nothing outside /api/knowledge was added."""
    from backend.api.routes import knowledge

    assert all(r.path.startswith("/api/knowledge") for r in knowledge.router.routes)
