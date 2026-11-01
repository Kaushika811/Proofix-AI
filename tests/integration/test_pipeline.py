import uuid
from pathlib import Path

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.orchestrator.runner import PipelineRunner
from backend.state.redis_store import RedisStore


VULNAPI_PATH = str(Path(__file__).parent.parent.parent / "vulnapi")


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_pipeline_end_to_end(redis_client):
    settings = Settings(stub_mode=True, github_dry_run=True, redis_url="redis://localhost:6379/0")
    store = RedisStore(redis_client, settings)
    runner = PipelineRunner(store, settings)

    run_id = str(uuid.uuid4())
    await store.init_run(run_id, VULNAPI_PATH)

    result = await runner.execute(run_id)
    # Stub A7 returns unchanged files; a run without a substantive patch must stop.
    assert result.status == "blocked"
    assert "substantive patch" in result.recovery_blocked_reason
    assert result.sig is not None
    assert result.cve_report is not None
    assert result.static_report is not None
    assert result.reproduction is not None
    assert result.patch_bundle is None

    events = await store.get_events(run_id)
    agent_ids = {e.agent_id for e in events}
    assert "A1" in agent_ids or "A1+A2+A3" in {e.agent_id for e in events}
    assert "A7" in agent_ids
