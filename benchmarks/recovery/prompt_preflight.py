"""Inspect approved repair-agent prompt boundaries without a provider call.

The real security approval path runs, then an injected exception stops before
transport. No prompt text is written to disk; the report contains metadata and
hashes only. This is an offline audit, not a live model evaluation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from unittest.mock import patch

import fakeredis.aioredis

from benchmarks.recovery.agent_cases import cases
from benchmarks.recovery.agent_preflight import _run_case
from backend.config import Settings
from backend.security.secret_scanner import scan_text
from backend.services.llm_gateway import LLMGateway
from backend.state.redis_store import RedisStore

OUTPUT = Path(__file__).with_name("prompt-preflight.json")
HOST_PATH = re.compile(r"/(?:Users|home|private|tmp|var)/[A-Za-z0-9_./-]+")


async def run_prompt_preflight() -> dict:
    case = cases()[0]
    settings = Settings(
        stub_mode=False, llm_provider="mistral",
        mistral_api_key="offline-prompt-capture",
        github_dry_run=True, github_token="", max_retries=1,
        learning_enabled=False, repair_memory_enabled=False,
        audit_store_raw_prompts=False,
    )
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    approved_calls: list[dict] = []
    original_approve = LLMGateway._approve

    async def capture_approval(self, prompt: str, system: str, **context):
        approved = await original_approve(self, prompt, system, **context)
        if approved is None:
            raise AssertionError("Security approval was disabled")
        content = approved.prompt + "\n" + approved.system
        approved_calls.append({
            "agent": context.get("agent_id"),
            "operation": context.get("operation"),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "chars": len(content),
            "secret_findings": len(scan_text(content).findings),
            "host_path_present": bool(HOST_PATH.search(content)),
            "contains_issue_description": case.description in content,
            "contains_complete_source": case.source in content,
            "contains_complete_test": case.test in content,
            "mentions_requirements_file": "requirements.txt" in content,
            "contains_test_token_payload": bool(re.search(r"eyJ[A-Za-z0-9_-]{20,}", content)),
            "mentions_target_source": "auth.py" in content,
            "mentions_controlled_test": "test_reported_issue" in content,
            "mentions_pytest": "pytest" in content,
        })
        raise RuntimeError("Offline prompt capture: provider transport disabled")

    async def forbid_dispatch(self, *args, **kwargs):
        raise AssertionError("Offline prompt preflight reached provider dispatch")

    try:
        with patch.object(LLMGateway, "_approve", capture_approval), patch.object(
            LLMGateway, "_dispatch", forbid_dispatch,
        ):
            case_result = await _run_case(case, RedisStore(client, settings), settings)
    finally:
        await client.aclose()

    return {
        "mode": "offline_approved_prompt_capture",
        "provider_calls": 0,
        "case": case_result["case"],
        "environment_status": case_result["environment_status"],
        "reproduction_status": case_result["reproduction_status"],
        "resolved_target": case_result["resolved_target"],
        "approved_prompt_count": len(approved_calls),
        "approved_prompts": approved_calls,
        "raw_prompts_stored": False,
        "model_quality_measured": False,
    }


def main() -> None:
    result = asyncio.run(run_prompt_preflight())
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "approved_prompt_count": result["approved_prompt_count"],
        "agents": [call["agent"] for call in result["approved_prompts"]],
        "output": str(OUTPUT),
    }))


if __name__ == "__main__":
    main()
