"""Stage status against a real pipeline run (R4, integration).

The unit tests pin the rollup rules. This runs the actual graph and asserts the
projection describes a finished run as finished — the defect was observable
only end to end, because it needed A8 to settle on `retry` while A10 still
issued a decision.
"""

import uuid
from pathlib import Path

import fakeredis.aioredis
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.config import Settings
from backend.main import create_app
from backend.orchestrator.runner import PipelineRunner
from backend.services.ui_projection import build_stage_progress
from backend.state.redis_store import RedisStore

VULNAPI_PATH = str(Path(__file__).parent.parent.parent / "vulnapi")
IN_FLIGHT = {"running", "retrying"}


@pytest_asyncio.fixture
async def finished_run():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    settings = Settings(stub_mode=True, github_dry_run=True, redis_url="redis://localhost:6379/0")
    store = RedisStore(client, settings)
    runner = PipelineRunner(store, settings)

    run_id = str(uuid.uuid4())
    await store.init_run(run_id, VULNAPI_PATH)
    await runner.execute(run_id)

    state = await store.load_state(run_id)
    events = await store.get_events(run_id, count=500)
    yield run_id, state, events, store, settings
    await client.aclose()


async def test_no_stage_is_in_flight_after_the_run_settles(finished_run):
    _run_id, state, events, _store, _settings = finished_run
    stages = build_stage_progress(state, events)

    assert state.status == "blocked"
    in_flight = [(s["id"], s["status"]) for s in stages if s["status"] in IN_FLIGHT]
    assert in_flight == [], f"finished run still reports in-flight stages: {in_flight}"


async def test_validation_stage_settles_even_when_an_agent_ended_in_retry(finished_run):
    _run_id, state, events, _store, _settings = finished_run
    stages = {s["id"]: s for s in build_stage_progress(state, events)}

    agent_statuses = {a["agentId"]: a["status"] for a in stages["validation"]["agents"]}
    # The underlying agent status is unchanged — only the rollup was wrong.
    assert all(status not in IN_FLIGHT for status in agent_statuses.values())
    assert stages["validation"]["status"] not in IN_FLIGHT


async def test_learning_stage_claims_completion_only_on_evidence(finished_run):
    _run_id, state, events, _store, _settings = finished_run
    stages = {s["id"]: s for s in build_stage_progress(state, events)}

    observed = any(isinstance((e.payload or {}).get("learning"), dict) for e in events)
    expected = "completed" if observed else "skipped"
    assert stages["learning"]["status"] == expected


async def test_stages_endpoint_reports_the_same_settled_view(finished_run):
    run_id, _state, _events, store, settings = finished_run

    app = create_app()
    app.state.redis = store.client
    app.state.settings = settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        body = (await http.get(f"/api/runs/{run_id}/stages")).json()

    assert [s["status"] for s in body["stages"] if s["status"] in IN_FLIGHT] == []
    assert len(body["stages"]) == 7
