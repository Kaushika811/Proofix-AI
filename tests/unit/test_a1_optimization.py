from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.agents.a1_semantic_mapper import A1SemanticMapperAgent
from backend.config import Settings
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

VULNAPI = Path(__file__).parent.parent.parent / "vulnapi"


@pytest_asyncio.fixture
async def redis_store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisStore(client, Settings(stub_mode=True, sig_cache_enabled=False))
    yield store
    await client.aclose()


@pytest.mark.asyncio
async def test_a1_emits_metrics(redis_store: RedisStore):
    agent = A1SemanticMapperAgent(redis_store, Settings(stub_mode=True, sig_cache_enabled=False))
    state = RunStateModel(run_id="r1", repo_path=str(VULNAPI), repo_clone_path=str(VULNAPI))

    with patch.object(agent, "emit_status", new_callable=AsyncMock) as mock_emit:
        await agent.run(state)

    completed = [c for c in mock_emit.call_args_list if c.args[1] == "completed"]
    assert completed
    payload = completed[-1].kwargs.get("payload") or completed[-1].args[3]
    metrics = payload["a1_metrics"]
    assert metrics["total_files"] >= 1
    assert "cache_hit" in metrics
    assert "parse_count" in metrics
    assert metrics["llm_calls"] == 0


@pytest.mark.asyncio
async def test_stub_mode_skips_cache(redis_store: RedisStore):
    agent = A1SemanticMapperAgent(
        redis_store,
        Settings(stub_mode=True, sig_cache_enabled=True),
    )
    state = RunStateModel(run_id="r2", repo_path=str(VULNAPI), repo_clone_path=str(VULNAPI))

    with patch.object(redis_store, "get_sig_cache", new_callable=AsyncMock) as mock_get:
        await agent.run(state)
        mock_get.assert_not_called()
