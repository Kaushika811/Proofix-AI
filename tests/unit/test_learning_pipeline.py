"""The learning pipeline facade, its failure isolation, and the API surface."""

import uuid

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.services.learning_pipeline import (
    LearningPipeline,
    get_learning_pipeline,
    reset_learning_pipeline,
)
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel


@pytest.fixture(autouse=True)
def _clean():
    reset_learning_pipeline()
    yield
    reset_learning_pipeline()


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisStore(client, Settings(stub_mode=True))
    await client.aclose()


def settings(**overrides) -> Settings:
    base = {"stub_mode": True}
    base.update(overrides)
    return Settings(**base)


def make_state(**overrides) -> RunStateModel:
    state = RunStateModel(
        run_id=str(uuid.uuid4()),
        repo_path="/tmp/fixture-repo",
        repo_clone_path="/tmp/fixture-repo",
    )
    state.reproduction = {"exception_type": "KeyError"}
    state.root_cause = {"root_cause": "missing validation of the key"}
    state.patch_bundle = {
        "patches": [{"file": "pkg/a.py", "original": "x = 1\n", "patched": "x = 2\n"}]
    }
    state.mutation_result = {"pytest_passed": True, "mutation_score": 0.7}
    state.security_result = {"rejected": False, "security_score": 100.0}
    state.pr_decision = {"pr_type": "draft"}
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


# -- lifecycle -------------------------------------------------------------


def test_pipeline_is_process_wide():
    """Accumulation across runs is the entire point of the layer."""
    assert get_learning_pipeline(settings()) is get_learning_pipeline(settings())


def test_reset_drops_the_instance():
    first = get_learning_pipeline(settings())
    reset_learning_pipeline()
    assert get_learning_pipeline(settings()) is not first


def test_enabled_by_default():
    assert LearningPipeline(settings()).enabled


def test_can_be_disabled():
    assert not LearningPipeline(settings(learning_enabled=False)).enabled


# -- extraction ------------------------------------------------------------


def test_learn_from_run_records_a_repair():
    pipeline = LearningPipeline(settings())
    knowledge = pipeline.learn_from_run(make_state(), repository_id="repo-a")
    assert knowledge is not None
    assert knowledge.bug_category == "missing-key"


def test_learn_from_run_without_patches_records_nothing():
    pipeline = LearningPipeline(settings())
    assert pipeline.learn_from_run(make_state(patch_bundle={"patches": []})) is None


def test_learn_from_run_is_disabled_by_configuration():
    pipeline = LearningPipeline(settings(learning_enabled=False))
    assert pipeline.learn_from_run(make_state()) is None


def test_auto_mergeable_seeds_an_accepted_outcome():
    pipeline = LearningPipeline(settings())
    state = make_state(pr_decision={"pr_type": "auto_mergeable"})
    knowledge = pipeline.learn_from_run(state, repository_id="repo-a")
    assert pipeline.engine.state.outcomes.current_status(knowledge.repair_id) == "accepted"


def test_extraction_failure_is_isolated(monkeypatch):
    """A learning fault must never cost the repair."""
    pipeline = LearningPipeline(settings())

    def explode(*args, **kwargs):
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(pipeline.engine, "learn_from_run", explode)
    assert pipeline.learn_from_run(make_state()) is None


def test_run_state_is_not_modified():
    """Phase 6 must not redesign or mutate RunState."""
    pipeline = LearningPipeline(settings())
    state = make_state()
    before = state.model_dump()
    pipeline.learn_from_run(state, repository_id="repo-a")
    assert state.model_dump() == before


# -- context ---------------------------------------------------------------


def test_context_is_empty_when_disabled():
    pipeline = LearningPipeline(settings(learning_enabled=False))
    assert pipeline.context_for("repo-a")["directives"] == []


def test_context_for_an_unknown_repository_is_empty_but_shaped():
    context = LearningPipeline(settings()).context_for("never-seen")
    assert context["directives"] == []
    assert "sources" in context


def test_directive_block_is_empty_without_knowledge():
    assert LearningPipeline(settings()).directive_block("never-seen") == ""


def test_context_failure_is_isolated(monkeypatch):
    pipeline = LearningPipeline(settings())

    def explode(*args, **kwargs):
        raise RuntimeError("index blew up")

    monkeypatch.setattr(pipeline.engine, "knowledge_index", explode)
    assert pipeline.context_for("repo-a")["directives"] == []


def test_observe_failure_is_isolated(monkeypatch):
    pipeline = LearningPipeline(settings())

    def explode(*args, **kwargs):
        raise RuntimeError("observe blew up")

    monkeypatch.setattr(pipeline.engine, "observe_repository", explode)
    assert pipeline.observe_repository("repo-a", None, {"a.py": object()}) is None


def test_observe_without_modules_is_skipped():
    assert LearningPipeline(settings()).observe_repository("repo-a", None, {}) is None


# -- outcomes and reviews --------------------------------------------------


def test_record_outcome():
    pipeline = LearningPipeline(settings())
    pipeline.learn_from_run(make_state(), repository_id="repo-a")
    repair_id = pipeline.engine.state.repairs.records[0].repair_id
    assert pipeline.record_outcome(repair_id, "merged") is not None


def test_record_review():
    pipeline = LearningPipeline(settings())
    pipeline.learn_from_run(make_state(), repository_id="repo-a")
    repair_id = pipeline.engine.state.repairs.records[0].repair_id
    review = pipeline.record_review(repair_id, "minor_edits", "needs a test")
    assert review.categories == ["testing"]


def test_outcome_is_disabled_with_learning():
    assert LearningPipeline(settings(learning_enabled=False)).record_outcome("r1", "merged") is None


def test_review_is_disabled_with_learning():
    assert LearningPipeline(settings(learning_enabled=False)).record_review("r1", "rejected") is None


def test_dashboard_reports_disabled_state():
    assert LearningPipeline(settings(learning_enabled=False)).dashboard() == {"enabled": False}


def test_dashboard_reports_enabled_state():
    assert LearningPipeline(settings()).dashboard()["enabled"] is True


# -- accumulation across runs ---------------------------------------------


def test_knowledge_accumulates_across_runs():
    pipeline = LearningPipeline(settings())
    for _ in range(4):
        pipeline.learn_from_run(make_state(), repository_id="repo-a")
    assert len(pipeline.engine.state.repairs.records) == 4


def test_templates_emerge_from_repeated_repairs():
    pipeline = LearningPipeline(settings())
    for _ in range(3):
        pipeline.learn_from_run(make_state(), repository_id="repo-a")
    assert pipeline.engine.templates()


def test_learned_context_appears_once_templates_exist():
    pipeline = LearningPipeline(settings())
    for _ in range(3):
        pipeline.learn_from_run(make_state(), repository_id="repo-a")
    context = pipeline.context_for("repo-a", "missing-key")
    assert context["template_id"] is not None


# -- API -------------------------------------------------------------------


def served_paths() -> set[str]:
    from backend.main import create_app

    return set(create_app().openapi()["paths"])


def test_learning_routes_are_registered():
    paths = served_paths()
    for path in (
        "/api/learning/dashboard",
        "/api/learning/metrics",
        "/api/learning/templates",
        "/api/learning/patterns",
        "/api/learning/organization",
        "/api/learning/repositories/{repository_id}",
        "/api/learning/context/{repository_id}",
        "/api/learning/repairs/{repair_id}/outcome",
        "/api/learning/repairs/{repair_id}/review",
    ):
        assert path in paths, path


def test_earlier_phase_routes_are_untouched():
    """Phase 6 is additive: nothing that existed may have moved."""
    paths = served_paths()
    assert "/api/runs" in paths
    assert any(p.startswith("/api/knowledge") for p in paths)
    assert any(p.startswith("/api/security") for p in paths)


def test_all_learning_routes_are_namespaced():
    from backend.api.routes import learning

    assert all(r.path.startswith("/api/learning") for r in learning.router.routes)


@pytest.mark.asyncio
async def test_dashboard_endpoint(store):
    from backend.api.routes import learning as api

    config = settings()
    get_learning_pipeline(config, store).learn_from_run(make_state(), repository_id="repo-a")
    assert (await api.dashboard(store, config))["enabled"] is True


@pytest.mark.asyncio
async def test_repository_endpoint_404s_for_an_unknown_repository(store):
    from fastapi import HTTPException

    from backend.api.routes import learning as api

    with pytest.raises(HTTPException) as excinfo:
        await api.repository_profile("never-seen", store, settings())
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_repairs_endpoint_returns_metadata_only(store):
    from backend.api.routes import learning as api

    config = settings()
    pipeline = get_learning_pipeline(config, store)
    pipeline.learn_from_run(make_state(), repository_id="repo-a")

    payload = await api.repairs(store, config, repository_id=None, limit=10)
    assert payload
    assert "original" not in str(payload)
    assert "patched" not in str(payload)


@pytest.mark.asyncio
async def test_outcome_endpoint_records(store):
    from backend.api.routes import learning as api

    config = settings()
    pipeline = get_learning_pipeline(config, store)
    knowledge = pipeline.learn_from_run(make_state(), repository_id="repo-a")

    body = api.OutcomeBody(status="merged", detail="shipped")
    result = await api.record_outcome(knowledge.repair_id, body, store, config)
    assert result["status"] == "merged"


@pytest.mark.asyncio
async def test_review_endpoint_categorises(store):
    from backend.api.routes import learning as api

    config = settings()
    pipeline = get_learning_pipeline(config, store)
    knowledge = pipeline.learn_from_run(make_state(), repository_id="repo-a")

    body = api.ReviewBody(decision="minor_edits", reason="needs a security review")
    result = await api.record_review(knowledge.repair_id, body, store, config)
    assert "security" in result["categories"]
