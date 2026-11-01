"""Replay the live audit failures using real pytest and production gates."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.a3_5_reproduction import A35ReproductionAgent
from backend.agents.a8_mutation_validator import A8MutationValidatorAgent
from backend.config import Settings
from backend.services.acceptance_test_integrity import pin_acceptance_test
from backend.services.patch_causality import evaluate_patch_causality
from backend.state.schema import RunStateModel
from benchmarks.recovery.agent_cases import cases


def store():
    result = MagicMock()
    result.append_event = AsyncMock()
    result.set_json = AsyncMock()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("case_index", [0, 1])
async def test_live_audit_patch_preserves_reproduction_identity(tmp_path, case_index):
    case = cases()[case_index]
    source = tmp_path / case.source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(case.source)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_reported_issue.py").write_text(case.test)
    state = RunStateModel(run_id=f"causality-{tmp_path.name}", repo_path=str(tmp_path),
                          issue_contract=case.issue)
    await A35ReproductionAgent(store(), Settings(stub_mode=True)).run(state)
    assert state.reproduction["status"] == "CONFIRMED"
    source.write_text(case.correct_source)
    evidence = await evaluate_patch_causality(
        tmp_path, [{"file": case.source_path, "original": case.source,
                    "patched": case.correct_source}], state.acceptance_test,
        expected_failure=state.reproduction["exception_message"],
        expected_failure_file=state.reproduction["failing_file"],
        expected_failure_line=state.reproduction["failing_line"],
    )
    assert evidence["causal_effect_supported"] is True, evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_count,route", [(0, "generate_code"), (3, "recovery_stop")])
async def test_pinned_acceptance_test_retains_bounded_patch_retry(tmp_path, retry_count, route):
    original = "def value():\n    return 0\n"
    patched = "def value():\n    return 2\n"
    (tmp_path / "app.py").write_text(patched)
    (tmp_path / "test_app.py").write_text(
        "from app import value\ndef test_value():\n    assert value() == 1\n"
    )
    state = RunStateModel(
        run_id=f"retry-{tmp_path.name}", repo_path=str(tmp_path), retry_count=retry_count,
        issue_contract={"description": "Return one", "target_file": "app.py",
                        "target_test": "test_app.py::test_value"},
        acceptance_test=pin_acceptance_test(tmp_path, "test_app.py::test_value"),
        reproduction={"status": "CONFIRMED", "failing_test": "test_app.py::test_value"},
        patch_bundle={"patches": [{"file": "app.py", "original": original,
                                    "patched": patched, "target_symbol": "value"}]},
    )
    await A8MutationValidatorAgent(store(), Settings(stub_mode=True, max_retries=3)).run(state)
    assert state.mutation_result["target_test_passed"] is False
    assert state.recovery_decision["category"] == "implementation_failure"
    assert state.recovery_decision["route"] == route
    assert state.mutation_result["patch_causality"] is None
