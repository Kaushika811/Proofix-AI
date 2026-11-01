"""Real pytest evidence flowing into the production recovery classifier."""

from pathlib import Path

import pytest

from backend.services.evidence_recovery import record_attempt
from backend.services.scoped_validation import run_scoped_validation
from backend.state.schema import RunStateModel


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture_failure", [False, True])
async def test_runtime_exception_routes_by_pytest_phase(tmp_path: Path, fixture_failure: bool):
    (tmp_path / "app.py").write_text('def value(data):\n    return data["id"]\n')
    (tmp_path / "test_app.py").write_text(
        "import pytest\nfrom app import value\n\n"
        "@pytest.fixture\ndef data():\n"
        + (
            '    raise TypeError("fixture configuration invalid")\n'
            if fixture_failure
            else "    return None\n"
        )
        + "\ndef test_value(data):\n    assert value(data) == 1\n"
    )
    outcome = await run_scoped_validation(
        tmp_path,
        f"phase-{tmp_path.name}",
        target_test="test_app.py::test_value",
        baseline_failures=[],
    )
    assert outcome.validation_failure is not None
    assert outcome.validation_failure.pytest_phase == ("setup" if fixture_failure else "call")
    state = RunStateModel(
        run_id="phase",
        repo_path=str(tmp_path),
        patch_bundle={
            "patches": [
                {"file": "app.py", "original": "old", "patched": (tmp_path / "app.py").read_text()}
            ]
        },
        mutation_result={
            "pytest_available": outcome.pytest_available,
            "pytest_passed": outcome.pytest_passed,
            "target_test_passed": outcome.target_test_passed,
            "validation_failure": outcome.validation_failure.model_dump(),
        },
    )
    record_attempt(state, 3)
    assert state.recovery_decision["route"] == (
        "recovery_stop" if fixture_failure else "generate_code"
    )
