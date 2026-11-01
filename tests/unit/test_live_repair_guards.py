"""Regression checks for the production repair path, not live-model quality scores."""

from unittest.mock import AsyncMock, MagicMock
import json

import pytest

from backend.agents.a7_code_generation import A7CodeGenerationAgent
from backend.services.evidence_recovery import record_attempt
from backend.services.privacy_guard import scan_text
from backend.services.context_package import _sanitize_evidence
from backend.services.reproduction_parser import parse_pytest_report
from backend.services.runtime_patch_prompt import _function_at_line, _function_from_failing_test
from backend.services.validation_failure_parser import parse_validation_failure
from backend.services.issue_targeting import issue_repair_scope
from backend.state.schema import RunStateModel


@pytest.mark.parametrize(
    "error", ["TypeError", "AttributeError", "KeyError", "ValueError", "SyntaxError"]
)
def test_application_exception_revises_patch(error):
    state = RunStateModel(
        run_id="app",
        repo_path=".",
        patch_bundle={"patches": [{"file": "app.py", "original": "old", "patched": "new"}]},
        mutation_result={
            "pytest_available": True,
            "pytest_passed": False,
            "target_test_passed": False,
            "validation_failure": {"pytest_phase": "call", "traceback": f"{error}: bad result"},
        },
    )
    record_attempt(state, 3)
    assert state.recovery_decision["route"] == "generate_code"
    assert state.recovery_decision["category"] == "implementation_failure"


@pytest.mark.parametrize("phase", ["setup", "teardown"])
def test_fixture_errors_do_not_become_application_repairs(tmp_path, phase):
    test = {
        "nodeid": "test_app.py::test_app",
        "outcome": "error",
        phase: {
            "outcome": "failed",
            "crash": {"message": "ValueError: missing configuration"},
            "longrepr": "ValueError: missing configuration",
        },
    }
    report = {"exitcode": 1, "summary": {"collected": 1}, "tests": [test]}
    result = parse_pytest_report(
        report, 1, "", "", tmp_path / "report.json", requested_test=test["nodeid"]
    )
    assert result.status == "INFRA_ERROR"
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    failure = parse_validation_failure(exit_code=1, stdout="", stderr="", report_path=path)
    assert failure.pytest_phase == phase
    assert failure.exception_type == "ValueError"


@pytest.mark.parametrize(
    "error", ["PackageNotFoundError", "ModuleNotFoundError", "ConnectionError"]
)
def test_runtime_prerequisites_are_not_confirmed_bugs(tmp_path, error):
    report = {
        "exitcode": 1,
        "summary": {"collected": 1},
        "tests": [
            {
                "nodeid": "test_app.py::test_app",
                "outcome": "failed",
                "call": {
                    "outcome": "failed",
                    "crash": {"message": f"{error}: absent"},
                    "longrepr": f"{error}: absent",
                },
            }
        ],
    }
    result = parse_pytest_report(report, 1, "", "", tmp_path / "r.json")
    assert result.status == "INFRA_ERROR"
    assert result.confidence == 0


@pytest.mark.parametrize(
    "path",
    [
        "/opt/homebrew/Cellar/python@3.14/lib/importlib/metadata/__init__.py",
        "/Users/dana@corp/project/app.py",
        "/srv/build@2/pkg/mod.py",
        "/Users/ada@example.com/project/app.py",
    ],
)
def test_email_guard_preserves_traceback_frame_paths(path):
    trace = f'File "{path}", line 9, in handler\ncontact ada@example.com'
    result = scan_text(trace)
    assert path in result.source
    assert "contact <REDACTED_EMAIL>" in result.source


def test_context_normalizes_repository_paths_before_email_scan(tmp_path):
    cleaned, _ = _sanitize_evidence({"traceback": f'File "{tmp_path}/app.py", line 4'}, tmp_path)
    assert cleaned["traceback"] == 'File "app.py", line 4'


def test_line_resolution_requires_function_containment():
    source = "def first():\n    return 1\n\nx = 2\n"
    assert _function_at_line(source, 2) == "first"
    assert _function_at_line(source, 4) is None


def test_selected_test_and_import_aliases_drive_target(tmp_path):
    (tmp_path / "app.py").write_text("def wrong():\n    return 0\n\ndef right():\n    return 1\n")
    (tmp_path / "test_app.py").write_text(
        "from app import wrong, right as selected\n"
        "def test_other():\n    assert wrong()\n\nclass TestApp:\n"
        "    def test_selected(self):\n        assert selected()\n"
    )
    assert (
        _function_from_failing_test(
            {"failing_test": "test_app.py::TestApp::test_selected[param]"}, "app.py", tmp_path
        )
        == "right"
    )


def test_selected_fixture_and_module_alias_drive_target(tmp_path):
    (tmp_path / "app.py").write_text("def right():\n    return 1\n")
    (tmp_path / "conftest.py").write_text(
        "import pytest\nimport app as subject\n"
        "@pytest.fixture\ndef value():\n    return subject.right()\n"
    )
    (tmp_path / "test_app.py").write_text("def test_selected(value):\n    assert value\n")
    assert (
        _function_from_failing_test(
            {"failing_test": "test_app.py::test_selected"}, "app.py", tmp_path
        )
        == "right"
    )


def scoped_state(tmp_path):
    return RunStateModel(
        run_id="scoped",
        repo_path=str(tmp_path),
        issue_contract={
            "description": "fix behavior",
            "target_test": "tests/test_app.py::test_app",
            "target_file": "app.py",
        },
        reproduction={"status": "CONFIRMED", "failing_test": "tests/test_app.py::test_app"},
        blast_graph={
            "auto_patch_scope": ["app.py", "helper.py", "unrelated.py", "tests/test_app.py"],
            "target_resolution": {
                "resolved_path": "app.py",
                "source": "issue_contract",
                "original_path": "app.py",
                "normalized_path": "app.py",
                "confidence": 1.0,
                "runtime_confirmed": True,
            },
        },
        root_cause={
            "citations": [
                {"file": name, "line": 1, "claim": "involved", "source_located": True}
                for name in ["helper.py", "tests/test_app.py"]
            ]
        },
    )


def test_supporting_scope_requires_evidence_and_protects_tests(tmp_path):
    assert issue_repair_scope(scoped_state(tmp_path)) == ["app.py", "helper.py"]


@pytest.mark.asyncio
async def test_early_generation_error_preserves_original_exception(tmp_path, monkeypatch):
    store = MagicMock()
    store.acquire_lock = AsyncMock(return_value=True)
    store.release_lock = AsyncMock()
    agent = A7CodeGenerationAgent(store, MagicMock())
    agent.emit_status = AsyncMock()
    monkeypatch.setattr(
        "backend.agents.a7_code_generation.load_context_package",
        AsyncMock(side_effect=RuntimeError("context unavailable")),
    )
    with pytest.raises(RuntimeError, match="context unavailable"):
        await agent.run(RunStateModel(run_id="early", repo_path=str(tmp_path)))
    store.release_lock.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_lock_does_not_release_another_owners_lock(tmp_path):
    store = MagicMock()
    store.acquire_lock = AsyncMock(return_value=False)
    store.release_lock = AsyncMock()
    agent = A7CodeGenerationAgent(store, MagicMock())
    agent.emit_status = AsyncMock()
    state = await agent.run(RunStateModel(run_id="lock", repo_path=str(tmp_path)))
    assert state.recovery_blocked_reason
    store.release_lock.assert_not_awaited()


@pytest.mark.asyncio
async def test_a6_a7_execute_complete_evidence_backed_multifile_plan(tmp_path, monkeypatch):
    from backend.agents.a6_fix_dag_planner import A6FixDAGPlannerAgent
    from backend.agents.a7_patch_engine import PatchLLMOutput
    from backend.config import Settings

    state = scoped_state(tmp_path)
    files = ["app.py", "helper.py", "third.py", "fourth.py"]
    state.blast_graph["auto_patch_scope"] += files[2:]
    state.root_cause["citations"] += [
        {"file": f, "line": 1, "claim": "involved", "source_located": True} for f in files[2:]
    ]
    for f in files:
        (tmp_path / f).write_text("def value():\n    return 0\n")
    store = MagicMock()
    for name in ["get_json", "set_json", "release_lock"]:
        setattr(store, name, AsyncMock(return_value=None))
    store.acquire_lock = AsyncMock(return_value=True)
    store.renew_lock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "backend.agents.a6_fix_dag_planner.load_context_package", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "backend.agents.a7_code_generation.load_context_package", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "backend.agents.a7_code_generation.get_style_exemplar", lambda *a: (None, "")
    )
    planner = A6FixDAGPlannerAgent(store, Settings(stub_mode=True))
    planner.emit_status = AsyncMock()
    state = await planner.run(state)
    assert state.fix_dag["nodes"][0]["files"] == files
    agent = A7CodeGenerationAgent(store, Settings(stub_mode=True))
    agent.emit_status = AsyncMock()
    agent._load_intelligence = AsyncMock(return_value=None)
    agent._generate_from_plan = AsyncMock(
        return_value=(
            PatchLLMOutput(
                patched_content="def value():\n    return 1\n",
                contract_assertion="value is one",
                contract_location="value",
            ),
            {},
        )
    )
    state = await agent.run(state)
    assert not state.recovery_blocked_reason
    assert {p["file"] for p in state.patch_bundle["patches"]} == set(files)
    assert agent._generate_from_plan.await_count == 4
