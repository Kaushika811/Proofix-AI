"""Unit tests for A7 validation retry metrics and prompt enrichment."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.a7_code_generation import A7CodeGenerationAgent
from backend.agents.a7_patch_engine import PatchLLMOutput
from backend.models.patch import PatchPlan
from backend.models.validation import RetryBrief, ValidationFailure

AUTH_SOURCE = "def validate_token():\n    return True\n"


@pytest.mark.asyncio
async def test_generate_from_plan_sets_validation_retry_reason():
    agent = A7CodeGenerationAgent(MagicMock(), MagicMock())
    agent.settings = MagicMock(stub_mode=False, llm_configured=lambda: True)

    retry_brief = RetryBrief(
        attempt=1,
        assertion_failure="AssertionError: assert True is False",
        validation_failure=ValidationFailure(
            failing_test="tests/test_auth.py::test_expired_token_rejected",
            assertion_message="AssertionError: assert True is False",
            expected_value="False",
            actual_value="True",
            validation_stage="mutation",
        ),
    )
    plan = PatchPlan(file="vulnapi/auth.py", root_cause="expiry", required_behavior_change="reject expired")

    with patch.object(
        agent,
        "_call_llm_with_integrity_guard",
        AsyncMock(
            return_value=PatchLLMOutput(
                patched_content="def validate_token():\n    return False\n",
                contract_assertion="reject expired",
                contract_location="vulnapi/auth.py",
            )
        ),
    ):
        output, metrics = await agent._generate_from_plan(
            plan,
            AUTH_SOURCE,
            "",
            retry_brief,
            {"pytest_passed": False},
            {"original": AUTH_SOURCE, "patched": AUTH_SOURCE},
            MagicMock(),
            retry_count=1,
        )

    assert output is not None
    assert metrics["retry_number"] == 1
    assert metrics["retry_reason"] is not None
    assert metrics["retry_reason"].startswith("pytest:")


@pytest.mark.asyncio
async def test_generate_from_plan_retry_zero_has_no_validation_reason():
    agent = A7CodeGenerationAgent(MagicMock(), MagicMock())
    agent.settings = MagicMock(stub_mode=False, llm_configured=lambda: True)
    plan = PatchPlan(file="vulnapi/auth.py", root_cause="expiry", required_behavior_change="reject expired")

    with patch.object(
        agent,
        "_call_llm_with_integrity_guard",
        AsyncMock(
            return_value=PatchLLMOutput(
                patched_content="def validate_token():\n    return False\n",
                contract_assertion="reject expired",
                contract_location="vulnapi/auth.py",
            )
        ),
    ):
        _output, metrics = await agent._generate_from_plan(
            plan,
            AUTH_SOURCE,
            "",
            None,
            None,
            None,
            MagicMock(),
            retry_count=0,
        )

    assert metrics["retry_number"] == 0
    assert metrics["retry_reason"] is None
