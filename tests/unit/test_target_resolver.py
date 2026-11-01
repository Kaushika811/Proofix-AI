"""Unit tests for runtime-confirmed patch target resolution."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from backend.agents.a5_blast_graph import A5BlastGraphAgent
from backend.config import Settings
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.blast_traversal import traverse_multi_origin
from backend.services.target_resolver import (
    normalize_repo_path,
    pin_resolved_target,
    resolve_patch_target,
)
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

VULNAPI = Path(__file__).parent.parent.parent / "vulnapi"


def _vulnapi_sig() -> SemanticIntentGraph:
    return SemanticIntentGraph(
        repo_path=str(VULNAPI),
        source_roots=["vulnapi/"],
        files={
            "vulnapi/auth.py": FileNode(
                path="vulnapi/auth.py",
                role="auth-boundary",
                imports=["base64", "json", "time"],
                imported_by=["vulnapi/middleware.py"],
                criticality=0.665,
                churn_weight=0.0,
            ),
            "vulnapi/api.py": FileNode(
                path="vulnapi/api.py",
                role="public-api",
                imports=["sqlite3"],
                imported_by=["vulnapi/middleware.py"],
                criticality=0.595,
                churn_weight=0.0,
            ),
            "vulnapi/middleware.py": FileNode(
                path="vulnapi/middleware.py",
                role="auth-boundary",
                imports=["vulnapi"],
                imported_by=[],
                criticality=0.665,
                churn_weight=0.0,
            ),
        },
        edges=[
            ("vulnapi/auth.py", "base64"),
            ("vulnapi/middleware.py", "vulnapi"),
        ],
    )


def _confirmed_state(**overrides) -> RunStateModel:
    base = {
        "run_id": "test-run",
        "repo_path": str(VULNAPI),
        "repo_clone_path": str(VULNAPI),
        "sig": _vulnapi_sig().model_dump(mode="json"),
        "reproduction": {
            "status": "CONFIRMED",
            "failing_test": "tests/test_auth.py::test_expired_token_rejected",
            "failing_file": "tests/test_auth.py",
            "failing_line": 27,
            "traceback": 'File "tests/test_auth.py", line 27, in test_expired_token_rejected\nAssertionError',
        },
        "root_cause": {
            "summary": "validate_token in auth.py does not reject expired tokens",
            "root_cause": "The validate_token function in auth.py does not properly validate expired tokens.",
            "citations": [
                {
                    "file": "auth.py",
                    "line": 19,
                    "claim": "missing expiry validation",
                    "verified": False,
                }
            ],
            "evidence_refs": [
                {
                    "source": "runtime",
                    "file": "tests/test_auth.py",
                    "line": 27,
                    "claim": "expired token accepted",
                }
            ],
        },
        "static_report": {
            "prioritized": [
                {"id": "f1", "file": "vulnapi/api.py", "line": 10, "message": "SQL injection"},
            ]
        },
    }
    base.update(overrides)
    return RunStateModel(**base)


def test_normalize_absolute_path():
    sig = _vulnapi_sig()
    repo = VULNAPI
    raw = "/tmp/proofix_x/vulnapi/auth.py"
    assert normalize_repo_path(repo, raw, sig) == "vulnapi/auth.py"


def test_normalize_windows_path():
    sig = _vulnapi_sig()
    repo = VULNAPI
    raw = r"C:\repo\vulnapi\auth.py"
    assert normalize_repo_path(repo, raw, sig) == "vulnapi/auth.py"


def test_normalize_relative_path():
    sig = _vulnapi_sig()
    repo = VULNAPI
    assert normalize_repo_path(repo, "./auth.py", sig) == "vulnapi/auth.py"


def test_stack_trace_resolves_application_frame():
    sig = _vulnapi_sig()
    state = _confirmed_state(
        reproduction={
            "status": "CONFIRMED",
            "failing_file": "tests/test_auth.py",
            "traceback": (
                'File "tests/test_auth.py", line 27, in test_expired_token_rejected\n'
                'File "/tmp/repo/vulnapi/auth.py", line 19, in validate_token\n'
                "AssertionError"
            ),
        },
        root_cause={"citations": [], "evidence_refs": []},
        static_report={"prioritized": []},
    )
    target = resolve_patch_target(VULNAPI, state, sig)
    assert target.resolved_application_path == "vulnapi/auth.py"
    assert target.resolution_source == "stack_trace"
    assert target.confidence == 1.0


def test_test_path_resolves_via_root_cause():
    sig = _vulnapi_sig()
    state = _confirmed_state()
    target = resolve_patch_target(VULNAPI, state, sig)
    assert target.resolved_application_path == "vulnapi/auth.py"
    assert target.resolution_source == "root_cause"
    assert target.confidence == 0.9


def test_test_path_resolves_via_import_mapping():
    sig = _vulnapi_sig()
    state = _confirmed_state(
        root_cause={
            "summary": "token validation failure",
            "root_cause": "assertion failed in test",
            "citations": [],
            "evidence_refs": [],
        }
    )
    target = resolve_patch_target(VULNAPI, state, sig)
    assert target.resolved_application_path == "vulnapi/auth.py"
    assert target.resolution_source == "import_mapping"
    assert target.confidence == 0.85


def test_no_runtime_evidence_uses_citations():
    sig = _vulnapi_sig()
    state = RunStateModel(
        run_id="r1",
        repo_path=str(VULNAPI),
        reproduction={"status": "UNCONFIRMED"},
        root_cause={
            "citations": [
                {"file": "vulnapi/auth.py", "line": 19, "claim": "issue", "verified": True}
            ]
        },
    )
    target = resolve_patch_target(VULNAPI, state, sig)
    assert target.resolved_application_path == "vulnapi/auth.py"
    assert target.resolution_source == "root_cause"


def test_unresolved_path_static_fallback():
    sig = _vulnapi_sig()
    state = RunStateModel(
        run_id="r2",
        repo_path=str(VULNAPI),
        reproduction={"status": "CONFIRMED", "failing_file": "tests/unknown.py"},
        root_cause={"citations": [], "evidence_refs": [], "summary": "", "root_cause": ""},
        static_report={
            "prioritized": [
                {"id": "f1", "file": "vulnapi/api.py", "line": 1, "message": "issue"},
            ]
        },
    )
    target = resolve_patch_target(VULNAPI, state, sig)
    assert target.resolved_application_path == "vulnapi/api.py"
    assert target.resolution_source == "fallback"
    assert target.confidence == 0.5


def test_runtime_confirmed_chooses_application_file():
    sig = _vulnapi_sig()
    state = _confirmed_state(
        reproduction={
            "status": "CONFIRMED",
            "failing_file": str(VULNAPI / "tests" / "test_auth.py"),
            "traceback": 'File "tests/test_auth.py", line 27, in test_expired_token_rejected',
        }
    )
    target = resolve_patch_target(VULNAPI, state, sig)
    assert target.resolved_application_path == "vulnapi/auth.py"
    assert target.normalized_path == "tests/test_auth.py"


def test_blast_traversal_starts_from_application_file():
    sig = _vulnapi_sig()
    state = _confirmed_state()
    target = resolve_patch_target(VULNAPI, state, sig)
    assert target.resolved_application_path == "vulnapi/auth.py"

    result = traverse_multi_origin(sig, [target.resolved_application_path])
    pin_resolved_target(result, target, runtime_confirmed=True)

    assert "vulnapi/auth.py" in result.origins
    assert "vulnapi/auth.py" in result.auto_patch_scope
    assert any(s.path == "vulnapi/auth.py" for s in result.scope)


@pytest_asyncio.fixture
async def redis_store():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    store = RedisStore(client, Settings(stub_mode=True))
    yield store
    await client.aclose()


@pytest.mark.asyncio
async def test_a5_agent_resolves_auth_for_runtime_confirmed(redis_store: RedisStore):
    state = _confirmed_state()

    agent = A5BlastGraphAgent(redis_store, Settings(stub_mode=True))
    mock_emit = AsyncMock()
    agent.emit_status = mock_emit
    result_state = await agent.run(state)

    blast = result_state.blast_graph or {}
    assert blast.get("origins") == ["vulnapi/auth.py"]
    assert "vulnapi/auth.py" in blast.get("auto_patch_scope", [])

    completed = mock_emit.await_args_list[-1]
    payload = completed.kwargs.get("payload") or completed.args[3]
    assert payload["target_resolution"]["resolved_target"] == "vulnapi/auth.py"
    assert payload["target_resolution"]["runtime_confirmed"] is True
