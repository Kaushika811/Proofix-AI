"""A5.5 agent: boundaries, integration with A6/A7, and graceful degradation.

The layer is advisory. Every test here that exercises a failure path asserts the
same thing: the pipeline continues with exactly the behaviour it had before A5.5
existed.
"""

import uuid

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.agents.a5_5_context_engineering import (
    A55ContextEngineeringAgent,
    load_context_package,
)
from backend.agents.a6_fix_dag_planner import A6FixDAGPlannerAgent
from backend.config import Settings
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

MODULE = '''"""Module."""

import os

LIMIT = 10


def helper(v):
    return v * 2


def noise():
    return "unrelated " * 50


def target(v):
    """Repair target."""
    return helper(v) + LIMIT
'''


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisStore(client, Settings(stub_mode=True))
    await client.aclose()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(MODULE)
    return tmp_path


def make_state(repo, **overrides) -> RunStateModel:
    state = RunStateModel(
        run_id=str(uuid.uuid4()),
        repo_path=str(repo),
        repo_clone_path=str(repo),
        source_roots=["pkg/"],
        sig={
            "repo_path": str(repo),
            "source_roots": ["pkg/"],
            "files": {
                "pkg/mod.py": {
                    "path": "pkg/mod.py",
                    "role": "internal-util",
                    "imports": [],
                    "imported_by": [],
                    "churn_weight": 0.2,
                    "criticality": 0.5,
                }
            },
            "edges": [],
        },
        blast_graph={
            "auto_patch_scope": ["pkg/mod.py"],
            "origins": ["pkg/mod.py"],
            "scope": [
                {
                    "path": "pkg/mod.py",
                    "direction": "forward",
                    "propagation_confidence": 1.0,
                    "risk_score": 0.1,
                    "hop_count": 0,
                    "origin": "pkg/mod.py",
                }
            ],
            "human_review_required": [],
        },
        root_cause={
            "summary": "Off-by-one in target",
            "root_cause": "target adds LIMIT incorrectly",
            "citations": [{"file": "pkg/mod.py", "line": 17, "claim": "target()", "verified": True}],
            "affected_modules": ["pkg/mod.py"],
        },
        reproduction={
            "status": "CONFIRMED",
            "failing_test": "tests/test_mod.py::test_target",
            "exception_type": "AssertionError",
            "exception_message": "expected 12",
            "failing_file": "pkg/mod.py",
            "failing_line": 17,
            "pre_existing_failures": [],
        },
        static_report={"prioritized": []},
    )
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


async def run_agent(store, state, settings=None):
    agent = A55ContextEngineeringAgent(store, settings or Settings(stub_mode=True))
    return await agent.run(state)


# -- happy path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_produces_and_stores_a_package(store, repo):
    state = make_state(repo)
    await run_agent(store, state)

    package = await load_context_package(store, state.run_id)
    assert package is not None
    assert package.target_file == "pkg/mod.py"
    assert package.ranked_files
    assert package.acceptance_criteria


@pytest.mark.asyncio
async def test_resolves_target_function_from_citation_line(store, repo):
    """AST line containment, not name guessing from prose."""
    state = make_state(repo)
    await run_agent(store, state)
    assert (await load_context_package(store, state.run_id)).target_function == "target"


@pytest.mark.asyncio
async def test_acceptance_criteria_reference_the_failing_test(store, repo):
    state = make_state(repo)
    await run_agent(store, state)
    criteria = (await load_context_package(store, state.run_id)).acceptance_criteria
    assert any("tests/test_mod.py::test_target" in c for c in criteria)
    assert any("previously passing" in c for c in criteria)


@pytest.mark.asyncio
async def test_runtime_evidence_is_carried(store, repo):
    state = make_state(repo)
    await run_agent(store, state)
    evidence = (await load_context_package(store, state.run_id)).runtime_evidence
    assert evidence["status"] == "CONFIRMED"
    assert evidence["exception_type"] == "AssertionError"


@pytest.mark.asyncio
async def test_metrics_are_emitted(store, repo):
    state = make_state(repo)
    await run_agent(store, state)
    events = await store.get_events(state.run_id)
    completed = [e for e in events if e.agent_id == "A5.5" and e.status == "completed"]
    assert completed

    payload = completed[-1].payload["context_engineering"]
    for key in (
        "context_files",
        "context_functions",
        "context_lines",
        "token_reduction",
        "estimated_prompt_tokens",
        "estimated_saved_tokens",
        "cache_hit",
        "ranking_time_ms",
        "extraction_time_ms",
        "build_time_ms",
        "privacy_redactions",
        "files_ranked",
        "files_extracted",
    ):
        assert key in payload, f"missing metric: {key}"


# -- boundaries ------------------------------------------------------------


def test_agent_never_imports_an_llm():
    """The 'no LLM' rule, enforced against the module's own source."""
    import backend.agents.a5_5_context_engineering as module

    source = open(module.__file__).read()
    assert "LLMService" not in source
    assert "llm_gateway" not in source
    assert "anthropic" not in source.lower()


def test_supporting_services_never_import_an_llm():
    for name in (
        "backend.services.context_ranker",
        "backend.services.context_extractor",
        "backend.services.context_package",
        "backend.services.privacy_guard",
        "backend.services.context_cache",
    ):
        module = __import__(name, fromlist=["_"])
        source = open(module.__file__).read()
        assert "LLMService" not in source, f"{name} imports an LLM client"


@pytest.mark.asyncio
async def test_run_state_is_not_mutated(store, repo):
    """The package goes to the run store, never into RunState."""
    state = make_state(repo)
    before = state.model_dump()
    after = (await run_agent(store, state)).model_dump()
    before.pop("ws_sequence")
    after.pop("ws_sequence")
    assert before == after


@pytest.mark.asyncio
async def test_repository_source_is_not_modified(store, repo):
    original = (repo / "pkg" / "mod.py").read_text()
    await run_agent(store, make_state(repo))
    assert (repo / "pkg" / "mod.py").read_text() == original


# -- determinism and caching ----------------------------------------------


@pytest.mark.asyncio
async def test_two_runs_produce_identical_context(store, repo):
    first_state = make_state(repo)
    await run_agent(store, first_state)
    first = await load_context_package(store, first_state.run_id)

    second_state = make_state(repo)
    await run_agent(store, second_state)
    second = await load_context_package(store, second_state.run_id)

    assert first.focused_context == second.focused_context
    assert first.ranked_paths() == second.ranked_paths()


@pytest.mark.asyncio
async def test_second_call_in_the_same_run_hits_the_cache(store, repo):
    state = make_state(repo)
    await run_agent(store, state)
    await run_agent(store, state)

    events = await store.get_events(state.run_id)
    payloads = [
        e.payload["context_engineering"]
        for e in events
        if e.agent_id == "A5.5" and e.status == "completed"
    ]
    assert payloads[0]["cache_hit"] is False
    assert payloads[1]["cache_hit"] is True


@pytest.mark.asyncio
async def test_retry_attempt_invalidates_the_cache(store, repo):
    state = make_state(repo)
    await run_agent(store, state)
    state.retry_count = 1
    await run_agent(store, state)

    events = await store.get_events(state.run_id)
    payloads = [
        e.payload["context_engineering"]
        for e in events
        if e.agent_id == "A5.5" and e.status == "completed"
    ]
    assert payloads[-1]["cache_hit"] is False


# -- degradation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_blast_scope_still_recovers_a_target_from_citations(store, repo):
    """An empty auto-patch scope is not the end of the evidence trail."""
    state = make_state(repo, blast_graph={"auto_patch_scope": [], "origins": [], "scope": []})
    await run_agent(store, state)

    package = await load_context_package(store, state.run_id)
    assert package is not None
    assert package.target_file == "pkg/mod.py"


@pytest.mark.asyncio
async def test_no_evidence_at_all_skips_without_failing(store, repo):
    state = make_state(
        repo,
        blast_graph={"auto_patch_scope": [], "origins": [], "scope": []},
        root_cause={"summary": "", "citations": [], "affected_modules": []},
        reproduction={"status": "UNCONFIRMED"},
        static_report={"prioritized": []},
    )
    result = await run_agent(store, state)

    assert result is state
    assert await load_context_package(store, state.run_id) is None

    events = await store.get_events(state.run_id)
    completed = [e for e in events if e.agent_id == "A5.5" and e.status == "completed"]
    assert completed[-1].payload["context_engineering"]["skipped"] == "no_target_file"


@pytest.mark.asyncio
async def test_vendor_path_blast_origin_never_becomes_the_repair_target(store, repo):
    """Belt-and-suspenders at the agent boundary: even if `blast_graph`
    somehow named a vendor path as its origin — bypassing
    `resolve_origins`'s own filter — A5.5 must not adopt it as the target.
    With no application-scoped evidence left, this is honestly "no target",
    not a vendor file dressed up as one."""
    state = make_state(
        repo,
        blast_graph={
            "auto_patch_scope": [".venv/Lib/site-packages/httpx/_auth.py"],
            "origins": [".venv/Lib/site-packages/httpx/_auth.py"],
            "scope": [],
            "human_review_required": [],
        },
        root_cause={
            "summary": "weak hash",
            "citations": [
                {
                    "file": ".venv/Lib/site-packages/httpx/_auth.py",
                    "line": 309,
                    "verified": True,
                }
            ],
            "affected_modules": [],
        },
    )
    result = await run_agent(store, state)

    assert result is state
    package = await load_context_package(store, state.run_id)
    assert package is None

    events = await store.get_events(state.run_id)
    completed = [e for e in events if e.agent_id == "A5.5" and e.status == "completed"]
    assert completed[-1].payload["context_engineering"]["skipped"] == "no_target_file"


@pytest.mark.asyncio
async def test_missing_sig_still_produces_a_package(store, repo):
    state = make_state(repo, sig=None)
    await run_agent(store, state)
    assert await load_context_package(store, state.run_id) is not None


@pytest.mark.asyncio
async def test_missing_target_file_on_disk_degrades(store, repo):
    state = make_state(repo)
    state.blast_graph = {
        "auto_patch_scope": ["pkg/gone.py"],
        "origins": ["pkg/gone.py"],
        "scope": [],
        "human_review_required": [],
    }
    await run_agent(store, state)
    package = await load_context_package(store, state.run_id)
    assert package.metrics.degraded is True


@pytest.mark.asyncio
async def test_load_returns_none_when_layer_did_not_run(store):
    assert await load_context_package(store, "never-ran") is None


# -- A6 integration --------------------------------------------------------


@pytest.mark.asyncio
async def test_a6_scope_set_is_unchanged_by_ranking(store, repo):
    """A5.5 reorders; it must never add or drop a file from the fix scope."""
    state = make_state(repo)
    state.blast_graph["auto_patch_scope"] = ["pkg/mod.py", "pkg/other.py"]
    await run_agent(store, state)

    agent = A6FixDAGPlannerAgent(store, Settings(stub_mode=True))
    ordered = await agent._scope_files(state, state.blast_graph)
    assert set(ordered) == {"pkg/mod.py", "pkg/other.py"}


@pytest.mark.asyncio
async def test_a6_falls_back_to_blast_scope_without_a_package(store, repo):
    state = make_state(repo)
    agent = A6FixDAGPlannerAgent(store, Settings(stub_mode=True))
    assert await agent._scope_files(state, state.blast_graph) == ["pkg/mod.py"]


@pytest.mark.asyncio
async def test_a6_ignores_ranked_files_outside_the_scope(store, repo):
    state = make_state(repo)
    await run_agent(store, state)
    agent = A6FixDAGPlannerAgent(store, Settings(stub_mode=True))
    ordered = await agent._scope_files(state, {"auto_patch_scope": ["pkg/mod.py"]})
    assert ordered == ["pkg/mod.py"]


@pytest.mark.asyncio
async def test_a6_handles_empty_scope(store, repo):
    agent = A6FixDAGPlannerAgent(store, Settings(stub_mode=True))
    assert await agent._scope_files(make_state(repo), {"auto_patch_scope": []}) == []


# -- A7 integration --------------------------------------------------------


def test_a7_uses_the_package_only_when_adopted(repo):
    from backend.agents.a7_code_generation import A7CodeGenerationAgent
    from backend.models.context import ContextPackage
    from backend.models.patch import PatchPlan
    from unittest.mock import MagicMock

    agent = A7CodeGenerationAgent(MagicMock(), Settings(stub_mode=True))
    plan = PatchPlan(file="pkg/mod.py", root_cause="rc", required_behavior_change="fix")

    adopted = ContextPackage(
        target_file="pkg/mod.py", focused_context="FOCUSED", prefer_focused=True
    )
    source, origin = agent._focus_section(plan, MODULE, adopted)
    assert source == "FOCUSED"
    assert origin == "a5_5_context_package"

    rejected = adopted.model_copy(update={"prefer_focused": False})
    _, origin = agent._focus_section(plan, MODULE, rejected)
    assert origin == "a7_local_extraction"


def test_a7_ignores_a_package_for_a_different_file(repo):
    from backend.agents.a7_code_generation import A7CodeGenerationAgent
    from backend.models.context import ContextPackage
    from backend.models.patch import PatchPlan
    from unittest.mock import MagicMock

    agent = A7CodeGenerationAgent(MagicMock(), Settings(stub_mode=True))
    plan = PatchPlan(file="pkg/mod.py", root_cause="rc", required_behavior_change="fix")
    other = ContextPackage(target_file="pkg/elsewhere.py", focused_context="FOCUSED")

    _, origin = agent._focus_section(plan, MODULE, other)
    assert origin == "a7_local_extraction"


def test_a7_falls_back_when_the_guard_failed(repo):
    from backend.agents.a7_code_generation import A7CodeGenerationAgent
    from backend.models.context import ContextPackage
    from backend.models.patch import PatchPlan
    from unittest.mock import MagicMock

    agent = A7CodeGenerationAgent(MagicMock(), Settings(stub_mode=True))
    plan = PatchPlan(file="pkg/mod.py", root_cause="rc", required_behavior_change="fix")
    failed = ContextPackage(
        target_file="pkg/mod.py", focused_context="FOCUSED", privacy_guard_status="failed"
    )

    _, origin = agent._focus_section(plan, MODULE, failed)
    assert origin == "a7_local_extraction"


def test_a7_without_a_package_behaves_as_before(repo):
    from backend.agents.a7_code_generation import A7CodeGenerationAgent
    from backend.models.patch import PatchPlan
    from backend.services.runtime_patch_prompt import extract_relevant_code
    from unittest.mock import MagicMock

    agent = A7CodeGenerationAgent(MagicMock(), Settings(stub_mode=True))
    plan = PatchPlan(
        file="pkg/mod.py",
        root_cause="rc",
        required_behavior_change="fix",
        target_function="target",
    )
    source, origin = agent._focus_section(plan, MODULE, None)
    assert origin == "a7_local_extraction"
    assert source == extract_relevant_code(MODULE, "target")


# -- orchestration ---------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_layer_is_a_no_op(store, repo):
    from backend.orchestrator.nodes import GraphNodes

    settings = Settings(stub_mode=True, context_engineering_enabled=False)
    nodes = GraphNodes(store, settings)
    state = make_state(repo)

    assert await nodes.engineer_context(state) is state
    assert await load_context_package(store, state.run_id) is None


@pytest.mark.asyncio
async def test_agent_failure_does_not_fail_the_run(store, repo, monkeypatch):
    from backend.orchestrator.nodes import GraphNodes

    nodes = GraphNodes(store, Settings(stub_mode=True))

    async def boom(_state):
        raise RuntimeError("ranking exploded")

    monkeypatch.setattr(nodes.a55, "run", boom)
    state = make_state(repo)
    result = await nodes.engineer_context(state)

    assert result is state
    assert any(e.get("agent") == "A5.5" for e in result.errors)


def test_graph_places_the_layer_between_blast_and_planning():
    import inspect

    from backend.orchestrator import graph as graph_module

    source = inspect.getsource(graph_module.build_graph)
    assert 'graph.add_edge("blast_scope", "engineer_context")' in source
    assert 'graph.add_edge("engineer_context", "plan_fixes")' in source
