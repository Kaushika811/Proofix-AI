from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, Request

from backend.config import Settings, get_settings
from backend.orchestrator.runner import PipelineRunner
from backend.state.redis_store import RedisStore


async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis


async def get_store(redis: Annotated[aioredis.Redis, Depends(get_redis)]) -> RedisStore:
    return RedisStore(redis)


async def get_runner(
    request: Request,
    store: Annotated[RedisStore, Depends(get_store)],
) -> PipelineRunner:
    return PipelineRunner(store, request.app.state.settings)


def get_settings_dep() -> Settings:
    return get_settings()
