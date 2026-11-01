"""A0.5 and its consumers: publication, reuse, new signals, and degradation.

Phase 3 is additive throughout. Every degradation test here asserts the same
contract the A5.5 tests do: with the layer off, broken, or absent, the pipeline
behaves exactly as it did before the layer existed.
"""

import uuid

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.agents.a1_semantic_mapper import A1SemanticMapperAgent
from backend.agents.repository_intelligence import (
    INTELLIGENCE_STORE_KEY,
    RepositoryIntelligenceAgent,
    load_repository_intelligence,
)
from backend.config import Settings
from backend.models.repository_graph import (
    RepairMemory,
    RepairRecord,
)
from backend.services.context_ranker import RankingInputs, rank_files
from backend.services.repair_memory import repository_id as compute_repository_id
from backend.services.repository_cache import RepositoryCache
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

MOD_A = '''"""Module A."""


def alpha(v):
    return beta(v)


def beta(v):
    return v + 1
'''

MOD_B = '''from pkg.a import alpha


def gamma(v):
    return alpha(v)
'''


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
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text(MOD_A)
    (pkg / "b.py").write_text(MOD_B)
    (tmp_path / "README.md").write_text("# Repo\n\nUses `pkg/a.py`.\n")
    return tmp_path


def make_state(repo) -> RunStateModel:
    return RunStateModel(
        run_id=str(uuid.uuid4()),
        repo_path=str(repo),
        repo_clone_path=str(repo),
        source_roots=["pkg/"],
    )


# -- publication -----------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_publishes_a_pointer_not_the_whole_index(store, settings, repo):
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)

    pointer = await store.get_json(state.run_id, INTELLIGENCE_STORE_KEY)
    assert pointer["repository_id"]
    assert pointer["metrics"]["repository_nodes"] > 0
    # The bulk stays in the cross-run cache.
    assert "repository_graph" not in pointer
    assert "parsed_modules" not in pointer


@pytest.mark.asyncio
async def test_index_is_loadable_through_the_pointer(store, settings, repo):
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)

    index = await load_repository_intelligence(store, state.run_id, settings)
    assert index is not None
    assert index.repository_graph.nodes
    assert index.call_graph.nodes
    assert index.parsed_modules


@pytest.mark.asyncio
async def test_agent_does_not_mutate_run_state(store, settings, repo):
    state = make_state(repo)
    before = state.model_dump(exclude={"ws_sequence", "source_roots"})
    await RepositoryIntelligenceAgent(store, settings).run(state)
    assert state.model_dump(exclude={"ws_sequence", "source_roots"}) == before


@pytest.mark.asyncio
async def test_second_run_reuses_the_cached_index(store, settings, repo):
    agent = RepositoryIntelligenceAgent(store, settings)
    await agent.run(make_state(repo))

    second = make_state(repo)
    await agent.run(second)
    pointer = await store.get_json(second.run_id, INTELLIGENCE_STORE_KEY)
    assert pointer["metrics"]["cache_hits"] == 1


@pytest.mark.asyncio
async def test_changed_file_triggers_an_incremental_update(store, settings, repo):
    agent = RepositoryIntelligenceAgent(store, settings)
    await agent.run(make_state(repo))

    (repo / "pkg" / "c.py").write_text("def added():\n    return 1\n")
    second = make_state(repo)
    await agent.run(second)

    pointer = await store.get_json(second.run_id, INTELLIGENCE_STORE_KEY)
    assert pointer["metrics"]["incremental_updates"] == 1
    assert pointer["delta"]["added"] == ["pkg/c.py"]


@pytest.mark.asyncio
async def test_repair_memory_is_attached_from_its_own_store(store, settings, repo):
    repo_id = compute_repository_id(repo)
    cache = RepositoryCache(store, version=settings.repository_cache_version)
    await cache.save_repair_memory(
        RepairMemory(
            repository_id=repo_id,
            records=[RepairRecord(repair_id="r1", file="pkg/a.py", validation_passed=True)],
        )
    )

    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)

    index = await load_repository_intelligence(store, state.run_id, settings)
    assert len(index.repair_memory.records) == 1


@pytest.mark.asyncio
async def test_loader_returns_none_when_the_layer_did_not_run(store, settings):
    assert await load_repository_intelligence(store, "never-ran", settings) is None


@pytest.mark.asyncio
async def test_completed_event_publishes_source_roots(store, settings, repo):
    """The Repository DNA panel needs this and there is no other endpoint that
    carries it — `index.source_roots` is real data the agent already computed."""
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)

    events = await store.get_events(state.run_id)
    done = [e for e in events if e.agent_id == "A0.5" and e.status == "completed"][-1]
    assert done.payload["repository_intelligence"]["source_roots"] == ["pkg/"]


@pytest.mark.asyncio
async def test_agent_makes_no_llm_calls(store, settings, repo, monkeypatch):
    """A0.5 is deterministic by construction; assert it stays that way."""
    import backend.services.llm as llm_module

    def explode(*args, **kwargs):
        raise AssertionError("A0.5 must never construct an LLM service")

    monkeypatch.setattr(llm_module, "LLMService", explode)
    await RepositoryIntelligenceAgent(store, settings).run(make_state(repo))


# -- A1 reuse --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a1_output_is_identical_with_and_without_the_index(store, settings, repo):
    """The reuse is an optimisation. The SIG it produces must not change."""
    without = make_state(repo)
    await A1SemanticMapperAgent(store, settings).run(without)

    with_index = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(with_index)
    await A1SemanticMapperAgent(store, settings).run(with_index)

    baseline = without.sig
    reused = with_index.sig
    assert baseline["files"] == reused["files"]
    assert baseline["edges"] == reused["edges"]
    assert baseline["source_roots"] == reused["source_roots"]


@pytest.mark.asyncio
async def test_a1_reports_reused_parses(store, settings, repo):
    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    await A1SemanticMapperAgent(store, settings).run(state)

    events = await store.get_events(state.run_id)
    a1_done = [e for e in events if e.agent_id == "A1" and e.status == "completed"][-1]
    metrics = a1_done.payload["a1_metrics"]
    assert metrics["repository_index_available"] is True
    assert metrics["repository_index_reused_parses"] > 0


@pytest.mark.asyncio
async def test_a1_reuse_can_be_disabled(client, repo):
    settings = Settings(stub_mode=True, sig_cache_enabled=False, repository_reuse_in_a1=False)
    store = RedisStore(client, settings)

    state = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    await A1SemanticMapperAgent(store, settings).run(state)

    events = await store.get_events(state.run_id)
    a1_done = [e for e in events if e.agent_id == "A1" and e.status == "completed"][-1]
    assert a1_done.payload["a1_metrics"]["repository_index_reused_parses"] == 0


@pytest.mark.asyncio
async def test_a1_works_when_the_index_is_absent(store, settings, repo):
    state = make_state(repo)
    await A1SemanticMapperAgent(store, settings).run(state)
    assert state.sig["files"]


# -- ranking signals -------------------------------------------------------


def test_ranking_without_intelligence_is_unchanged():
    """Empty lookups must contribute exactly nothing."""
    base = RankingInputs(auto_patch_scope=["a.py", "b.py"], resolved_target="a.py", target_confidence=1.0)
    enriched = RankingInputs(
        auto_patch_scope=["a.py", "b.py"],
        resolved_target="a.py",
        target_confidence=1.0,
        ownership={},
        call_fan_in={},
        history_churn={},
    )
    assert [(f.file, f.score) for f in rank_files(base)] == [
        (f.file, f.score) for f in rank_files(enriched)
    ]


@pytest.mark.parametrize(
    "field,value,signal",
    [
        ("prior_repairs", {"b.py": 1.0}, "prior_repair"),
        ("co_change", {"b.py": 1.0}, "co_change"),
        ("history_churn", {"b.py": 1.0}, "history_churn"),
        ("call_fan_in", {"b.py": 40}, "call_fan_in"),
        ("call_fan_out", {"b.py": 40}, "call_fan_out"),
        ("ownership", {"b.py": 1.0}, "ownership"),
        ("documentation", {"b.py": 1.0}, "documentation"),
    ],
)
def test_each_new_signal_contributes_and_is_explained(field, value, signal):
    inputs = RankingInputs(auto_patch_scope=["a.py", "b.py"], **{field: value})
    ranked = {f.file: f for f in rank_files(inputs)}

    assert ranked["b.py"].signals[signal] > 0
    assert ranked["b.py"].score > ranked["a.py"].score
    assert ranked["b.py"].evidence


def test_new_signals_cannot_outrank_runtime_evidence():
    """Repository priors break ties; they must never overturn a stack frame."""
    inputs = RankingInputs(
        auto_patch_scope=["suspect.py", "popular.py"],
        stack_frames=["suspect.py"],
        prior_repairs={"popular.py": 1.0},
        co_change={"popular.py": 1.0},
        history_churn={"popular.py": 1.0},
        call_fan_in={"popular.py": 100},
        call_fan_out={"popular.py": 100},
        ownership={"popular.py": 1.0},
        documentation={"popular.py": 1.0},
    )
    assert rank_files(inputs)[0].file == "suspect.py"


def test_signals_for_files_outside_the_candidate_set_are_ignored():
    inputs = RankingInputs(auto_patch_scope=["a.py"], prior_repairs={"elsewhere.py": 1.0})
    ranked = rank_files(inputs)
    assert [f.file for f in ranked] == ["a.py"]
    assert "prior_repair" not in ranked[0].signals


def test_ranking_with_signals_is_deterministic():
    inputs = RankingInputs(
        auto_patch_scope=["a.py", "b.py", "c.py"],
        ownership={"a.py": 0.5, "b.py": 0.5, "c.py": 0.5},
        call_fan_in={"a.py": 3, "b.py": 3, "c.py": 3},
    )
    assert [f.file for f in rank_files(inputs)] == [f.file for f in rank_files(inputs)]


# -- degradation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_layer_publishes_nothing(client, repo):
    from backend.orchestrator.nodes import GraphNodes

    settings = Settings(stub_mode=True, repository_intelligence_enabled=False)
    store = RedisStore(client, settings)
    state = make_state(repo)

    await GraphNodes(store, settings).index_repository(state)
    assert await store.get_json(state.run_id, INTELLIGENCE_STORE_KEY) is None


@pytest.mark.asyncio
async def test_agent_failure_is_recorded_and_does_not_raise(client, repo, monkeypatch):
    from backend.orchestrator import nodes as nodes_module
    from backend.orchestrator.nodes import GraphNodes

    settings = Settings(stub_mode=True)
    store = RedisStore(client, settings)

    def explode(*args, **kwargs):
        raise RuntimeError("indexing blew up")

    monkeypatch.setattr(nodes_module.RepositoryIntelligenceAgent, "run", explode)

    state = make_state(repo)
    result = await GraphNodes(store, settings).index_repository(state)
    assert any(e["agent"] == "A0.5" for e in result.errors)


@pytest.mark.asyncio
async def test_missing_repository_path_degrades_without_raising(store, settings, tmp_path):
    state = RunStateModel(
        run_id=str(uuid.uuid4()),
        repo_path=str(tmp_path / "absent"),
        repo_clone_path=str(tmp_path / "absent"),
    )
    await RepositoryIntelligenceAgent(store, settings).run(state)
    index = await load_repository_intelligence(store, state.run_id, settings)
    assert index is not None
    assert index.repository_graph.files == []


# -- repair memory write path ---------------------------------------------


def validated_state(repo) -> RunStateModel:
    state = make_state(repo)
    state.mutation_result = {"pytest_passed": True, "mutation_score": 0.5, "mutation_status": "ok"}
    state.security_result = {"rejected": False, "security_score": 100.0}
    state.pr_decision = {"pr_type": "auto_mergeable"}
    state.reproduction = {"exception_type": "KeyError"}
    state.patch_bundle = {
        "patches": [{"file": "pkg/a.py", "original": MOD_A, "patched": MOD_A + "\n"}],
        "diff_text": "--- a\n+++ b\n",
    }
    return state


@pytest.mark.asyncio
async def test_validated_repair_is_written_to_memory(store, settings, repo):
    from backend.orchestrator.nodes import GraphNodes

    nodes = GraphNodes(store, settings)
    state = validated_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    await nodes._remember_repairs(state)

    memory = await RepositoryCache(
        store, version=settings.repository_cache_version
    ).load_repair_memory(compute_repository_id(repo))

    assert len(memory.records) == 1
    record = memory.records[0]
    assert record.file == "pkg/a.py"
    assert record.bug_type == "missing-key"
    assert record.validation_passed
    assert record.pr_type == "auto_mergeable"


@pytest.mark.asyncio
async def test_failed_validation_is_not_remembered(store, settings, repo):
    from backend.orchestrator.nodes import GraphNodes

    state = validated_state(repo)
    state.mutation_result["pytest_passed"] = False
    await RepositoryIntelligenceAgent(store, settings).run(state)
    await GraphNodes(store, settings)._remember_repairs(state)

    memory = await RepositoryCache(
        store, version=settings.repository_cache_version
    ).load_repair_memory(compute_repository_id(repo))
    assert memory.records == []


@pytest.mark.asyncio
async def test_security_rejection_is_not_remembered(store, settings, repo):
    from backend.orchestrator.nodes import GraphNodes

    state = validated_state(repo)
    state.security_result["rejected"] = True
    await RepositoryIntelligenceAgent(store, settings).run(state)
    await GraphNodes(store, settings)._remember_repairs(state)

    memory = await RepositoryCache(
        store, version=settings.repository_cache_version
    ).load_repair_memory(compute_repository_id(repo))
    assert memory.records == []


@pytest.mark.asyncio
async def test_remembered_repair_is_retrievable_by_a_later_run(store, settings, repo):
    """The round trip the whole layer exists for."""
    from backend.orchestrator.nodes import GraphNodes

    first = validated_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(first)
    await GraphNodes(store, settings)._remember_repairs(first)

    # A later run, at a different repository state.
    (repo / "pkg" / "a.py").write_text(MOD_A + "\n\ndef added():\n    return 1\n")
    second = make_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(second)

    index = await load_repository_intelligence(store, second.run_id, settings)
    from backend.models.repository_graph import RepairQuery
    from backend.services.repair_memory import content_hash, find_similar_repairs

    matches = find_similar_repairs(
        index.repair_memory,
        RepairQuery(
            repository_hash=index.repository_hash,
            file="pkg/a.py",
            file_hash=content_hash(MOD_A),
            bug_type="missing-key",
        ),
    )
    assert matches
    assert "identical file content" in matches[0].matched_on


@pytest.mark.asyncio
async def test_memory_write_failure_is_recorded_not_raised(store, settings, repo, monkeypatch):
    from backend.orchestrator import nodes as nodes_module
    from backend.orchestrator.nodes import GraphNodes

    state = validated_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)

    async def explode(*args, **kwargs):
        raise RuntimeError("cache down")

    monkeypatch.setattr(nodes_module.RepositoryCache, "load_repair_memory", explode)
    await GraphNodes(store, settings)._remember_repairs(state)
    assert any(e["agent"] == "repair_memory" for e in state.errors)


@pytest.mark.asyncio
async def test_disabled_repair_memory_writes_nothing(client, repo):
    from backend.orchestrator.nodes import GraphNodes

    settings = Settings(stub_mode=True, repair_memory_enabled=False)
    store = RedisStore(client, settings)
    state = validated_state(repo)
    await RepositoryIntelligenceAgent(store, settings).run(state)
    await GraphNodes(store, settings)._remember_repairs(state)

    memory = await RepositoryCache(store).load_repair_memory(compute_repository_id(repo))
    assert memory.records == []
