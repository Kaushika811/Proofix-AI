"""Lifecycle events emitted by the real runner (G5, integration).

The unit tests cover the store's once-only guarantee. These run the actual
pipeline and assert the runner announces the boundaries of a run — and that a
failure to announce can never turn a successful run into a failed one.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.orchestrator.runner import PipelineRunner
from backend.state.redis_store import RedisStore

VULNAPI_PATH = str(Path(__file__).parent.parent.parent / "vulnapi")


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    settings = Settings(stub_mode=True, github_dry_run=True, redis_url="redis://localhost:6379/0")
    yield RedisStore(client, settings), settings
    await client.aclose()


async def test_successful_run_announces_start_and_completion(store):
    redis_store, settings = store
    runner = PipelineRunner(redis_store, settings)
    run_id = str(uuid.uuid4())
    await redis_store.init_run(run_id, VULNAPI_PATH)

    await runner.execute(run_id)

    events = await redis_store.get_lifecycle_events(run_id)
    assert [e.type for e in events] == ["run.started", "run.blocked"]

    started, completed = events
    assert started.run_id == run_id
    assert started.timestamp is not None
    assert completed.decision_label, "completion must report how the run ended"
    assert "substantive patch" in completed.reason


async def test_completed_run_current_agent_is_not_stale(store):
    # B-B20: `current_agent` used to freeze at "fan-in" (set by
    # `layer1_fan_in`) because no node after it ever updated the pointer, so a
    # completed run reported the wrong "current agent" forever.
    redis_store, settings = store
    runner = PipelineRunner(redis_store, settings)
    run_id = str(uuid.uuid4())
    await redis_store.init_run(run_id, VULNAPI_PATH)

    result = await runner.execute(run_id)

    assert result.current_agent == "A7"


async def test_completion_reports_the_routing_decision(store):
    redis_store, settings = store
    runner = PipelineRunner(redis_store, settings)
    run_id = str(uuid.uuid4())
    await redis_store.init_run(run_id, VULNAPI_PATH)

    result = await runner.execute(run_id)

    completed = (await redis_store.get_lifecycle_events(run_id))[-1]
    # The decision is the pipeline's own, never a substitute.
    assert completed.decision == (result.pr_decision or {}).get("pr_type")


async def test_failed_run_announces_the_reason(store):
    redis_store, settings = store
    runner = PipelineRunner(redis_store, settings)
    run_id = str(uuid.uuid4())
    await redis_store.init_run(run_id, VULNAPI_PATH)

    runner.graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph exploded"))

    with pytest.raises(RuntimeError):
        await runner.execute(run_id)

    events = await redis_store.get_lifecycle_events(run_id)
    assert [e.type for e in events] == ["run.started", "run.failed"]
    failed = events[-1]
    assert failed.reason == "RuntimeError: graph exploded"
    assert failed.decision is None


async def test_a_run_that_never_existed_announces_nothing(store):
    redis_store, settings = store
    runner = PipelineRunner(redis_store, settings)
    missing = str(uuid.uuid4())

    with pytest.raises(ValueError):
        await runner.execute(missing)

    assert await redis_store.get_lifecycle_events(missing) == []


async def test_lifecycle_failure_does_not_fail_the_run(store):
    """Telemetry must never decide a run's outcome.

    A run that finished but could not announce it is still a run that finished;
    raising here would turn a Redis hiccup into a failed repair.
    """
    redis_store, settings = store
    runner = PipelineRunner(redis_store, settings)
    run_id = str(uuid.uuid4())
    await redis_store.init_run(run_id, VULNAPI_PATH)

    redis_store.append_lifecycle_event = AsyncMock(side_effect=ConnectionError("redis down"))

    result = await runner.execute(run_id)

    assert result.status == "blocked"


async def test_re_executing_a_run_does_not_duplicate_completion(store):
    redis_store, settings = store
    runner = PipelineRunner(redis_store, settings)
    run_id = str(uuid.uuid4())
    await redis_store.init_run(run_id, VULNAPI_PATH)

    await runner.execute(run_id)
    await runner.execute(run_id)

    events = await redis_store.get_lifecycle_events(run_id)
    assert [e.type for e in events].count("run.blocked") == 1
    assert [e.type for e in events].count("run.started") == 1
