"""One isolated Mistral probe of the actual repair-agent path.

Only the expired-token case is selected. The case runner calibrates the test,
checks for secrets, enforces an exact two-file source/test scope, and uses a
temporary repository. GitHub publishing and raw-prompt audit storage are off.
Run only after separate authorization for repair-agent model processing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fakeredis.aioredis

from benchmarks.recovery.agent_cases import cases
from benchmarks.recovery.agent_preflight import _run_case
from backend.config import Settings
from backend.state.redis_store import RedisStore

OUTPUT = Path(__file__).with_name("live-agent-probe.json")


async def run_live_probe() -> dict:
    settings = Settings(
        stub_mode=False,
        llm_provider="mistral",
        llm_max_attempts=1,
        github_dry_run=True,
        github_token="",
        max_retries=1,
        learning_enabled=False,
        repair_memory_enabled=False,
        audit_store_raw_prompts=False,
    )
    if not settings.llm_configured():
        raise RuntimeError("Mistral is not configured; no live model call was made")
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        store = RedisStore(client, settings)
        case = cases()[0]
        result = await _run_case(case, store, settings)
        return {
            "mode": "live_actual_agents_isolated_probe",
            "provider": "mistral",
            "case_count": 1,
            "independent_real_world_defects_measured": 0,
            "cases": [result],
        }
    finally:
        await client.aclose()


def main() -> None:
    result = asyncio.run(run_live_probe())
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "case": result["cases"][0]["case"],
        "status": result["cases"][0]["status"],
        "patch_count": result["cases"][0]["patch_count"],
        "output": str(OUTPUT),
    }))


if __name__ == "__main__":
    main()
