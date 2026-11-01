from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.a7_code_generation import A7CodeGenerationAgent
from backend.agents.a7_patch_engine import PatchLLMOutput
from backend.models.patch import PatchPlan
from backend.state.schema import RunStateModel


async def _run(tmp_path: Path, monkeypatch, generated):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("def target():\n    return False\n")
    agent = A7CodeGenerationAgent(MagicMock(), MagicMock())
    agent.settings = MagicMock(stub_mode=False, llm_configured=lambda: True)
    agent.store = MagicMock()
    agent.store.acquire_lock = AsyncMock(return_value=True)
    agent.store.renew_lock = AsyncMock(return_value=True)
    agent.store.release_lock = AsyncMock()
    agent.store.set_json = AsyncMock()
    agent.emit_status = AsyncMock()
    agent._generate_from_plan = AsyncMock(side_effect=generated)
    agent._load_intelligence = AsyncMock(return_value=None)
    monkeypatch.setattr("backend.agents.a7_code_generation.load_context_package",
                        AsyncMock(return_value=None))
    monkeypatch.setattr("backend.agents.a7_code_generation.get_style_exemplar",
                        lambda *args: (None, ""))
    monkeypatch.setattr("backend.agents.a7_code_generation.build_patch_plans",
                        lambda *args, **kwargs: [
                            PatchPlan(file=name, root_cause="bug", required_behavior_change="fix target",
                                      target_function="target") for name in ("a.py", "b.py")
                        ])
    state = RunStateModel(
        run_id="coverage", repo_path=str(tmp_path), repo_clone_path=str(tmp_path),
        blast_graph={"auto_patch_scope": ["a.py", "b.py"]},
        fix_dag={"nodes": [{"issue_id": "one", "files": ["a.py", "b.py"]}]},
    )
    await agent.run(state)
    return agent, state


@pytest.mark.asyncio
async def test_missing_required_plan_item_blocks_before_a8(tmp_path, monkeypatch):
    patch = PatchLLMOutput(patched_content="def target():\n    return True\n",
                           contract_assertion="target returns true", contract_location="a.py")
    agent, state = await _run(tmp_path, monkeypatch, [(patch, {}), (None, {})])
    assert "b.py" in state.recovery_blocked_reason
    assert state.patch_bundle is None
    assert (tmp_path / "a.py").read_text() == "def target():\n    return False\n"
    agent.store.set_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_patch_blocks_before_a8(tmp_path, monkeypatch):
    agent, state = await _run(tmp_path, monkeypatch, [(None, {}), (None, {})])
    assert state.recovery_blocked_reason
    assert state.patch_bundle is None
    agent.store.set_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_revision_restores_pre_attempt_content_not_original_baseline(tmp_path, monkeypatch):
    # The immutable baseline predates the failed candidate currently on disk.
    # An aborted revision must restore that candidate, not silently rewrite history.
    monkeypatch.setattr(A7CodeGenerationAgent, "_resolve_original_baseline",
                        lambda *args: "def target():\n    return -1\n")
    patch = PatchLLMOutput(patched_content="def target():\n    return True\n",
                           contract_assertion="target returns true", contract_location="a.py")
    _, state = await _run(tmp_path, monkeypatch, [(patch, {}), (None, {})])
    assert state.recovery_blocked_reason
    assert (tmp_path / "a.py").read_text() == "def target():\n    return False\n"


@pytest.mark.asyncio
async def test_successful_writes_record_content_bound_evidence(tmp_path, monkeypatch):
    from backend.services.repair_step_evidence import patch_fingerprint
    patch = PatchLLMOutput(patched_content="def target():\n    return True\n",
                           contract_assertion="target returns true", contract_location="a.py")
    _, state = await _run(tmp_path, monkeypatch, [(patch, {}), (patch, {})])
    record = state.patch_bundle["application_evidence"]
    assert record["patch_hash"] == patch_fingerprint(state.patch_bundle)
    assert set(record["files"]) == {"a.py", "b.py"}
    assert "return True" in record["files"]["a.py"]["diff"]
    assert record["files"]["a.py"]["expectedOutcome"] == "fix target"
