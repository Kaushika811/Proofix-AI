"""CLI helper to verify Redis connectivity."""

import asyncio

from backend.config import get_settings
from backend.state.redis_store import create_redis_client


async def main() -> None:
    settings = get_settings()
    client = await create_redis_client(settings)
    try:
        pong = await client.ping()
        print(f"Redis OK: {pong}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
