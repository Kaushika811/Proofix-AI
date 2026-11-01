"""Unit tests for A7 patch integrity guard — abbreviated LLM output must not be written."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.a7_code_generation import A7CodeGenerationAgent
from backend.agents.a7_patch_engine import PatchLLMOutput

VULNAPI = Path(__file__).parent.parent.parent / "vulnapi"
AUTH_SOURCE = (VULNAPI / "vulnapi/auth.py").read_text(encoding="utf-8")

ABBREVIATED = """
import base64
import json
import time

def validate_token(token: str) -> bool:
    if payload.get("exp", 0) < time.time():
        return False
    return True

# ... remainder of file unchanged ...
"""

COMPLETE_FIX = AUTH_SOURCE.replace(
    "# Missing: if payload.get(\"exp\", 0) < time.time(): return False",
    "if payload.get(\"exp\", 0) < time.time():\n        return False",
)


@pytest.mark.asyncio
async def test_integrity_guard_retries_abbreviated_then_accepts_complete():
    agent = A7CodeGenerationAgent(MagicMock(), MagicMock())
    llm = MagicMock()
    llm.structured = AsyncMock(
        side_effect=[
            PatchLLMOutput(
                patched_content=ABBREVIATED,
                contract_assertion="check expiry",
                contract_location="vulnapi/auth.py",
            ),
            PatchLLMOutput(
                patched_content=COMPLETE_FIX,
                contract_assertion="check expiry",
                contract_location="vulnapi/auth.py",
            ),
        ]
    )
    metrics: dict = {}
    original = AUTH_SOURCE

    result = await agent._call_llm_with_integrity_guard(llm, "prompt", "system", original, metrics)

    assert result is not None
    assert result.patched_content == COMPLETE_FIX
    assert metrics["retry_reason"] is None
    assert llm.structured.await_count == 2


@pytest.mark.asyncio
async def test_integrity_guard_rejects_persistent_abbreviated():
    agent = A7CodeGenerationAgent(MagicMock(), MagicMock())
    llm = MagicMock()
    llm.structured = AsyncMock(
        return_value=PatchLLMOutput(
            patched_content=ABBREVIATED,
            contract_assertion="check expiry",
            contract_location="vulnapi/auth.py",
        )
    )
    metrics: dict = {}

    result = await agent._call_llm_with_integrity_guard(llm, "prompt", "system", AUTH_SOURCE, metrics)

    assert result is None
    assert metrics["retry_reason"] == "abbreviated"
    assert llm.structured.await_count == 2


@pytest.mark.asyncio
async def test_run_skips_disk_write_when_integrity_fails(tmp_path: Path):
    auth_dir = tmp_path / "vulnapi"
    auth_dir.mkdir()
    auth_file = auth_dir / "auth.py"
    auth_file.write_text(AUTH_SOURCE, encoding="utf-8")

    agent = A7CodeGenerationAgent(MagicMock(), MagicMock())
    agent.settings = MagicMock(stub_mode=False, llm_configured=lambda: True)
    agent.store = MagicMock()
    agent.store.acquire_lock = AsyncMock(return_value=True)
    # The lease is renewed per plan (B-B08): 60 s was shorter than the LLM calls
    # it guarded, so the lock expired mid-generation.
    agent.store.renew_lock = AsyncMock(return_value=True)
    agent.store.append_event = AsyncMock()
    agent.store.set_json = AsyncMock()
    agent.store.release_lock = AsyncMock()

    abbreviated_output = PatchLLMOutput(
        patched_content=ABBREVIATED,
        contract_assertion="check expiry",
        contract_location="vulnapi/auth.py",
    )

    with (
        patch.object(agent, "_generate_from_plan", AsyncMock(return_value=(abbreviated_output, {}))),
    ):
        from backend.models.blast import BlastGraphResult
        from backend.models.root_cause import RootCauseBrief
        from backend.state.schema import RunStateModel

        state = RunStateModel(
            run_id="test-run",
            repo_path=str(tmp_path),
            repo_clone_path=str(tmp_path),
            blast_graph=BlastGraphResult(
                origins=["vulnapi/auth.py"],
                auto_patch_scope=["vulnapi/auth.py"],
            ).model_dump(),
            root_cause=RootCauseBrief(summary="expiry", root_cause="missing exp check").model_dump(),
        )

        await agent.run(state)

    assert auth_file.read_text(encoding="utf-8") == AUTH_SOURCE
