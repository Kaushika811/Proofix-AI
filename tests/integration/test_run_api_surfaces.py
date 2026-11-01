"""Integration tests for the run API surfaces.

Exercises the real FastAPI app against a fake Redis: registry surfaces, the
context endpoint, stage progress, repository identity and lifecycle replay.

The `surface=v1|v2` parameter these tests exercise is an API contract, not a
second frontend — see the note above `SURFACE_V1` in `ui_projection.py`. The
frontend once called Workspace V2 was cancelled and removed; these endpoints
remain because they are backend capability the product uses or will use.

The governing rule these tests enforce is that absent data is reported as
absent. An endpoint that invents a value to avoid a 404 is the failure mode the
whole workspace is built to prevent.
"""

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.agents.a5_5_context_engineering import CONTEXT_STORE_KEY
from backend.agents.repository_intelligence import INTELLIGENCE_STORE_KEY
from backend.config import Settings
from backend.main import create_app
from backend.models.context import ContextMetrics, ContextPackage, RankedContextFile, Redaction
from backend.models.fix_dag import DependencyEdge, FixDAGPlan, FixNode
from backend.models.patch import BehavioralContract, PatchBundle, PatchCandidate
from backend.state.events import AgentStatusEvent, RunLifecycleEvent
from backend.state.redis_store import RedisStore

RUN_ID = "d4c3b2a1-9f8e-7d6c-5b4a-3e2d1c0b9a88"
MISSING_RUN_ID = "00000000-0000-0000-0000-000000000000"


@pytest_asyncio.fixture
async def client():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    app = create_app()
    app.state.redis = redis
    app.state.settings = Settings(stub_mode=True, redis_url="redis://localhost:6379/0")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, RedisStore(redis, app.state.settings)
    await redis.aclose()


async def _seed_run(store: RedisStore, run_id: str = RUN_ID, status: str = "completed"):
    state = await store.init_run(run_id, "/tmp/clones/vulnapi")
    state.status = status
    state.base_commit_sha = "abc123"
    await store.save_state(state)
    return state


def _package() -> ContextPackage:
    return ContextPackage(
        target_file="vulnapi/auth.py",
        target_function="validate_token",
        root_cause_summary="expiry never compared",
        acceptance_criteria=["reject tokens whose exp is in the past"],
        ranked_files=[
            RankedContextFile(
                file="vulnapi/auth.py",
                score=1.8,
                reason="resolved target",
                confidence=0.9,
                signals={"stack_frame": 1.0, "verified_citation": 0.8},
                is_target=True,
            )
        ],
        redactions=[Redaction(file="vulnapi/config.py", line=3, detector="name_pattern")],
        privacy_guard_status="masked",
        metrics=ContextMetrics(
            files_ranked=42,
            context_files=2,
            context_functions=3,
            original_tokens=18000,
            reduced_tokens=2400,
            token_reduction=0.867,
            privacy_redactions=1,
        ),
    )


# -- G1: registry surfaces -------------------------------------------------


async def test_agents_endpoint_defaults_to_the_v1_surface(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/agents")
    assert response.status_code == 200

    agent_ids = [entry["agentId"] for entry in response.json()]
    # The default surface publishes the whole pipeline. A0.5 gained a card in
    # Phase 2 and A5.5 in Phase 4, which closed the split.
    assert "A0.5" in agent_ids
    assert "A5.5" in agent_ids
    assert "A1" in agent_ids


async def test_the_surface_parameter_no_longer_changes_the_response(client):
    """Still accepted, still validated, no longer selective.

    Removing it would break a client that sends it; leaving it *doing*
    something it no longer does would be worse.
    """
    http, store = client
    await _seed_run(store)

    default = await http.get(f"/api/runs/{RUN_ID}/agents")
    v1 = await http.get(f"/api/runs/{RUN_ID}/agents", params={"surface": "v1"})
    response = await http.get(f"/api/runs/{RUN_ID}/agents", params={"surface": "v2"})
    assert response.status_code == 200
    assert default.json() == v1.json() == response.json()

    entries = response.json()
    agent_ids = [e["agentId"] for e in entries]
    assert "A0.5" in agent_ids
    assert "A5.5" in agent_ids
    for entry in entries:
        assert entry["stage"]
        assert entry["stageLabel"]
        assert isinstance(entry["stageOrder"], int)


async def test_unknown_surface_is_rejected(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/agents", params={"surface": "v3"})
    assert response.status_code == 422


async def test_stages_endpoint_publishes_the_workflow(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/stages")
    assert response.status_code == 200

    stages = response.json()["stages"]
    assert [s["id"] for s in stages] == [
        "repository", "investigation", "context", "planning", "patch", "validation", "learning",
    ]
    context_stage = next(s for s in stages if s["id"] == "context")
    assert [a["agentId"] for a in context_stage["agents"]] == ["A5.5"]
    assert context_stage["label"] == "Context Engineering"


async def test_stage_status_reflects_agent_events(client):
    http, store = client
    # A live run: `running` is only a truthful stage status while the run is
    # still in flight (R4).
    await _seed_run(store, status="running")
    await store.append_event(
        AgentStatusEvent(run_id=RUN_ID, agent_id="A1", status="completed", message="SIG built")
    )
    await store.append_event(
        AgentStatusEvent(run_id=RUN_ID, agent_id="A4", status="started", message="investigating")
    )

    stages = {s["id"]: s for s in (await http.get(f"/api/runs/{RUN_ID}/stages")).json()["stages"]}

    assert stages["investigation"]["status"] == "running"
    a1 = next(a for a in stages["repository"]["agents"] if a["agentId"] == "A1")
    assert a1["status"] == "completed"
    assert a1["message"] == "SIG built"


# -- A1: semantic graph endpoint --------------------------------------------


async def _seed_sig(store: RedisStore, files: dict) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.sig = {
        "repo_path": "/tmp/clones/vulnapi",
        "source_roots": ["vulnapi"],
        "files": files,
        "edges": [["vulnapi/routes.py", "vulnapi/auth.py"]],
        "generated_at": "2026-08-13T00:00:00",
    }
    await store.save_state(state)


async def test_semantic_graph_endpoint_returns_the_projected_sig(client):
    http, store = client
    await _seed_run(store)
    await _seed_sig(
        store,
        {
            "vulnapi/auth.py": {
                "role": "auth-boundary",
                "imports": [],
                "imported_by": ["vulnapi/routes.py"],
                "churn_weight": 0.3,
                "criticality": 0.9,
            },
            "vulnapi/routes.py": {
                "role": "public-api",
                "imports": ["vulnapi/auth.py"],
                "imported_by": [],
                "churn_weight": 0.5,
                "criticality": 0.7,
            },
        },
    )

    response = await http.get(f"/api/runs/{RUN_ID}/semantic-graph")
    assert response.status_code == 200

    body = response.json()
    assert body["totalFiles"] == 2
    assert body["roleCounts"]["auth-boundary"] == 1
    assert body["roleCounts"]["public-api"] == 1
    assert body["roleCounts"]["data-access"] == 0
    # Highest criticality first.
    assert body["files"][0]["path"] == "vulnapi/auth.py"
    assert body["files"][0]["importedBy"] == ["vulnapi/routes.py"]
    assert body["edges"] == [["vulnapi/routes.py", "vulnapi/auth.py"]]
    assert body["sourceRoots"] == ["vulnapi"]


async def test_semantic_graph_endpoint_404s_before_a1_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/semantic-graph")
    assert response.status_code == 404
    assert "semantic graph" in response.json()["detail"].lower()


async def test_semantic_graph_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/semantic-graph")
    assert response.status_code == 404


# -- A2: dependency risk endpoint --------------------------------------------


async def _seed_cve(store: RedisStore, **fields) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.cve_report = {
        "findings": [],
        "critical_queue": [],
        "total_dependencies": 0,
        "manifest": None,
        "ecosystem": None,
        **fields,
    }
    await store.save_state(state)


async def test_dependency_risk_endpoint_returns_the_projected_report(client):
    http, store = client
    await _seed_run(store)
    await _seed_cve(
        store,
        findings=[
            {
                "package": "urllib3",
                "cve_id": "CVE-2023-45803",
                "severity": "7.5",
                "installed_version": "1.26.5",
                "reachable": True,
                "reach_path": ["vulnapi/net.py"],
                "classification": "Critical",
            }
        ],
        critical_queue=["CVE-2023-45803"],
        total_dependencies=2,
        manifest="requirements.txt",
        ecosystem="PyPI",
    )

    response = await http.get(f"/api/runs/{RUN_ID}/dependency-risk")
    assert response.status_code == 200

    body = response.json()
    assert body["totalDependencies"] == 2
    assert body["advisoryCount"] == 1
    assert body["reachableCount"] == 1
    assert body["findings"][0]["package"] == "urllib3"
    assert body["findings"][0]["reachPath"] == ["vulnapi/net.py"]
    assert body["manifest"] == "requirements.txt"


async def test_dependency_risk_endpoint_200s_with_zero_advisories_when_a2_ran_clean(client):
    http, store = client
    await _seed_run(store)
    await _seed_cve(store, total_dependencies=5, manifest="requirements.txt", ecosystem="PyPI")

    response = await http.get(f"/api/runs/{RUN_ID}/dependency-risk")
    assert response.status_code == 200
    body = response.json()
    assert body["advisoryCount"] == 0
    assert body["totalDependencies"] == 5
    assert body["findings"] == []


async def test_dependency_risk_endpoint_404s_before_a2_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/dependency-risk")
    assert response.status_code == 404
    assert "dependency analysis" in response.json()["detail"].lower()


async def test_dependency_risk_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/dependency-risk")
    assert response.status_code == 404


# -- A3: static findings endpoint --------------------------------------------


async def _seed_static(store: RedisStore, **fields) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.static_report = {
        "raw_count": 0,
        "prioritized": [],
        "baseline_json": {},
        "scanner_status": {},
        **fields,
    }
    await store.save_state(state)


async def test_static_findings_endpoint_returns_the_projected_report(client):
    http, store = client
    await _seed_run(store)
    await _seed_static(
        store,
        raw_count=3,
        prioritized=[
            {
                "id": "finding-0",
                "file": "vulnapi/auth.py",
                "line": 12,
                "message": "pickle usage",
                "tools": ["bandit"],
                "severity": 0.9,
                "blast_radius_score": 0.62,
                "consensus": False,
                "criticality": 0.81,
                "churn_weight": 0.37,
                "severity_measured": True,
            }
        ],
        scanner_status={"bandit": "ok", "semgrep": "unavailable", "ruff": "ok_no_findings"},
    )

    response = await http.get(f"/api/runs/{RUN_ID}/static-findings")
    assert response.status_code == 200

    body = response.json()
    assert body["rawCount"] == 3
    assert body["prioritizedCount"] == 1
    assert body["scannerStatus"]["semgrep"] == "unavailable"
    assert body["findings"][0]["rank"] == 1
    assert body["findings"][0]["severityMeasured"] is True
    assert body["findings"][0]["blastRadiusScore"] == 0.62


async def test_static_findings_endpoint_200s_with_zero_findings_when_a3_ran_clean(client):
    http, store = client
    await _seed_run(store)
    await _seed_static(
        store,
        raw_count=0,
        scanner_status={"bandit": "ok_no_findings", "semgrep": "ok_no_findings", "ruff": "ok_no_findings"},
    )

    response = await http.get(f"/api/runs/{RUN_ID}/static-findings")
    assert response.status_code == 200
    body = response.json()
    assert body["findings"] == []
    assert body["scannerStatus"]["bandit"] == "ok_no_findings"


async def test_static_findings_endpoint_404s_before_a3_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/static-findings")
    assert response.status_code == 404
    assert "static analysis" in response.json()["detail"].lower()


async def test_static_findings_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/static-findings")
    assert response.status_code == 404


# -- A9: security re-scan endpoint -------------------------------------------


async def _seed_security(store: RedisStore, **fields) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.security_result = {
        "new_findings": [],
        "rejected": False,
        "security_score": None,
        "scanners_run": [],
        "reexecution_command": "",
        "reexecution_timeout_seconds": 150,
        **fields,
    }
    await store.save_state(state)


async def test_security_endpoint_returns_the_projected_report(client):
    http, store = client
    await _seed_run(store)
    await _seed_security(
        store,
        rejected=True,
        security_score=75.0,
        scanners_run=["bandit"],
        new_findings=[
            {
                "id": "new-0",
                "file": "vulnapi/auth.py",
                "line": 42,
                "message": "Possible hardcoded password: 'hunter2'",
                "tools": ["bandit"],
                "severity": 0.7,
            }
        ],
    )

    response = await http.get(f"/api/runs/{RUN_ID}/security")
    assert response.status_code == 200

    body = response.json()
    assert body["verdict"] == "new_findings"
    assert body["rejected"] is True
    assert body["securityScore"] == 75.0
    assert body["scannersRun"] == ["bandit"]
    assert body["newFindings"][0]["file"] == "vulnapi/auth.py"
    assert "severity" not in body["newFindings"][0]


async def test_security_endpoint_200s_with_zero_findings_when_a9_ran_clean(client):
    http, store = client
    await _seed_run(store)
    await _seed_security(store, security_score=100.0, scanners_run=["bandit", "semgrep"])

    response = await http.get(f"/api/runs/{RUN_ID}/security")
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "clean"
    assert body["newFindings"] == []
    assert body["scannersRun"] == ["bandit", "semgrep"]


async def test_security_endpoint_reports_not_measured_when_no_scanner_ran(client):
    http, store = client
    await _seed_run(store)
    await _seed_security(store)

    response = await http.get(f"/api/runs/{RUN_ID}/security")
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "not_measured"
    assert body["securityScore"] is None
    assert body["scannersRun"] == []


async def test_security_endpoint_404s_before_a9_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/security")
    assert response.status_code == 404
    assert "security" in response.json()["detail"].lower()


async def test_security_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/security")
    assert response.status_code == 404


# -- A10: mergeability decision endpoint -------------------------------------


async def _seed_decision(store: RedisStore, **fields) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.reproduction = {"status": "CONFIRMED"}
    state.reproduction_confidence = "exact_test"
    state.mutation_result = {
        "pytest_passed": True,
        "target_test_passed": True,
        "regression_tests_passed": True,
        "patch_retry_required": False,
        "correctness_score": 92.0,
    }
    state.security_result = {"rejected": False, "security_score": 100.0}
    state.pr_decision = {
        "pr_type": "auto_mergeable",
        "axis_scores": {
            "correctness": 92.0,
            "security": 100.0,
            "fidelity": 100.0,
            "scope_risk": 90.0,
        },
        "pr_url": None,
        "description_why": "",
        "description_what": "",
        "review_note": None,
        "phantom_changes_detected": False,
        **fields,
    }
    await store.save_state(state)


async def test_decision_endpoint_returns_the_projected_report(client):
    http, store = client
    await _seed_run(store)
    await _seed_decision(store)

    response = await http.get(f"/api/runs/{RUN_ID}/decision")
    assert response.status_code == 200

    body = response.json()
    assert body["prType"] == "auto_mergeable"
    assert len(body["hardGates"]) == 10
    assert all(g["checked"] and g["passed"] for g in body["hardGates"])
    assert body["routingModifiers"]["hardGatesClear"] is True


async def test_decision_endpoint_reports_unmeasured_axis_without_zero(client):
    http, store = client
    await _seed_run(store)
    await _seed_decision(
        store,
        pr_type="draft",
        axis_scores={
            "correctness": 92.0,
            "security": None,
            "fidelity": 100.0,
            "scope_risk": 90.0,
        },
        review_note="Not measured: security. Manual verification required before merge.",
    )

    response = await http.get(f"/api/runs/{RUN_ID}/decision")
    assert response.status_code == 200
    body = response.json()
    security = next(a for a in body["axes"] if a["name"] == "security")
    assert security["value"] is None
    assert security["meetsLowThreshold"] is None
    firing = [g for g in body["hardGates"] if g["checked"] and g["passed"] is False]
    assert firing[0]["code"] == "axes_measured"


async def test_decision_endpoint_404s_before_a10_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/decision")
    assert response.status_code == 404
    assert "mergeability" in response.json()["detail"].lower()


async def test_decision_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/decision")
    assert response.status_code == 404


# -- A3.5: reproduction endpoint ---------------------------------------------


async def _seed_reproduction(store: RedisStore, **fields) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.reproduction = {
        "status": "CONFIRMED",
        "reproduced": True,
        "failing_test": "tests/test_auth.py::test_expired_token_rejected",
        "exception_type": "AssertionError",
        "exception_message": "assert True is False",
        "failing_file": "vulnapi/tests/test_auth.py",
        "failing_line": 27,
        "traceback": "E   AssertionError: assert True is False",
        "confidence": 0.9,
        "force_draft_pr": False,
        "command": "python -m pytest --tb=long --json-report -v",
        "exit_code": 1,
        "timed_out": False,
        "stdout": "collected 12 items",
        "stderr": "",
        "duration_seconds": 0.03,
        "started_at": "2026-08-13T17:30:37",
        "finished_at": "2026-08-13T17:30:38",
        "tests_collected": 12,
        "tests_passed": 8,
        "tests_failed": 4,
        "evidence_source": "pytest_report",
        "reexecution_command": "python -m pytest tests/test_auth.py::test_expired_token_rejected -v --tb=long",
        "reexecution_is_targeted": True,
        "reexecution_timeout_seconds": 120,
        "pre_existing_failures": ["tests/test_auth.py::test_expired_token_rejected"],
        **fields,
    }
    await store.save_state(state)


async def test_reproduction_endpoint_returns_the_projected_evidence(client):
    http, store = client
    await _seed_run(store)
    await _seed_reproduction(store)

    response = await http.get(f"/api/runs/{RUN_ID}/reproduction")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "CONFIRMED"
    assert body["uiStatus"] == "reproduced"
    assert body["failingTest"] == "tests/test_auth.py::test_expired_token_rejected"
    assert body["failingFile"] == "vulnapi/tests/test_auth.py"
    assert body["command"] == "python -m pytest --tb=long --json-report -v"
    assert body["testsCollected"] == 12
    assert len(body["stages"]) == 5
    assert body["stages"][0]["id"] == "suite_executed"


async def test_reproduction_endpoint_reports_unconfirmed_distinctly(client):
    http, store = client
    await _seed_run(store)
    await _seed_reproduction(
        store,
        status="UNCONFIRMED",
        reproduced=False,
        force_draft_pr=True,
        failing_test=None,
        exception_type=None,
        exception_message=None,
        failing_file=None,
        failing_line=None,
        traceback=None,
        confidence=0.0,
        evidence_source=None,
        tests_failed=0,
        tests_passed=12,
    )

    response = await http.get(f"/api/runs/{RUN_ID}/reproduction")
    assert response.status_code == 200
    body = response.json()
    assert body["uiStatus"] == "not_reproduced"
    assert body["failingTest"] is None


async def test_reproduction_endpoint_404s_before_a3_5_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/reproduction")
    assert response.status_code == 404
    assert "reproduction result" in response.json()["detail"].lower()


async def test_reproduction_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/reproduction")
    assert response.status_code == 404


# -- A4: investigation endpoint ----------------------------------------------


async def _seed_investigation(store: RedisStore, **fields) -> None:
    """Seed A4's real artifacts: the brief it publishes and the audit of it.

    Built by running the real builder over the real upstream shapes rather than
    hand-writing a response body, so a change to either side fails here.
    """
    from backend.models.root_cause import Citation, EvidenceReference, RootCauseBrief
    from backend.services.evidence_investigation import build_investigation_report

    state = await store.load_state(RUN_ID)
    assert state is not None
    state.static_report = {
        "raw_count": 2,
        "scanner_status": {"bandit": "ok", "semgrep": "ok_no_findings", "ruff": "unavailable"},
        "prioritized": [
            {
                "id": "finding-0",
                "file": "vulnapi/auth.py",
                "line": 27,
                "message": "hardcoded secret",
                "tools": ["bandit"],
                "severity": 0.9,
                "severity_measured": True,
            }
        ],
    }
    brief = RootCauseBrief(
        summary="Expired tokens are accepted",
        root_cause="validate_token never compares exp against the clock",
        citations=[
            Citation(file="vulnapi/auth.py", line=27, claim="no exp check", verified=True),
            Citation(file="vulnapi/ghost.py", line=4, claim="phantom", verified=False),
        ],
        evidence_refs=[
            EvidenceReference(
                source="runtime", ref_id="t", claim="AssertionError", weight=0.35
            )
        ],
        confidence=0.6,
    )
    state.root_cause = brief.model_dump(mode="json")
    report = build_investigation_report(
        brief=brief,
        static_report=state.static_report,
        reproduction=state.reproduction,
        cve_report=None,
        confidence_components=[("runtime evidence", 0.35, "1 runtime reference(s)")],
        root_cause_source="deterministic",
        **fields,
    )
    state.investigation = report.model_dump(mode="json")
    await store.save_state(state)


async def test_investigation_endpoint_returns_the_projected_report(client):
    http, store = client
    await _seed_run(store)
    # The failure lands in the same file bandit flagged, which is what makes
    # the two independent sources corroborating rather than unrelated.
    await _seed_reproduction(store, failing_file="vulnapi/auth.py")
    await _seed_investigation(store)

    response = await http.get(f"/api/runs/{RUN_ID}/investigation")
    assert response.status_code == 200

    body = response.json()
    assert body["subjectKind"] == "runtime_failure"
    assert body["reproductionStatus"] == "reproduced"
    assert body["rootCause"] == "validate_token never compares exp against the clock"
    assert body["rootCauseSource"] == "deterministic"
    assert body["confidence"] == 0.6
    assert body["confidenceBreakdown"][0]["component"] == "runtime evidence"

    stances = {e["id"]: e["stance"] for e in body["evidence"]}
    assert stances["reproduction"] == "supporting"
    assert stances["scanner:bandit"] == "supporting"
    # Ran clean and could not run are both non-arguments, and neither is
    # allowed to read as evidence against the finding.
    assert stances["scanner:semgrep"] == "neutral"
    assert stances["scanner:ruff"] == "neutral"
    assert stances["citation:1"] == "contradicting"

    assert body["completeness"]["categoryStatus"]["dependency"] == "unavailable"
    assert any(u["source"] == "ruff" for u in body["unavailableSources"])


async def test_investigation_endpoint_reports_unmeasured_values_as_null(client):
    """A runtime failure has no severity — no tool assigned one."""
    http, store = client
    await _seed_run(store)
    await _seed_reproduction(store, failing_file="vulnapi/auth.py")
    await _seed_investigation(store)

    body = (await http.get(f"/api/runs/{RUN_ID}/investigation")).json()
    assert body["severity"] is None
    assert body["severityMeasured"] is False
    strengths = {e["id"]: e["strength"] for e in body["evidence"]}
    assert strengths["scanner:semgrep"] is None
    assert strengths["scanner:ruff"] is None


async def test_investigation_endpoint_surfaces_a_degraded_investigation(client):
    """A4 falling back to the deterministic path is reported, not hidden."""
    http, store = client
    await _seed_run(store)
    await _seed_reproduction(store)
    await _seed_investigation(store, errors=["LLM investigation unavailable (TimeoutError)"])

    body = (await http.get(f"/api/runs/{RUN_ID}/investigation")).json()
    assert body["status"] == "error"
    assert body["errors"] == ["LLM investigation unavailable (TimeoutError)"]
    assert body["evidence"]  # degraded, but the evidence it did gather survives


async def test_investigation_endpoint_404s_before_a4_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/investigation")
    assert response.status_code == 404
    assert "investigation" in response.json()["detail"].lower()


async def test_investigation_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/investigation")
    assert response.status_code == 404


# -- A5: blast-radius impact endpoint ----------------------------------------


async def _seed_blast(store: RedisStore, **fields) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.sig = {
        "repo_path": "/tmp/clones/vulnapi",
        "source_roots": ["vulnapi"],
        "files": {
            "vulnapi/auth.py": {
                "role": "auth-boundary", "imports": [], "imported_by": ["vulnapi/middleware.py"],
                "churn_weight": 0.0, "criticality": 0.9,
            },
            "vulnapi/middleware.py": {
                "role": "internal-util", "imports": ["auth"], "imported_by": [],
                "churn_weight": 0.0, "criticality": 0.5,
            },
        },
        "edges": [["vulnapi/middleware.py", "auth"]],
        "generated_at": "2026-08-13T00:00:00",
    }
    state.static_report = {
        "raw_count": 1,
        "scanner_status": {"bandit": "ok"},
        "prioritized": [{"id": "f0", "file": "vulnapi/middleware.py", "line": 4, "message": "x", "tools": ["bandit"], "severity": 0.6, "severity_measured": True}],
    }
    state.blast_graph = {
        "scope": [
            {
                "path": "vulnapi/auth.py", "direction": "forward", "directions": ["forward"],
                "propagation_confidence": 1.0, "risk_score": 0.0, "hop_count": 0,
                "origin": "vulnapi/auth.py", "reached_via": None, "edge_basis": None,
            },
            {
                "path": "vulnapi/middleware.py", "direction": "backward", "directions": ["backward"],
                "propagation_confidence": 0.42, "risk_score": 0.0, "hop_count": 1,
                "origin": "vulnapi/auth.py", "reached_via": "vulnapi/auth.py",
                "edge_basis": "resolved_suffix",
            },
        ],
        "human_review_required": ["vulnapi/middleware.py"],
        "auto_patch_scope": ["vulnapi/auth.py"],
        "origins": ["vulnapi/auth.py"],
        "edges": [
            {
                "from_path": "vulnapi/auth.py", "to_path": "vulnapi/middleware.py",
                "direction": "backward", "basis": "resolved_suffix", "hop_count": 1,
            }
        ],
        "target_resolution": {
            "original_path": "/tmp/clones/vulnapi/vulnapi/auth.py",
            "normalized_path": "vulnapi/auth.py",
            "resolved_path": "vulnapi/auth.py",
            "source": "stack_trace",
            "confidence": 0.9,
            "runtime_confirmed": True,
            "pinned": False,
        },
        **fields,
    }
    await store.save_state(state)


async def test_blast_endpoint_returns_the_projected_impact(client):
    http, store = client
    await _seed_run(store)
    await _seed_blast(store)

    response = await http.get(f"/api/runs/{RUN_ID}/blast")
    assert response.status_code == 200

    body = response.json()
    assert body["origin"]["resolvedPath"] == "vulnapi/auth.py"
    assert body["origin"]["source"] == "stack_trace"
    assert body["maxHop"] == 1

    by_path = {s["path"]: s for s in body["scope"]}
    assert by_path["vulnapi/middleware.py"]["role"] == "internal-util"
    assert by_path["vulnapi/middleware.py"]["hasStaticFinding"] is True
    assert by_path["vulnapi/auth.py"]["hasStaticFinding"] is False
    assert by_path["vulnapi/middleware.py"]["directions"] == ["backward"]

    assert body["edges"] == [
        {
            "from": "vulnapi/auth.py",
            "to": "vulnapi/middleware.py",
            "direction": "backward",
            "basis": "resolved_suffix",
            "hopCount": 1,
        }
    ]
    # A0.5 never ran in this fixture — capabilities must say so, not silently
    # report zero.
    assert body["capabilities"] is None


async def test_blast_endpoint_reports_patch_authority_overlap(client):
    http, store = client
    await _seed_run(store)
    await _seed_blast(
        store,
        auto_patch_scope=["vulnapi/auth.py", "vulnapi/middleware.py"],
        human_review_required=["vulnapi/middleware.py"],
    )

    body = (await http.get(f"/api/runs/{RUN_ID}/blast")).json()
    assert body["patchAuthorityOverlap"] == ["vulnapi/middleware.py"]


async def test_blast_endpoint_never_calls_risk_a_certainty(client):
    http, store = client
    await _seed_run(store)
    await _seed_blast(store)

    body = (await http.get(f"/api/runs/{RUN_ID}/blast")).json()
    assert "priorityScore" in body["scope"][0]
    assert "riskScore" not in body["scope"][0]
    assert body["riskMeasurementCaveat"]


async def test_blast_endpoint_returns_empty_scope_as_a_real_answer(client):
    http, store = client
    await _seed_run(store)
    state = await store.load_state(RUN_ID)
    state.blast_graph = {"scope": [], "origins": []}
    await store.save_state(state)

    response = await http.get(f"/api/runs/{RUN_ID}/blast")
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == []
    assert body["origin"] is None


async def test_blast_endpoint_404s_before_a5_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/blast")
    assert response.status_code == 404
    assert "blast" in response.json()["detail"].lower()


async def test_blast_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/blast")
    assert response.status_code == 404


# -- A6: repair-plan endpoint -------------------------------------------------


async def _seed_repair_plan(store: RedisStore, **fields) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.static_report = {
        "raw_count": 1,
        "scanner_status": {"bandit": "ok"},
        "prioritized": [
            {
                "id": "finding-0", "file": "vulnapi/auth.py", "message": "hardcoded secret",
                "tools": ["bandit"], "severity": 0.9, "severity_measured": True,
            }
        ],
    }
    state.cve_report = {
        "manifest": "requirements.txt", "total_dependencies": 2,
        "findings": [
            {
                "cve_id": "CVE-1", "package": "pyjwt", "severity": "HIGH",
                "installed_version": "1.0.0", "classification": "Critical",
                "reach_path": ["vulnapi/auth.py"],
            }
        ],
    }
    state.fix_dag = {
        "nodes": [
            {"issue_id": "cve-CVE-1", "files": ["vulnapi/auth.py"], "depends_on": []},
            {"issue_id": "finding-0", "files": ["vulnapi/auth.py"], "depends_on": ["cve-CVE-1"]},
        ],
        "execution_order": ["cve-CVE-1", "finding-0"],
        "conflict_batches": [],
        "dependency_edges": [
            {
                "from_issue": "cve-CVE-1", "to_issue": "finding-0",
                "reason": "cve_reachability:pyjwt->vulnapi/auth.py",
            }
        ],
        "ordering_source": "deterministic",
        "ordering_rationale": "",
        **fields,
    }
    await store.save_state(state)


async def test_repair_plan_endpoint_returns_the_projected_plan(client):
    http, store = client
    await _seed_run(store)
    await _seed_repair_plan(store)

    response = await http.get(f"/api/runs/{RUN_ID}/repair-plan")
    assert response.status_code == 200

    body = response.json()
    assert [s["issueId"] for s in body["steps"]] == ["cve-CVE-1", "finding-0"]

    cve_step = body["steps"][0]
    assert cve_step["why"] == {
        "kind": "cve", "package": "pyjwt", "severity": "HIGH",
        "installedVersion": "1.0.0", "reachPath": ["vulnapi/auth.py"],
    }
    # The step named by `execution_order[0]` — the one value A7 actually reads.
    assert cve_step["isHandoffTarget"] is True

    finding_step = body["steps"][1]
    assert finding_step["why"]["kind"] == "static_finding"
    assert finding_step["isHandoffTarget"] is False
    assert finding_step["incomingEdges"] == [
        {"fromIssue": "cve-CVE-1", "reason": "cve_reachability:pyjwt->vulnapi/auth.py"}
    ]

    assert body["orderingSource"] == "deterministic"
    assert body["executionAuthority"]["field"] == "execution_order[0]"
    # A5.5 never ran in this fixture — carried-forward evidence must say so.
    assert body["carriedForward"] is None


async def test_repair_plan_endpoint_carries_forward_a5_5_evidence(client):
    http, store = client
    await _seed_run(store)
    await _seed_repair_plan(store)
    await store.set_json(RUN_ID, CONTEXT_STORE_KEY, _package().to_storage_dict())

    body = (await http.get(f"/api/runs/{RUN_ID}/repair-plan")).json()

    assert body["carriedForward"]["acceptanceCriteria"] == _package().acceptance_criteria
    assert body["carriedForward"]["patchConstraints"] == _package().patch_constraints
    # A5.5's own field, attached as-is — A6 has no function-level analysis.
    assert body["carriedForward"]["targetFunction"] == "validate_token"


async def test_repair_plan_endpoint_omits_target_function_when_a5_5_never_ran(client):
    http, store = client
    await _seed_run(store)
    await _seed_repair_plan(store)

    body = (await http.get(f"/api/runs/{RUN_ID}/repair-plan")).json()
    assert body["carriedForward"] is None


async def test_repair_plan_endpoint_reports_no_target_function_honestly(client):
    """A5.5 ran but resolved no target function for this file."""
    http, store = client
    await _seed_run(store)
    await _seed_repair_plan(store)
    package = _package()
    package.target_function = None
    await store.set_json(RUN_ID, CONTEXT_STORE_KEY, package.to_storage_dict())

    body = (await http.get(f"/api/runs/{RUN_ID}/repair-plan")).json()
    assert body["carriedForward"]["targetFunction"] is None


async def test_repair_plan_endpoint_never_fabricates_a_confidence(client):
    http, store = client
    await _seed_run(store)
    await _seed_repair_plan(store)

    body = (await http.get(f"/api/runs/{RUN_ID}/repair-plan")).json()
    assert "confidence" not in body
    assert all("confidence" not in s for s in body["steps"])


async def test_repair_plan_endpoint_returns_empty_steps_as_a_real_answer(client):
    http, store = client
    await _seed_run(store)
    state = await store.load_state(RUN_ID)
    state.fix_dag = {"nodes": [], "execution_order": []}
    await store.save_state(state)

    response = await http.get(f"/api/runs/{RUN_ID}/repair-plan")
    assert response.status_code == 200
    assert response.json()["steps"] == []


async def test_repair_plan_endpoint_404s_before_a6_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/repair-plan")
    assert response.status_code == 404
    assert "repair plan" in response.json()["detail"].lower()


async def test_repair_plan_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/repair-plan")
    assert response.status_code == 404


async def test_plan_route_still_returns_the_raw_dict_unchanged(client):
    """`/plan` is a separate, already-documented contract — verbatim `fix_dag`."""
    http, store = client
    await _seed_run(store)
    await _seed_repair_plan(store)

    body = (await http.get(f"/api/runs/{RUN_ID}/plan")).json()
    assert body["ordering_source"] == "deterministic"
    assert "steps" not in body


# -- G2: context endpoint --------------------------------------------------


async def test_context_endpoint_returns_the_stored_package(client):
    http, store = client
    await _seed_run(store)
    await store.set_json(RUN_ID, CONTEXT_STORE_KEY, _package().to_storage_dict())

    response = await http.get(f"/api/runs/{RUN_ID}/context")
    assert response.status_code == 200

    body = response.json()
    assert body["target_file"] == "vulnapi/auth.py"
    assert body["target_function"] == "validate_token"
    assert body["metrics"]["original_tokens"] == 18000
    assert body["metrics"]["reduced_tokens"] == 2400
    assert body["privacy_guard_status"] == "masked"
    # Ranking explainability is what the flagship stage is built on.
    assert body["ranked_files"][0]["signals"]["stack_frame"] == 1.0
    assert body["redactions"][0]["detector"] == "name_pattern"


async def test_context_endpoint_404s_when_no_package_exists(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/context")
    assert response.status_code == 404
    assert "context package" in response.json()["detail"].lower()


async def test_context_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/context")
    assert response.status_code == 404


async def test_context_endpoint_does_not_recompute(client):
    """The endpoint reads; it never rebuilds. A stored package with impossible
    numbers must come back verbatim, proving nothing regenerated it."""
    http, store = client
    await _seed_run(store)
    package = _package()
    package.metrics.files_ranked = 999
    await store.set_json(RUN_ID, CONTEXT_STORE_KEY, package.to_storage_dict())

    body = (await http.get(f"/api/runs/{RUN_ID}/context")).json()
    assert body["metrics"]["files_ranked"] == 999


# -- Phase 5: repair plan endpoint -----------------------------------------


def _plan() -> FixDAGPlan:
    return FixDAGPlan(
        nodes=[
            FixNode(issue_id="cve-pyjwt", files=["requirements.txt"]),
            FixNode(issue_id="fix-auth", files=["vulnapi/auth.py"], depends_on=["cve-pyjwt"]),
            FixNode(issue_id="fix-routes", files=["vulnapi/auth.py", "vulnapi/routes.py"]),
        ],
        execution_order=["cve-pyjwt", "fix-auth", "fix-routes"],
        conflict_batches=[["fix-auth", "fix-routes"]],
        dependency_edges=[
            DependencyEdge(
                from_issue="cve-pyjwt",
                to_issue="fix-auth",
                reason="dependency upgrade precedes the code that imports it",
            )
        ],
    )


async def _seed_plan(store: RedisStore, plan: FixDAGPlan) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.fix_dag = plan.model_dump(mode="json")
    await store.save_state(state)


async def test_plan_endpoint_returns_the_stored_dag(client):
    http, store = client
    await _seed_run(store)
    await _seed_plan(store, _plan())

    response = await http.get(f"/api/runs/{RUN_ID}/plan")
    assert response.status_code == 200

    body = response.json()
    assert body["execution_order"] == ["cve-pyjwt", "fix-auth", "fix-routes"]
    assert body["conflict_batches"] == [["fix-auth", "fix-routes"]]
    # The per-edge reason and per-node files are the fields the agent
    # projection never publishes; the stage is unbuildable without them.
    assert body["dependency_edges"][0]["reason"].startswith("dependency upgrade")
    assert body["nodes"][1]["depends_on"] == ["cve-pyjwt"]
    assert body["nodes"][2]["files"] == ["vulnapi/auth.py", "vulnapi/routes.py"]


async def test_plan_endpoint_404s_before_a6_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/plan")
    assert response.status_code == 404
    assert "repair plan" in response.json()["detail"].lower()


async def test_plan_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/plan")
    assert response.status_code == 404


async def test_plan_endpoint_does_not_recompute(client):
    """A plan whose order contradicts its edges comes back unchanged — proof
    the route reads A6's output rather than re-deriving one."""
    http, store = client
    await _seed_run(store)
    plan = _plan()
    plan.execution_order = ["fix-auth", "cve-pyjwt", "fix-routes"]
    await _seed_plan(store, plan)

    body = (await http.get(f"/api/runs/{RUN_ID}/plan")).json()
    assert body["execution_order"] == ["fix-auth", "cve-pyjwt", "fix-routes"]


# -- Phase 6: patch bundle endpoint ----------------------------------------


def _bundle() -> PatchBundle:
    return PatchBundle(
        issue_id="fix-auth",
        patches=[
            PatchCandidate(
                file="vulnapi/auth.py",
                original="def validate_token(t):\n    return decode(t)\n",
                patched="def validate_token(t):\n    claims = decode(t)\n    check_exp(claims)\n    return claims\n",
                method="ast_validated_write",
            )
        ],
        contracts=[
            BehavioralContract(
                assertion="a token whose exp is in the past is rejected",
                location="vulnapi/auth.py::validate_token",
            )
        ],
        style_exemplar_commit="deadbeef",
        diff_text="--- a/vulnapi/auth.py\n+++ b/vulnapi/auth.py\n-    return decode(t)\n+    claims = decode(t)\n",
    )


async def _seed_bundle(store: RedisStore, bundle: PatchBundle) -> None:
    state = await store.load_state(RUN_ID)
    assert state is not None
    state.patch_bundle = bundle.model_dump(mode="json")
    await store.save_state(state)


async def test_patch_endpoint_returns_the_stored_bundle(client):
    http, store = client
    await _seed_run(store)
    await _seed_bundle(store, _bundle())

    response = await http.get(f"/api/runs/{RUN_ID}/patch")
    assert response.status_code == 200

    body = response.json()
    assert body["issue_id"] == "fix-auth"
    assert body["style_exemplar_commit"] == "deadbeef"
    # Original and patched source are the fields the agent projection never
    # publishes — it emits eight preview lines — so the diff view is
    # unbuildable without them.
    assert body["patches"][0]["original"].startswith("def validate_token")
    assert "check_exp" in body["patches"][0]["patched"]
    assert body["contracts"][0]["location"] == "vulnapi/auth.py::validate_token"
    assert body["diff_text"].startswith("--- a/vulnapi/auth.py")


async def test_patch_endpoint_passes_the_write_method_through(client):
    """The integrity badge may claim only what A7 stamped, so `method` has to
    survive the route unaltered — including a value the UI has no badge for."""
    http, store = client
    await _seed_run(store)
    bundle = _bundle()
    bundle.patches[0].method = "libcst"
    await _seed_bundle(store, bundle)

    body = (await http.get(f"/api/runs/{RUN_ID}/patch")).json()
    assert body["patches"][0]["method"] == "libcst"


async def test_patch_endpoint_404s_before_a7_completes(client):
    http, store = client
    await _seed_run(store)

    response = await http.get(f"/api/runs/{RUN_ID}/patch")
    assert response.status_code == 404
    assert "patch bundle" in response.json()["detail"].lower()


async def test_patch_endpoint_distinguishes_no_bundle_from_an_empty_one(client):
    """A7 completing with zero patches is a result, not an absence: every plan
    failed the integrity check. That has to reach the client as a 200 with an
    empty list, or the stage reports "not generated yet" for a run that did."""
    http, store = client
    await _seed_run(store)
    await _seed_bundle(store, PatchBundle(issue_id="fix-auth"))

    response = await http.get(f"/api/runs/{RUN_ID}/patch")
    assert response.status_code == 200
    assert response.json()["patches"] == []


async def test_patch_endpoint_404s_for_an_unknown_run(client):
    http, _store = client
    response = await http.get(f"/api/runs/{MISSING_RUN_ID}/patch")
    assert response.status_code == 404


# -- G4: repository metadata -----------------------------------------------


async def test_header_exposes_repository_identity(client):
    http, store = client
    await _seed_run(store)
    await store.set_json(
        RUN_ID,
        INTELLIGENCE_STORE_KEY,
        {"repository_id": "repo-abc", "head_sha": "cafebabe", "repository_hash": "h1"},
    )

    body = (await http.get(f"/api/runs/{RUN_ID}")).json()

    assert body["repositoryId"] == "repo-abc"
    assert body["headSha"] == "cafebabe"
    assert body["repositoryHash"] == "h1"
    assert body["repositoryName"]
    assert body["branch"]


async def test_header_identity_without_the_index_layer(client):
    http, store = client
    await _seed_run(store)

    body = (await http.get(f"/api/runs/{RUN_ID}")).json()

    # Derived identity is still published; the unobserved commit is not.
    assert body["repositoryId"]
    assert body["headSha"] == "abc123"  # the recorded base commit


async def test_header_keeps_the_v1_contract(client):
    http, store = client
    await _seed_run(store)

    body = (await http.get(f"/api/runs/{RUN_ID}")).json()
    for key in (
        "repository", "branch", "shortRunId", "retries",
        "executionTime", "decisionLabel", "runId", "status", "currentAgent",
    ):
        assert key in body, f"V1 header key {key} disappeared"


# -- G5: lifecycle ---------------------------------------------------------


async def test_header_replays_lifecycle_events(client):
    http, store = client
    await _seed_run(store)
    await store.append_lifecycle_event(RunLifecycleEvent(type="run.started", run_id=RUN_ID))
    await store.append_lifecycle_event(
        RunLifecycleEvent(
            type="run.completed", run_id=RUN_ID, decision="draft", decision_label="Draft PR"
        )
    )

    body = (await http.get(f"/api/runs/{RUN_ID}")).json()

    assert [e["type"] for e in body["lifecycle"]] == ["run.started", "run.completed"]
    assert body["lifecycle"][1]["decision"] == "draft"
    assert body["lifecycle"][1]["decision_label"] == "Draft PR"
    assert body["lifecycle"][0]["timestamp"]


async def test_lifecycle_is_empty_before_a_run_starts(client):
    http, store = client
    await _seed_run(store)
    body = (await http.get(f"/api/runs/{RUN_ID}")).json()
    assert body["lifecycle"] == []


async def test_agent_timeline_excludes_lifecycle_frames(client):
    http, store = client
    await _seed_run(store)
    await store.append_lifecycle_event(RunLifecycleEvent(type="run.started", run_id=RUN_ID))
    await store.append_event(
        AgentStatusEvent(run_id=RUN_ID, agent_id="A1", status="completed", message="done")
    )

    events = (await http.get(f"/api/runs/{RUN_ID}/events")).json()

    # V1 replays this endpoint and parses every frame as an agent event.
    assert [e["agent_id"] for e in events] == ["A1"]


# -- OpenAPI ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs/{run_id}/semantic-graph",
        "/api/runs/{run_id}/dependency-risk",
        "/api/runs/{run_id}/static-findings",
        "/api/runs/{run_id}/reproduction",
        "/api/runs/{run_id}/investigation",
        "/api/runs/{run_id}/blast",
        "/api/runs/{run_id}/repair-plan",
        "/api/runs/{run_id}/context",
        "/api/runs/{run_id}/plan",
        "/api/runs/{run_id}/patch",
        "/api/runs/{run_id}/stages",
        "/api/runs/{run_id}/agents",
    ],
)
async def test_new_routes_are_registered_in_openapi(client, path):
    http, _store = client
    schema = (await http.get("/openapi.json")).json()
    assert path in schema["paths"]


async def test_events_are_paginated_by_cursor(client):
    """B-B15 — a run past the cap lost its *oldest* events, silently.

    `xrevrange` reads the newest N, and the client replays the timeline from the
    beginning, so what fell off was the start of the run with nothing saying so.
    """
    http, store = client
    await _seed_run(store)

    for sequence in range(1, 13):
        await store.append_event(
            AgentStatusEvent(
                run_id=RUN_ID,
                agent_id="A1",
                status="completed",
                message=f"event {sequence}",
                sequence=sequence,
            )
        )

    # `after=0` starts the walk: sequences begin at 1. Omitting the cursor
    # keeps the old meaning — the most recent page — which is what every
    # existing caller still gets.
    latest = (await http.get(f"/api/runs/{RUN_ID}/events", params={"limit": 5})).json()
    assert [e["sequence"] for e in latest] == [8, 9, 10, 11, 12]

    first = (
        await http.get(f"/api/runs/{RUN_ID}/events", params={"limit": 5, "after": 0})
    ).json()
    assert [e["sequence"] for e in first] == [1, 2, 3, 4, 5]

    cursor = first[-1]["sequence"]
    second = (
        await http.get(f"/api/runs/{RUN_ID}/events", params={"limit": 5, "after": cursor})
    ).json()
    assert [e["sequence"] for e in second] == [6, 7, 8, 9, 10]

    # A short page is how the caller knows it has the whole timeline.
    last = (
        await http.get(
            f"/api/runs/{RUN_ID}/events", params={"limit": 5, "after": second[-1]["sequence"]}
        )
    ).json()
    assert [e["sequence"] for e in last] == [11, 12]


async def test_events_default_to_the_whole_timeline_for_existing_clients(client):
    """The response stays a plain array; no client had to change."""
    http, store = client
    await _seed_run(store)
    await store.append_event(
        AgentStatusEvent(run_id=RUN_ID, agent_id="A1", status="completed", sequence=1)
    )

    events = (await http.get(f"/api/runs/{RUN_ID}/events")).json()
    assert isinstance(events, list)
    assert events[0]["sequence"] == 1
