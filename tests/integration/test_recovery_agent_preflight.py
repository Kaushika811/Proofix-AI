"""Keep the offline agent-path evaluation calibrated and honest."""

import pytest
import fakeredis.aioredis

from benchmarks.recovery.agent_cases import cases
from benchmarks.recovery.agent_preflight import _run_case, run_preflight
from benchmarks.recovery.prompt_preflight import run_prompt_preflight
from backend.config import Settings
from backend.state.redis_store import RedisStore


@pytest.mark.asyncio
async def test_preflight_calibrates_behavior_before_real_agent_path():
    report = await run_preflight()

    assert report["live_model_measured"] is False
    assert report["independent_real_world_defects_measured"] == 0
    assert report["case_count"] == 3
    for case in report["cases"]:
        assert case["calibration"]["original"]["assertion_failure"] is True
        assert case["calibration"]["correct_fix"]["target_test_passed"] is True
        assert case["calibration"]["incorrect_patch"]["assertion_failure"] is True
        assert case["reproduction_status"] == "CONFIRMED"
        assert case["resolved_target"] == case["issue_contract"]["target_file"]
        assert case["patch_count"] == 0
        assert case["status"] == "blocked"
        assert case["repair_correctness"] is None


@pytest.mark.asyncio
async def test_isolated_live_fixture_passes_real_environment_gate_without_model():
    settings = Settings(
        stub_mode=False, llm_provider="mistral", mistral_api_key="",
        github_dry_run=True, github_token="", max_retries=1,
        learning_enabled=False, repair_memory_enabled=False,
    )
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        result = await _run_case(cases()[0], RedisStore(client, settings), settings)
    finally:
        await client.aclose()

    assert result["environment_status"] == "ready"
    assert result["reproduction_status"] == "CONFIRMED"
    assert result["resolved_target"] == "vulnapi/auth.py"
    assert result["root_cause_source"] == "deterministic"


@pytest.mark.asyncio
async def test_repair_prompt_preflight_stops_before_provider_transport():
    report = await run_prompt_preflight()

    assert report["provider_calls"] == 0
    assert report["model_quality_measured"] is False
    assert report["environment_status"] == "ready"
    assert report["reproduction_status"] == "CONFIRMED"
    assert [item["agent"] for item in report["approved_prompts"]] == [
        "A1", "A4", "A6", "A7",
    ]
    assert all(item["secret_findings"] == 0 for item in report["approved_prompts"])
    assert all(not item["host_path_present"] for item in report["approved_prompts"])
    assert all(not item["mentions_requirements_file"] for item in report["approved_prompts"])
