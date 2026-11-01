"""Tests for the frontend view-model projection layer."""

from datetime import datetime, timedelta


import pytest

from backend.services.ui_projection import (
    SEMANTIC_ROLES,
    STAGE_REGISTRY,
    SURFACE_V1,
    SURFACE_V2,
    _visualization_for,
    agents_for_surface,
    build_agent_entries,
    build_dependency_risk,
    build_executive_summary,
    build_repair_attempts,
    build_run_report,
    build_mergeability_decision,
    build_reproduction_evidence,
    build_security_rescan,
    build_semantic_graph,
    build_static_findings,
    build_workspace_header,
    group_runs_by_repository,
    repo_display_name,
    run_decision,
    short_run_id,
    sidebar_status,
    total_attempts,
)
from backend.state.events import AgentStatusEvent
from backend.state.schema import RunStateModel

RUN_ID = "e8fac24c-13e5-415f-add4-a64b759e414a"


def _event(agent_id: str, status: str, message: str = "", offset: int = 0) -> AgentStatusEvent:
    return AgentStatusEvent(
        run_id=RUN_ID,
        agent_id=agent_id,
        status=status,  # type: ignore[arg-type]
        timestamp=datetime(2026, 8, 2, 12, 0, 0) + timedelta(seconds=offset),
        message=message,
        sequence=offset,
    )


def _completed_state(**overrides) -> RunStateModel:
    base = dict(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        retry_count=3,
        reproduction={
            "status": "CONFIRMED",
            "failing_test": "tests/test_auth.py::test_expired_token_rejected",
            "exception_type": "AssertionError",
            "exception_message": "expired token accepted",
            "failing_file": "vulnapi/auth.py",
            "failing_line": 27,
            "confidence": 0.9,
        },
        root_cause={
            "summary": "Missing expiry check",
            "root_cause": "validate_token() never compares exp",
            "confidence": 0.95,
            "citations": [{"file": "vulnapi/auth.py", "line": 27, "claim": "no expiry", "verified": True}],
        },
        blast_graph={
            "scope": [{"path": "vulnapi/auth.py", "direction": "forward", "hop_count": 0}],
            "auto_patch_scope": ["vulnapi/auth.py"],
            "human_review_required": [],
            "origins": ["vulnapi/auth.py"],
        },
        mutation_result={"pytest_passed": True, "correctness_score": 92.0, "mutation_score": 0.9},
        security_result={"security_score": 100.0, "rejected": False, "new_findings": []},
        pr_decision={
            "pr_type": "auto_mergeable",
            "axis_scores": {
                "correctness": 92.0,
                "security": 100.0,
                "fidelity": 100.0,
                "scope_risk": 90.0,
            },
        },
    )
    base.update(overrides)
    return RunStateModel(**base)


def test_short_run_id_elides_middle():
    assert short_run_id(RUN_ID) == "e8fa…414a"
    assert short_run_id("abc") == "abc"


def test_repo_display_name_from_path():
    assert repo_display_name(RunStateModel(run_id="r", repo_path="/tmp/x/vulnapi")) == "vulnapi"
    assert repo_display_name(RunStateModel(run_id="r", repo_path="")) == "repository"


@pytest.mark.parametrize(
    "pr_type,expected_decision,expected_label",
    [
        ("auto_mergeable", "merge", "Auto Merge"),
        ("diff_only", "draft", "Diff Only"),
        ("draft", "draft", "Review required"),
    ],
)
def test_run_decision_maps_pr_types(pr_type, expected_decision, expected_label):
    state = _completed_state(pr_decision={"pr_type": pr_type})
    assert run_decision(state) == (expected_decision, expected_label)


def test_failed_run_decision_wins_over_pr_type():
    state = _completed_state(status="failed")
    assert run_decision(state)[0] == "failed"


@pytest.mark.parametrize(
    "env_status,expected_label",
    [
        ("unsupported", "Unsupported repository"),
        ("no_manifest", "No dependency manifest"),
        ("not_prepared", "Environment not prepared"),
        ("no_test_runner", "No test runner available"),
    ],
)
def test_blocked_run_decision_label_matches_the_probe_status(env_status, expected_label):
    # B-B19: all three blocking statuses used to collapse onto one label that
    # contradicted the `reason` text printed directly beneath it.
    state = _completed_state(status="blocked", environment={"status": env_status})
    assert run_decision(state) == ("blocked", expected_label)


def test_blocked_run_decision_label_falls_back_when_environment_is_absent():
    state = _completed_state(status="blocked", environment=None)
    assert run_decision(state) == ("blocked", "Environment not prepared")


class TestMutationCardTriState:
    """`mutant_survived` defaults to `False` whether mutation ran or not — only
    `mutation_status == "scored"` distinguishes measured from unmeasured."""

    def test_mutation_unavailable_reports_survived_as_none(self):
        state = _completed_state(
            mutation_result={"pytest_passed": True, "mutation_status": "unavailable"}
        )
        viz = _visualization_for("mutation", state)
        assert viz["data"]["score"] is None
        assert viz["data"]["survived"] is None
        assert viz["data"]["survivedMutants"] is None

    def test_mutation_scored_with_zero_survivors(self):
        state = _completed_state(
            mutation_result={
                "pytest_passed": True,
                "mutation_status": "scored",
                "mutation_score": 1.0,
                "mutant_survived": False,
                "survived_mutants": 0,
            }
        )
        viz = _visualization_for("mutation", state)
        assert viz["data"]["survived"] is False
        assert viz["data"]["survivedMutants"] == 0

    def test_mutation_scored_with_survivors(self):
        state = _completed_state(
            mutation_result={
                "pytest_passed": True,
                "mutation_status": "scored",
                "mutation_score": 0.7,
                "mutant_survived": True,
                "survived_mutants": 3,
            }
        )
        viz = _visualization_for("mutation", state)
        assert viz["data"]["survived"] is True
        assert viz["data"]["survivedMutants"] == 3


class TestMergeCardCompositeIsSingleSourced:
    """The merge card's composite score must be `_trust_score`'s own number —
    not a second formula recomputed from the per-axis metrics in the frontend."""

    def test_composite_matches_trust_score_when_all_axes_measured(self):
        state = _completed_state(
            pr_decision={
                "pr_type": "auto_mergeable",
                "axis_scores": {
                    "correctness": 100.0,
                    "security": 100.0,
                    "fidelity": 100.0,
                    "scope_risk": 75.0,
                },
            }
        )
        viz = _visualization_for("merge", state)
        # (100+100+100+75)/4 = 93.75 -> 0.94 -> 94
        assert viz["data"]["compositeScore"] == 94
        assert "weights" not in viz["data"]

    def test_composite_is_none_when_nothing_measured(self):
        state = _completed_state(pr_decision={"pr_type": "draft", "axis_scores": {}})
        viz = _visualization_for("merge", state)
        assert viz is None  # no axis_scores at all -> no card


def test_sidebar_status_reports_running_while_in_flight():
    assert sidebar_status(_completed_state(status="running")) == "running"
    assert sidebar_status(_completed_state()) == "completed"


@pytest.mark.parametrize("surface", [SURFACE_V1, SURFACE_V2])
def test_every_registry_agent_produces_a_card(surface):
    state = _completed_state()
    events = [_event("A1", "completed", "SIG built", 1)]
    entries = build_agent_entries(state, events, surface=surface)

    published = agents_for_surface(surface)
    assert len(entries) == len(published)
    assert [e["id"] for e in entries] == [a.card for a in published]
    # Every card must satisfy the frontend's AgentEntry contract.
    for entry in entries:
        assert entry["lines"], f"{entry['id']} has no narrative"
        assert entry["evidence"]["title"]
        # `context` is the one deliberate exception: its Supporting Metrics
        # grid duplicated `ContextEngineeringPanel` field-for-field, so it is
        # `None` (the card omits the section) rather than a list.
        if entry["id"] == "context":
            assert entry["metrics"] is None
        else:
            assert isinstance(entry["metrics"], list)
        # Identity and stage travel with the card so no client rebuilds them.
        assert entry["agentId"]
        assert entry["stage"] in {s.id for s in STAGE_REGISTRY}
        assert entry["stageLabel"]


def test_agent_without_events_is_skipped_when_run_is_terminal():
    # A9 leaves no result behind when the pipeline routes around it.
    state = _completed_state(security_result=None)
    entries = {e["id"]: e for e in build_agent_entries(state, [])}
    assert entries["security"]["status"] == "skipped"
    assert "Skipped" in entries["security"]["lines"][0]


def test_agent_without_events_is_running_while_run_is_active():
    state = _completed_state(status="running")
    entries = {e["id"]: e for e in build_agent_entries(state, [])}
    assert entries["security"]["status"] == "running"


def test_mutation_card_reports_retry_when_patch_retry_required():
    state = _completed_state(
        mutation_result={"pytest_passed": False, "patch_retry_required": True, "correctness_score": 0.0}
    )
    entries = {e["id"]: e for e in build_agent_entries(state, [_event("A8", "completed", "done", 1)])}
    assert entries["mutation"]["status"] == "retry"


def test_workspace_header_shape():
    state = _completed_state()
    events = [_event("A1", "started", "go", 0), _event("A10", "completed", "done", 72)]
    header = build_workspace_header(state, events)

    assert header["repository"] == "vulnapi"
    assert header["shortRunId"] == "e8fa…414a"
    assert header["retries"] == 3
    assert header["executionTime"] == "1m 12s"
    assert header["decisionLabel"] == "Auto Merge"


def test_attempt_counts_agree_across_models():
    """Executive summary, report and retry sequence must not disagree."""
    state = _completed_state()
    summary = build_executive_summary(state, [])
    report = build_run_report(state, [])
    attempts = build_repair_attempts(state, [])

    assert summary["attempts"] == total_attempts(state) == 4
    assert report["rejection"]["attempts"] == 4
    assert len(attempts["attempts"]) == 4
    assert attempts["attempts"][-1]["n"] == 4


def _attempt_events() -> list[AgentStatusEvent]:
    """Two patch/validate cycles with genuinely different outcomes."""
    def _with_payload(agent_id: str, offset: int, payload: dict) -> AgentStatusEvent:
        event = _event(agent_id, "completed", offset=offset)
        event.payload = payload
        return event

    return [
        _with_payload("A7", 1, {"a7_patch_metrics": {"target_file": "app/db.py", "retry_number": 0}}),
        _with_payload(
            "A8",
            2,
            {
                "pytest_passed": False,
                "patch_retry_required": True,
                "correctness_score": 0.0,
                "mutation_score": None,
                "failing_test": "tests/test_db.py::test_query",
                "assertion_message": "expected parameterised query",
            },
        ),
        _with_payload(
            "A7",
            3,
            {"a7_patch_metrics": {"target_file": "app/db.py", "retry_number": 1,
                                  "retry_reason": "validation_failure"}},
        ),
        _with_payload(
            "A8",
            4,
            {
                "pytest_passed": True,
                "patch_retry_required": False,
                "correctness_score": 84.0,
                "mutation_score": 0.6,
            },
        ),
    ]


def test_repair_attempts_are_derived_per_attempt_not_from_final_state():
    """Each attempt reports its own outcome, not the last attempt's."""
    state = _completed_state()
    attempts = build_repair_attempts(state, _attempt_events())["attempts"]

    assert len(attempts) == 2
    assert attempts[0]["result"] == "Validation Failed"
    assert attempts[1]["result"] == "Validation Passed"
    # The rows must differ — the old projection made every row identical.
    assert attempts[0]["detail"] != attempts[1]["detail"]
    assert "expected parameterised query" in attempts[0]["detail"]


def test_unmeasured_attempt_is_not_reported_as_a_zero_score():
    """mutmut only runs once pytest passes; a failed attempt has no score."""
    attempts = build_repair_attempts(_completed_state(), _attempt_events())["attempts"]

    # Failed attempt: no mutation score exists, so it must not read as 0.00.
    assert attempts[0]["scoreLabel"] == "correctness 0.00"
    assert "mutation" not in attempts[0]["scoreLabel"]
    # Passed attempt: mutmut ran, so the real measurement is shown.
    assert attempts[1]["scoreLabel"] == "mutation 0.60"


def test_skipped_security_scan_is_not_reported_as_clean():
    state = _completed_state(security_result=None)
    report = build_run_report(state, [])
    flags = {e["text"]: e["ok"] for e in report["evidence"]}
    assert flags["Security re-scan not run"] is False
    assert "Security re-scan clean" not in flags


def test_rescan_with_no_scanner_available_is_not_reported_as_clean():
    """A9 ran, found nothing, and verified nothing — `security_score is None`.

    It rejects no findings because it examined no code, so `rejected: False`
    must not be read as a clean bill of health.
    """
    state = _completed_state(
        security_result={"security_score": None, "rejected": False, "new_findings": []}
    )
    report = build_run_report(state, [])
    flags = {e["text"]: e["ok"] for e in report["evidence"]}

    assert flags["Security re-scan not run"] is False
    assert "Security re-scan clean" not in flags


def test_clean_security_scan_is_reported_as_clean():
    report = build_run_report(_completed_state(), [])
    flags = {e["text"]: e["ok"] for e in report["evidence"]}
    assert flags["Security re-scan clean"] is True


def test_trust_score_is_mean_of_axes():
    report = build_run_report(_completed_state(), [])
    # (92 + 100 + 100 + 90) / 4 / 100 == 0.955
    assert report["trustScore"] == pytest.approx(0.955, abs=0.006)
    assert [t["label"] for t in report["trust"]] == [
        "Correctness",
        "Security",
        "Fidelity",
        # High is good on this axis, same as the other three — see the note in
        # build_run_report on why it is not labelled "Scope Risk".
        "Scope Safety",
    ]


def test_run_report_survives_an_empty_run():
    """A run that failed before any agent produced output must still project."""
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="failed")
    report = build_run_report(state, [])
    summary = build_executive_summary(state, [])

    assert report["decision"] == "failed"
    # `None`, not 0.0. A run that failed before any agent produced output has no
    # measurements, and reporting 0.0 asserted it scored the worst possible
    # result on four axes nobody evaluated.
    assert report["trustScore"] is None
    assert report["proofBundle"] == "—"
    assert summary["repository"] == "vulnapi"


def test_run_report_exposes_recovery_evidence_and_block_reason():
    state = RunStateModel(
        run_id=RUN_ID, repo_path="vulnapi", status="blocked",
        recovery_blocked_reason="Generated patch exactly repeats a failed attempt",
        recovery_attempts=[{
            "number": 1, "target": "auth.py", "diagnosis": "expiry guard missing",
            "patch_hash": "a" * 64, "failing_test": "tests/test_auth.py::test_expired",
            "failure": "assert True is False", "failure_category": "implementation_failure",
            "validation_status": "executed",
            "recovery_decision": "generate_code", "reason": "Target behavior still fails",
            "changed_from_previous": None,
        }],
    )
    report = build_run_report(state, [])
    assert report["decisionLabel"] == "Recovery stopped"
    assert report["decisionReason"] == state.recovery_blocked_reason
    assert report["recoveryAttempts"][0] == {
        "number": 1, "target": "auth.py", "diagnosis": "expiry guard missing",
        "patchHash": "a" * 64, "failingTest": "tests/test_auth.py::test_expired",
        "failure": "assert True is False", "category": "implementation_failure",
        "validationStatus": "executed",
        "decision": "generate_code", "reason": "Target behavior still fails",
        "changedFromPrevious": None,
    }


def test_group_runs_by_repository_buckets_by_repo():
    states = [
        _completed_state(),
        _completed_state(run_id="other", repo_path="llm-shield"),
        RunStateModel(run_id="third", repo_path="vulnapi", status="running"),
    ]
    grouped = {g["name"]: g["runs"] for g in group_runs_by_repository(states)}

    assert set(grouped) == {"vulnapi", "llm-shield"}
    assert len(grouped["vulnapi"]) == 2


# -- the summary must not claim gates a run never reached ---------------------


def test_blocked_run_does_not_claim_trust_gates_were_satisfied():
    """"All trust gates satisfied" was the default for any run with no review
    note — including one that stopped at the environment precheck, before a
    single gate existed to satisfy."""
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="blocked")
    state.environment = {"status": "not_prepared", "reason": "pytest is not importable"}

    summary = build_executive_summary(state, [])

    assert summary["decision"] == "blocked"
    assert "All trust gates satisfied" not in summary["decisionReason"]
    assert summary["decisionReason"]


def test_auto_mergeable_run_may_still_say_gates_were_satisfied():
    """The sentence is true of exactly one outcome, and that one keeps it."""
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed")
    state.pr_decision = {"pr_type": "auto_mergeable", "axis_scores": {}}

    summary = build_executive_summary(state, [])

    # `run_decision` speaks the UI's vocabulary: `auto_mergeable` is "merge".
    assert summary["decision"] == "merge"
    assert summary["decisionReason"] == "All trust gates satisfied."


def test_confidence_is_not_measured_when_a4_never_ran():
    """`(confidence or 0) * 100` rendered "0.0%" for an investigation that never
    happened, which reads as a measured no-confidence verdict."""
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="blocked")

    assert build_executive_summary(state, [])["confidence"] == "not measured"


def test_severity_is_not_measured_when_no_static_findings_exist():
    """The old floor was "LOW", which a run blocked before A3 ever ran reported
    as though a scan had come back clean."""
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="blocked")

    assert build_executive_summary(state, [])["severity"] == "not measured"


def test_severity_of_a_real_low_finding_is_still_low():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed")
    state.static_report = {"prioritized": [{"severity": 0.1}]}

    assert build_executive_summary(state, [])["severity"] == "LOW"


def test_severity_reads_the_top_ranked_finding():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed")
    state.static_report = {"prioritized": [{"severity": 0.95}, {"severity": 0.1}]}

    assert build_executive_summary(state, [])["severity"] == "CRITICAL"


def test_a_real_zero_confidence_is_still_reported_as_a_number():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed")
    state.root_cause = {"root_cause": "unclear", "confidence": 0.0}

    assert build_executive_summary(state, [])["confidence"] == "0.0%"


def test_pytest_unavailability_is_read_from_the_recorded_fact():
    """A8 decides this where pytest is invoked; the projection must not have to
    guess it from a traceback string it never saw."""
    from backend.services.ui_projection import _pytest_unavailable

    assert _pytest_unavailable({"pytest_available": False}) is True
    assert _pytest_unavailable({"pytest_available": True}) is False
    # Runs stored before the field existed still resolve through the old path.
    assert _pytest_unavailable(
        {"failure_brief": {"stack_trace": "No module named pytest"}}
    ) is True


# -- the environment precheck must be visible on the V1 surface ---------------
#
# A blocked run reaches exactly one agent: A0.7. Before it was in the registry,
# V1 published eleven cards none of which corresponded to the stage the run
# actually stopped at, so the boundary was invisible and the journal read as a
# pipeline still working through its first stage.


def _blocked_state() -> RunStateModel:
    state = RunStateModel(run_id=RUN_ID, repo_path="quant_med", status="blocked")
    state.environment = {
        "status": "blocked",
        "language": "python",
        "blocking": True,
        "test_runner": "pytest",
        "test_runner_available": False,
        "reason": "No dependency manifest found in the repository.",
        "suggested_command": "pip install -e .",
        "manifests": [],
    }
    return state


def _a07_events() -> list[AgentStatusEvent]:
    return [
        AgentStatusEvent(
            run_id=RUN_ID,
            agent_id="A0.7",
            status="started",
            message="Checking whether the test suite can run",
        ),
        AgentStatusEvent(
            run_id=RUN_ID,
            agent_id="A0.7",
            status="failed",
            message="No dependency manifest found in the repository.",
        ),
    ]


def test_v1_publishes_an_environment_card_for_a_blocked_run():
    entries = build_agent_entries(_blocked_state(), _a07_events(), surface=SURFACE_V1)
    card = next(e for e in entries if e["id"] == "environment")

    assert card["agentId"] == "A0.7"
    assert card["index"] == 1  # first stage the pipeline executes
    assert card["status"] == "failed"
    # The reason is the probe's own sentence, rendered verbatim.
    assert "No dependency manifest found in the repository." in card["lines"]


def test_blocked_run_does_not_fabricate_downstream_agents():
    entries = build_agent_entries(_blocked_state(), _a07_events(), surface=SURFACE_V1)
    downstream = [e for e in entries if e["id"] != "environment"]

    assert downstream, "the V1 rail still publishes the rest of the pipeline"
    # Nothing after the precheck ran, and nothing claims it did.
    assert {e["status"] for e in downstream} == {"skipped"}
    assert not any(e["status"] == "completed" for e in downstream)


def test_environment_card_reports_the_probe_verbatim():
    entries = build_agent_entries(_blocked_state(), _a07_events(), surface=SURFACE_V1)
    card = next(e for e in entries if e["id"] == "environment")

    metrics = {m["label"]: m["value"] for m in card["metrics"]}
    assert metrics["Environment"] == "blocked"
    assert metrics["Test Runner"] == "pytest"

    fields = {f["label"]: f["value"] for f in card["evidence"]["fields"]}
    assert fields["Reason"] == "No dependency manifest found in the repository."
    assert fields["Suggested command"] == "pip install -e ."


def test_environment_card_says_nothing_when_the_precheck_did_not_run():
    """Stub mode and a disabled precheck both leave `environment` empty. The
    card must report absence rather than invent a verdict."""
    state = RunStateModel(run_id=RUN_ID, repo_path="quant_med", status="completed")
    entries = build_agent_entries(state, [], surface=SURFACE_V1)
    card = next(e for e in entries if e["id"] == "environment")

    assert card["status"] == "skipped"
    assert card["evidence"]["fields"] == []


def test_blocked_header_carries_the_status_and_the_environment_report():
    """The client settles a blocked run from these two fields alone."""
    header = build_workspace_header(_blocked_state(), _a07_events())

    assert header["decisionLabel"] == "Environment not prepared"
    assert header["environment"]["reason"] == (
        "No dependency manifest found in the repository."
    )


def test_blocked_run_is_never_projected_as_running_or_completed():
    state = _blocked_state()
    assert sidebar_status(state) == "blocked"
    assert run_decision(state) == ("blocked", "Environment not prepared")


# -- A1 semantic graph projection ------------------------------------------


def _sig_state(files: dict) -> RunStateModel:
    return RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        sig={
            "repo_path": "/tmp/vulnapi",
            "source_roots": ["vulnapi"],
            "files": files,
            "edges": [["vulnapi/routes.py", "vulnapi/auth.py"]],
            "generated_at": "2026-08-13T00:00:00",
        },
    )


def test_semantic_graph_is_none_before_a1_completes():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="running")
    assert build_semantic_graph(state) is None


def test_semantic_graph_counts_every_declared_role_even_at_zero():
    state = _sig_state(
        {
            "vulnapi/auth.py": {
                "role": "auth-boundary",
                "imports": [],
                "imported_by": ["vulnapi/routes.py"],
                "churn_weight": 0.2,
                "criticality": 0.9,
            },
        }
    )
    graph = build_semantic_graph(state)
    assert graph is not None
    assert set(graph["roleCounts"]) == set(SEMANTIC_ROLES)
    assert graph["roleCounts"]["auth-boundary"] == 1
    assert graph["roleCounts"]["data-access"] == 0


def test_semantic_graph_files_are_camelcased_and_sorted_by_criticality():
    state = _sig_state(
        {
            "vulnapi/utils.py": {
                "role": "internal-util",
                "imports": ["vulnapi/config.py"],
                "imported_by": [],
                "churn_weight": 0.1,
                "criticality": 0.2,
            },
            "vulnapi/routes.py": {
                "role": "public-api",
                "imports": ["vulnapi/auth.py"],
                "imported_by": [],
                "churn_weight": 0.5,
                "criticality": 0.8,
            },
        }
    )
    graph = build_semantic_graph(state)
    assert graph is not None
    assert [f["path"] for f in graph["files"]] == ["vulnapi/routes.py", "vulnapi/utils.py"]
    top = graph["files"][0]
    assert top["role"] == "public-api"
    assert top["imports"] == ["vulnapi/auth.py"]
    assert top["importedBy"] == []
    assert top["churnWeight"] == 0.5
    assert top["criticality"] == 0.8
    assert graph["sourceRoots"] == ["vulnapi"]
    assert graph["edges"] == [["vulnapi/routes.py", "vulnapi/auth.py"]]
    assert graph["totalFiles"] == 2
    assert graph["totalEdges"] == 1


def test_dependency_risk_is_none_before_a2_completes():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="running")
    assert build_dependency_risk(state) is None


def test_dependency_risk_reports_a_clean_manifest_distinctly_from_never_ran():
    """A2 completed, found no advisories, but the manifest was real — this must
    not collapse into the same `None` a never-run A2 returns."""
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        cve_report={
            "findings": [],
            "critical_queue": [],
            "total_dependencies": 12,
            "manifest": "requirements.txt",
            "ecosystem": "PyPI",
        },
    )
    risk = build_dependency_risk(state)
    assert risk is not None
    assert risk["manifest"] == "requirements.txt"
    assert risk["totalDependencies"] == 12
    assert risk["advisoryCount"] == 0
    assert risk["findings"] == []


def test_dependency_risk_reports_no_manifest_when_a2_found_none():
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        cve_report={"findings": [], "critical_queue": [], "total_dependencies": 0},
    )
    risk = build_dependency_risk(state)
    assert risk is not None
    assert risk["manifest"] is None
    assert risk["totalDependencies"] == 0


def test_dependency_risk_camelcases_and_orders_by_classification():
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        cve_report={
            "findings": [
                {
                    "package": "requests",
                    "cve_id": "CVE-INFO",
                    "severity": "5.0",
                    "installed_version": "2.31.0",
                    "reachable": False,
                    "reach_path": None,
                    "classification": "Informational",
                },
                {
                    "package": "urllib3",
                    "cve_id": "CVE-2023-45803",
                    "severity": "7.5",
                    "installed_version": "1.26.5",
                    "reachable": True,
                    "reach_path": ["vulnapi/net.py"],
                    "classification": "Critical",
                },
                {
                    "package": "mystery",
                    "cve_id": "CVE-UNK",
                    "severity": "HIGH",
                    "installed_version": None,
                    "reachable": None,
                    "reach_path": None,
                    "classification": "Unknown",
                },
            ],
            "critical_queue": ["CVE-2023-45803"],
            "total_dependencies": 3,
            "manifest": "requirements.txt",
            "ecosystem": "PyPI",
        },
    )
    risk = build_dependency_risk(state)
    assert risk is not None
    # Reachable (Critical) first, then undetermined, then confirmed-inert.
    assert [f["package"] for f in risk["findings"]] == ["urllib3", "mystery", "requests"]
    top = risk["findings"][0]
    assert top["cveId"] == "CVE-2023-45803"
    assert top["installedVersion"] == "1.26.5"
    assert top["reachPath"] == ["vulnapi/net.py"]
    assert top["affectedSymbol"] is None
    assert risk["reachableCount"] == 1
    assert risk["informationalCount"] == 1
    assert risk["unknownCount"] == 1
    assert risk["advisoryCount"] == 3


def test_static_findings_is_none_before_a3_completes():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="running")
    assert build_static_findings(state) is None


def test_static_findings_reports_a_clean_run_distinctly_from_never_ran():
    """A3 completed, ranked nothing, but the scanners really ran — this must
    not collapse into the same `None` a never-run A3 returns."""
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        static_report={
            "raw_count": 0,
            "prioritized": [],
            "baseline_json": {},
            "scanner_status": {"bandit": "ok_no_findings", "semgrep": "unavailable", "ruff": "ok_no_findings"},
        },
    )
    result = build_static_findings(state)
    assert result is not None
    assert result["rawCount"] == 0
    assert result["findings"] == []
    assert result["scannerStatus"]["bandit"] == "ok_no_findings"
    assert result["scannerStatus"]["semgrep"] == "unavailable"


def test_static_findings_camelcases_and_ranks_in_backend_order():
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        static_report={
            "raw_count": 5,
            "prioritized": [
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
                },
                {
                    "id": "finding-1",
                    "file": "vulnapi/routes.py",
                    "line": 3,
                    "message": "unused import",
                    "tools": ["ruff"],
                    "severity": 0.4,
                    "blast_radius_score": 0.18,
                    "consensus": False,
                    "criticality": 0.55,
                    "churn_weight": 0.1,
                    "severity_measured": False,
                },
            ],
            "baseline_json": {},
            "scanner_status": {"bandit": "ok", "semgrep": "unavailable", "ruff": "ok"},
        },
    )
    result = build_static_findings(state)
    assert result is not None
    assert result["rawCount"] == 5
    assert result["prioritizedCount"] == 2
    # Backend order is preserved — never re-ranked in the projection.
    assert [f["file"] for f in result["findings"]] == ["vulnapi/auth.py", "vulnapi/routes.py"]
    assert [f["rank"] for f in result["findings"]] == [1, 2]
    top = result["findings"][0]
    assert top["severityMeasured"] is True
    assert top["blastRadiusScore"] == 0.62
    assert top["criticality"] == 0.81
    assert top["churnWeight"] == 0.37
    bottom = result["findings"][1]
    assert bottom["severityMeasured"] is False
    assert bottom["tools"] == ["ruff"]


def test_security_rescan_is_none_before_a9_completes():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="running")
    assert build_security_rescan(state) is None


def test_security_rescan_not_measured_when_no_scanner_ran():
    """`security_score is None` must project to `not_measured`, never `clean`."""
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        security_result={
            "new_findings": [],
            "rejected": False,
            "security_score": None,
            "scanners_run": [],
            "reexecution_command": "bandit -f json -q -r vulnapi",
            "reexecution_timeout_seconds": 60,
        },
    )
    result = build_security_rescan(state)
    assert result is not None
    assert result["verdict"] == "not_measured"
    assert result["securityScore"] is None
    assert result["scannersRun"] == []
    assert result["newFindings"] == []


def test_security_rescan_clean_run_reports_real_score():
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        security_result={
            "new_findings": [],
            "rejected": False,
            "security_score": 100.0,
            "scanners_run": ["bandit", "semgrep"],
            "reexecution_command": "bandit -f json -q -r vulnapi",
            "reexecution_timeout_seconds": 60,
        },
    )
    result = build_security_rescan(state)
    assert result is not None
    assert result["verdict"] == "clean"
    assert result["rejected"] is False
    assert result["securityScore"] == 100.0
    assert result["scannersRun"] == ["bandit", "semgrep"]
    assert result["retryContext"] is None


def test_security_rescan_new_finding_projects_no_severity_field():
    """A9's findings carry no measured severity — the projection must not
    invent one by leaking the constant `severity` field through."""
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        security_result={
            "new_findings": [
                {
                    "id": "new-0",
                    "file": "vulnapi/auth.py",
                    "line": 42,
                    "message": "Possible hardcoded password: 'hunter2'",
                    "tools": ["bandit"],
                    "severity": 0.7,
                }
            ],
            "rejected": True,
            "security_score": 75.0,
            "scanners_run": ["bandit"],
            "failure_brief": {"security_constraint": "must not introduce hardcoded secrets"},
            "validation_failure": {"assertion_message": "New security finding: hardcoded password"},
            "reexecution_command": "bandit -f json -q -r vulnapi",
            "reexecution_timeout_seconds": 60,
        },
    )
    result = build_security_rescan(state)
    assert result is not None
    assert result["verdict"] == "new_findings"
    assert result["rejected"] is True
    assert result["securityScore"] == 75.0
    finding = result["newFindings"][0]
    assert finding == {
        "id": "new-0",
        "file": "vulnapi/auth.py",
        "line": 42,
        "message": "Possible hardcoded password: 'hunter2'",
        "tools": ["bandit"],
    }
    assert "severity" not in finding
    assert result["retryContext"] == {
        "assertionMessage": "New security finding: hardcoded password",
        "securityConstraint": "must not introduce hardcoded secrets",
    }


def _reconciled_state(reconciliation, **overrides) -> RunStateModel:
    security = {
        "new_findings": [],
        "rejected": False,
        "security_score": 100.0,
        "scanners_run": ["bandit"],
        "reconciliation": reconciliation,
        "reexecution_command": "bandit -f json -q -r vulnapi",
        "reexecution_timeout_seconds": 60,
    }
    security.update(overrides)
    return RunStateModel(
        run_id=RUN_ID, repo_path="vulnapi", status="completed", security_result=security
    )


def test_security_rescan_projects_a_carried_pair_with_its_line_delta():
    """The point of the ledger: a shifted finding is visibly shifted, not
    silently absent from the new-findings list."""
    state = _reconciled_state(
        [
            {
                "tool": "bandit",
                "compared": True,
                "keys": [
                    {
                        "tool": "bandit",
                        "file": "vulnapi/auth.py",
                        "message": "Possible hardcoded password",
                        "baseline": [{"line": 12}],
                        "post": [{"line": 14}],
                        "matched": [
                            {"baseline_index": 0, "post_index": 0, "line_delta": 2}
                        ],
                        "introduced_indexes": [],
                        "resolved_indexes": [],
                    }
                ],
            },
            {"tool": "semgrep", "compared": False, "keys": []},
        ]
    )

    result = build_security_rescan(state)

    assert result["reconciliationAvailable"] is True
    bandit_lane, semgrep_lane = result["reconciliation"]
    key = bandit_lane["keys"][0]
    assert key["baseline"] == [{"id": "b-bandit-0-0", "line": 12}]
    assert key["post"] == [{"id": "p-bandit-0-0", "line": 14}]
    assert key["rails"] == [
        {"baselineId": "b-bandit-0-0", "postId": "p-bandit-0-0", "lineDelta": 2}
    ]
    assert key["introducedIds"] == []
    # A scanner that did not run is a lane that says so, never an empty pass.
    assert semgrep_lane == {"tool": "semgrep", "compared": False, "keys": []}


def test_security_rescan_projects_multiplicity_as_one_rail_and_one_dangling_chip():
    state = _reconciled_state(
        [
            {
                "tool": "bandit",
                "compared": True,
                "keys": [
                    {
                        "tool": "bandit",
                        "file": "vulnapi/auth.py",
                        "message": "Possible hardcoded password",
                        "baseline": [{"line": 12}],
                        "post": [{"line": 12}, {"line": 40}],
                        "matched": [
                            {"baseline_index": 0, "post_index": 0, "line_delta": 0}
                        ],
                        "introduced_indexes": [1],
                        "resolved_indexes": [],
                    }
                ],
            },
            {"tool": "semgrep", "compared": True, "keys": []},
        ],
        rejected=True,
        security_score=75.0,
        scanners_run=["bandit", "semgrep"],
    )

    key = build_security_rescan(state)["reconciliation"][0]["keys"][0]

    assert len(key["post"]) == 2
    assert len(key["rails"]) == 1
    assert key["introducedIds"] == ["p-bandit-0-1"]
    # The dangling chip is exactly the one no rail reaches.
    railed = {r["postId"] for r in key["rails"]}
    assert [p["id"] for p in key["post"] if p["id"] not in railed] == key["introducedIds"]


def test_security_rescan_reconciliation_carries_no_severity():
    """Every A9 finding is stamped with a constant 0.7. It is not a
    measurement, so no occurrence may carry it into the panel."""
    state = _reconciled_state(
        [
            {
                "tool": "bandit",
                "compared": True,
                "keys": [
                    {
                        "tool": "bandit",
                        "file": "vulnapi/auth.py",
                        "message": "Possible hardcoded password",
                        "baseline": [],
                        "post": [{"line": 40}],
                        "matched": [],
                        "introduced_indexes": [0],
                        "resolved_indexes": [],
                    }
                ],
            },
            {"tool": "semgrep", "compared": True, "keys": []},
        ]
    )

    key = build_security_rescan(state)["reconciliation"][0]["keys"][0]
    assert key["post"] == [{"id": "p-bandit-0-0", "line": 40}]
    assert "severity" not in key


def test_security_rescan_without_a_stored_reconciliation_says_so():
    """A run completed before A9 recorded the comparison must not render an
    empty ledger — "nothing carried" is a claim, and it was never measured."""
    state = _reconciled_state([], scanners_run=["bandit", "semgrep"])

    result = build_security_rescan(state)

    assert result["reconciliationAvailable"] is False
    assert result["reconciliation"] == [
        {"tool": "bandit", "compared": True, "keys": []},
        {"tool": "semgrep", "compared": True, "keys": []},
    ]


def test_security_rescan_backward_compatible_with_state_missing_scanners_run():
    """State persisted before `scanners_run` existed must still project cleanly."""
    state = RunStateModel(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        security_result={
            "new_findings": [],
            "rejected": False,
            "security_score": 100.0,
            "reexecution_command": "",
            "reexecution_timeout_seconds": 150,
        },
    )
    result = build_security_rescan(state)
    assert result is not None
    assert result["scannersRun"] == []
    assert result["verdict"] == "clean"


def _mergeable_state(**overrides) -> RunStateModel:
    base = dict(
        run_id=RUN_ID,
        repo_path="vulnapi",
        status="completed",
        reproduction={"status": "CONFIRMED"},
        reproduction_confidence="exact_test",
        mutation_result={
            "pytest_passed": True,
            "target_test_passed": True,
            "regression_tests_passed": True,
            "patch_retry_required": False,
            "correctness_score": 92.0,
        },
        security_result={"rejected": False, "security_score": 100.0},
        pr_decision={
            "pr_type": "auto_mergeable",
            "axis_scores": {
                "correctness": 92.0,
                "security": 100.0,
                "fidelity": 100.0,
                "scope_risk": 90.0,
            },
            "pr_url": "https://github.com/acme/vulnapi/pull/DRY_RUN",
            "description_why": "Token expiry not checked",
            "description_what": "Adds an expiry comparison in validate_token.",
            "review_note": None,
            "phantom_changes_detected": False,
        },
        proof_bundle={
            "issue_id": "issue-1",
            "steps": [
                {
                    "name": "reproduction_before",
                    "command": "pytest tests/test_auth.py::test_expired",
                    "base_commit": "abc123",
                    "patch_commit": "",
                    "expected_result": "fails",
                    "timeout_seconds": 60,
                    "is_targeted": True,
                }
            ],
            "bundle_hash": "deadbeef1234",
            "reproduction_confidence": "exact_test",
        },
    )
    base.update(overrides)
    return RunStateModel(**base)


def test_mergeability_decision_is_none_before_a10_completes():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="running")
    assert build_mergeability_decision(state) is None


def test_mergeability_decision_clean_run_all_gates_pass():
    state = _mergeable_state()
    result = build_mergeability_decision(state)
    assert result is not None
    assert result["prType"] == "auto_mergeable"
    assert result["trust"] == 0.95

    correctness = next(a for a in result["axes"] if a["name"] == "correctness")
    assert correctness == {
        "name": "correctness",
        "label": "Correctness",
        "value": 92.0,
        "measured": True,
        "lowThreshold": 80.0,
        "meetsLowThreshold": True,
    }

    security = next(a for a in result["axes"] if a["name"] == "security")
    assert security["meetsLowThreshold"] is True
    assert security["autoMergeThreshold"] == 90.0
    assert security["meetsAutoMergeThreshold"] is True

    assert len(result["hardGates"]) == 10
    assert all(g["checked"] and g["passed"] for g in result["hardGates"])

    assert result["routingModifiers"] == {
        "hardGatesClear": True,
        "citationReviewNeeded": False,
        "reproductionConfidence": "exact_test",
        "securityMeetsAutoMergeThreshold": True,
    }
    assert result["phantomChangesDetected"] is False
    assert result["prUrl"] == "https://github.com/acme/vulnapi/pull/DRY_RUN"
    assert result["proofBundle"]["bundleHash"] == "deadbeef1234"
    assert result["proofBundle"]["steps"][0]["command"] == "pytest tests/test_auth.py::test_expired"


def test_mergeability_decision_unmeasured_axis_is_not_a_zero():
    state = _mergeable_state(
        security_result={},
        pr_decision={
            "pr_type": "draft",
            "axis_scores": {
                "correctness": 92.0,
                "security": None,
                "fidelity": 100.0,
                "scope_risk": 90.0,
            },
            "pr_url": None,
            "description_why": "",
            "description_what": "",
            "review_note": "Not measured: security. Manual verification required before merge.",
            "phantom_changes_detected": False,
        },
    )
    result = build_mergeability_decision(state)
    assert result is not None

    security = next(a for a in result["axes"] if a["name"] == "security")
    assert security["value"] is None
    assert security["measured"] is False
    assert security["meetsLowThreshold"] is None
    assert security["meetsAutoMergeThreshold"] is None

    firing = [g for g in result["hardGates"] if g["checked"] and g["passed"] is False]
    assert len(firing) == 1
    assert firing[0]["code"] == "axes_measured"
    assert "security" in firing[0]["detail"]

    not_reached = [g for g in result["hardGates"] if not g["checked"]]
    assert len(not_reached) == 1
    assert not_reached[0]["code"] == "reproduction_confirmed"

    # A hard-blocked run never reaches the routing modifiers.
    assert result["routingModifiers"] == {
        "hardGatesClear": False,
        "citationReviewNeeded": None,
        "reproductionConfidence": None,
        "securityMeetsAutoMergeThreshold": None,
    }
    assert result["prUrl"] is None


def test_mergeability_decision_security_gate_asymmetry_is_visible():
    """A security score of 85 clears the generic 80 bar but not the stricter
    90 auto-merge bar — both facts must be visible, not collapsed into one."""
    state = _mergeable_state(
        security_result={"rejected": False, "security_score": 85.0},
        pr_decision={
            "pr_type": "diff_only",
            "axis_scores": {
                "correctness": 92.0,
                "security": 85.0,
                "fidelity": 100.0,
                "scope_risk": 90.0,
            },
            "pr_url": None,
            "description_why": "",
            "description_what": "",
            "review_note": "Lower-confidence full-suite reproduction proof. "
            "Manual review required — not eligible for auto-merge.",
            "phantom_changes_detected": False,
        },
    )
    result = build_mergeability_decision(state)
    assert result is not None
    security = next(a for a in result["axes"] if a["name"] == "security")
    assert security["meetsLowThreshold"] is True
    assert security["meetsAutoMergeThreshold"] is False
    assert result["routingModifiers"]["hardGatesClear"] is True
    assert result["routingModifiers"]["securityMeetsAutoMergeThreshold"] is False


def test_mergeability_decision_phantoms_true_leaks_no_fabricated_identity():
    """`phantoms` is re-derived as a non-empty placeholder set purely to drive
    `bool(phantoms)` — the phantom entity names are never persisted, so the
    projection must not claim to know what they were."""
    state = _mergeable_state(
        pr_decision={
            "pr_type": "draft",
            "axis_scores": {
                "correctness": 92.0,
                "security": 100.0,
                "fidelity": 50.0,
                "scope_risk": 90.0,
            },
            "pr_url": None,
            "description_why": "",
            "description_what": "",
            "review_note": "Phantom changes detected between PR description and diff. "
            "Manual verification required before merge.",
            "phantom_changes_detected": True,
        },
    )
    result = build_mergeability_decision(state)
    assert result is not None
    assert result["phantomChangesDetected"] is True
    firing = [g for g in result["hardGates"] if g["checked"] and g["passed"] is False]
    assert firing[0]["code"] == "phantoms_detected"


def test_mergeability_decision_repository_evidence_is_none_when_nothing_ran():
    state = _mergeable_state(sig=None, blast_graph=None, patch_bundle=None)
    result = build_mergeability_decision(state)
    assert result is not None
    assert result["repositoryEvidence"] is None


def test_mergeability_decision_repository_evidence_counts_are_real_not_fabricated():
    state = _mergeable_state(
        sig={
            "files": {
                "vulnapi/auth.py": {"role": "handler"},
                "vulnapi/middleware.py": {"role": "handler"},
                "vulnapi/config.py": {"role": "config"},
            }
        },
        blast_graph={
            "scope": [
                {"path": "vulnapi/middleware.py", "hop_count": 1},
                {"path": "vulnapi/config.py", "hop_count": 2},
            ],
            "edges": [
                {"from_path": "vulnapi/auth.py", "to_path": "vulnapi/middleware.py"},
                {"from_path": "vulnapi/middleware.py", "to_path": "vulnapi/config.py"},
            ],
        },
        patch_bundle={
            "patches": [{"file": "vulnapi/auth.py", "original": "", "patched": ""}],
        },
    )
    result = build_mergeability_decision(state)
    assert result is not None
    evidence = result["repositoryEvidence"]
    assert evidence["filesAnalyzed"] == 3
    assert evidence["changedFiles"] == ["vulnapi/auth.py"]
    assert evidence["dependencyEdgeCount"] == 2
    assert evidence["blastScopeFiles"] == ["vulnapi/config.py", "vulnapi/middleware.py"]
    # Modules are directories of the real changed + blast-scope files only —
    # no path outside those two real sets appears here.
    assert evidence["affectedModules"] == ["vulnapi"]


def test_mergeability_decision_repository_evidence_distinguishes_zero_from_never_ran():
    # A5 ran and found an empty scope — a real fact, distinct from A5 never
    # having run at all, which must read as `None` rather than a bare 0.
    state = _mergeable_state(
        sig={"files": {"vulnapi/auth.py": {"role": "handler"}}},
        blast_graph={"scope": [], "edges": []},
        patch_bundle=None,
    )
    result = build_mergeability_decision(state)
    assert result is not None
    evidence = result["repositoryEvidence"]
    assert evidence["filesAnalyzed"] == 1
    assert evidence["dependencyEdgeCount"] == 0
    assert evidence["blastScopeFiles"] == []
    assert evidence["changedFiles"] == []


def test_reproduction_evidence_is_none_before_a3_5_completes():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="running")
    assert build_reproduction_evidence(state) is None


def _confirmed_repro(**overrides) -> dict:
    base = {
        "status": "CONFIRMED",
        "reproduced": True,
        "failing_test": "tests/test_auth.py::test_expired_token_rejected",
        "exception_type": "AssertionError",
        "exception_message": "assert True is False",
        "failing_file": "vulnapi/tests/test_auth.py",
        "failing_line": 27,
        "traceback": "E   AssertionError: assert True is False",
        "stack_trace": "E   AssertionError: assert True is False",
        "confidence": 0.9,
        "force_draft_pr": False,
        "report_path": "/tmp/pytest_x.json",
        "infra_detail": None,
        "reexecution_command": "python -m pytest tests/test_auth.py::test_expired_token_rejected -v --tb=long",
        "reexecution_is_targeted": True,
        "reexecution_timeout_seconds": 120,
        "pre_existing_failures": [
            "tests/test_auth.py::test_expired_token_rejected",
            "tests/test_config.py::test_secret_from_env",
        ],
        "command": "python -m pytest --tb=long --json-report -v",
        "exit_code": 1,
        "timed_out": False,
        "stdout": "collected 12 items",
        "stderr": "",
        "duration_seconds": 0.031,
        "started_at": "2026-08-13T17:30:37.636850",
        "finished_at": "2026-08-13T17:30:37.976929",
        "tests_collected": 12,
        "tests_passed": 8,
        "tests_failed": 4,
        "evidence_source": "pytest_report",
    }
    base.update(overrides)
    return base


def test_reproduction_evidence_camelcases_a_confirmed_result():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed", reproduction=_confirmed_repro())
    evidence = build_reproduction_evidence(state)
    assert evidence is not None
    assert evidence["status"] == "CONFIRMED"
    assert evidence["uiStatus"] == "reproduced"
    assert evidence["confidence"] == 0.9
    assert evidence["failingTest"] == "tests/test_auth.py::test_expired_token_rejected"
    assert evidence["failingFile"] == "vulnapi/tests/test_auth.py"
    assert evidence["errorSignature"] == "AssertionError: assert True is False"
    assert evidence["command"] == "python -m pytest --tb=long --json-report -v"
    assert evidence["exitCode"] == 1
    assert evidence["testsCollected"] == 12
    assert evidence["testsPassed"] == 8
    assert evidence["testsFailed"] == 4
    assert evidence["evidenceSource"] == "pytest_report"
    # The target itself must not appear twice — once as `failingTest`, once
    # inside `baselineFailures`.
    assert evidence["baselineFailures"] == ["tests/test_config.py::test_secret_from_env"]


def test_reproduction_evidence_maps_all_four_real_states():
    cases = [
        ("CONFIRMED", "reproduced"),
        ("UNCONFIRMED", "not_reproduced"),
        ("NO_TESTS", "unavailable"),
        ("INFRA_ERROR", "error"),
    ]
    for status, expected_ui_status in cases:
        state = RunStateModel(
            run_id=RUN_ID, repo_path="vulnapi", status="completed",
            reproduction=_confirmed_repro(status=status),
        )
        evidence = build_reproduction_evidence(state)
        assert evidence is not None
        assert evidence["uiStatus"] == expected_ui_status, status


def test_reproduction_stages_confirmed_are_all_done():
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed", reproduction=_confirmed_repro())
    stages = build_reproduction_evidence(state)["stages"]
    assert [s["id"] for s in stages] == [
        "suite_executed", "tests_collected", "tests_run", "failure_observed", "evidence_captured",
    ]
    assert all(s["status"] == "done" for s in stages)
    assert "AssertionError" in stages[3]["detail"]


def test_reproduction_stages_unconfirmed_marks_failure_not_triggered():
    repro = _confirmed_repro(
        status="UNCONFIRMED", confidence=0.0, failing_test=None, exception_type=None,
        exception_message=None, failing_file=None, failing_line=None, traceback=None,
        stack_trace=None, evidence_source=None, tests_failed=0, tests_passed=12,
    )
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed", reproduction=repro)
    stages = build_reproduction_evidence(state)["stages"]
    by_id = {s["id"]: s for s in stages}
    assert by_id["suite_executed"]["status"] == "done"
    assert by_id["tests_collected"]["status"] == "done"
    assert by_id["tests_run"]["status"] == "done"
    assert by_id["failure_observed"]["status"] == "not_triggered"
    assert by_id["evidence_captured"]["status"] == "skipped"


def test_reproduction_stages_no_tests_skips_downstream():
    repro = _confirmed_repro(
        status="NO_TESTS", confidence=0.0, failing_test=None, exception_type=None,
        exception_message=None, failing_file=None, failing_line=None, traceback=None,
        stack_trace=None, evidence_source=None, exit_code=5,
        tests_collected=0, tests_passed=None, tests_failed=None,
        infra_detail="No tests collected by pytest",
    )
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed", reproduction=repro)
    stages = build_reproduction_evidence(state)["stages"]
    by_id = {s["id"]: s for s in stages}
    assert by_id["suite_executed"]["status"] == "done"
    assert by_id["tests_collected"]["status"] == "failed"
    assert by_id["tests_run"]["status"] == "skipped"
    assert by_id["failure_observed"]["status"] == "skipped"
    assert by_id["evidence_captured"]["status"] == "skipped"


def test_reproduction_stages_timeout_fails_at_the_first_stage():
    repro = _confirmed_repro(
        status="INFRA_ERROR", confidence=0.0, failing_test=None, exception_type=None,
        exception_message=None, failing_file=None, failing_line=None, traceback=None,
        stack_trace=None, evidence_source=None, exit_code=-1, timed_out=True,
        tests_collected=None, tests_passed=None, tests_failed=None,
        infra_detail="timeout",
    )
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed", reproduction=repro)
    evidence = build_reproduction_evidence(state)
    assert evidence["timedOut"] is True
    stages = evidence["stages"]
    by_id = {s["id"]: s for s in stages}
    assert by_id["suite_executed"]["status"] == "failed"
    assert "time limit" in by_id["suite_executed"]["detail"]
    assert by_id["tests_collected"]["status"] == "skipped"
    assert by_id["tests_run"]["status"] == "skipped"
    assert by_id["failure_observed"]["status"] == "skipped"
    assert by_id["evidence_captured"]["status"] == "skipped"


def test_reproduction_evidence_no_signature_without_an_exception():
    repro = _confirmed_repro(status="UNCONFIRMED", exception_type=None, exception_message=None)
    state = RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed", reproduction=repro)
    evidence = build_reproduction_evidence(state)
    assert evidence["errorSignature"] is None


def test_semantic_graph_defaults_an_unrecognised_role_to_internal_util_count():
    """A SIG payload with no role key at all (defensive, should not happen in
    practice) must not crash the projection or silently drop the file."""
    state = _sig_state(
        {
            "vulnapi/mystery.py": {
                "imports": [],
                "imported_by": [],
                "churn_weight": 0.0,
                "criticality": 0.4,
            },
        }
    )
    graph = build_semantic_graph(state)
    assert graph is not None
    assert graph["files"][0]["role"] == "internal-util"
    assert graph["roleCounts"]["internal-util"] == 1


def test_repair_summary_preserves_unknowns_and_full_diagnosis():
    state = RunStateModel(run_id="summary", repo_path="/repo", status="blocked",
        root_cause={"root_cause": "Detailed diagnosis. " * 30, "confidence": 1.0,
                    "diagnosis_status": "incomplete"},
        mutation_result={"target_test_passed": False, "mutation_status": "not_run"},
        recovery_attempts=[{"validation_status": "executed", "failure": "assert False"},
                           {"validation_status": "not_executed"}])
    result = build_executive_summary(state, [])
    rows = {r["label"]: r for r in result["verification"]}
    assert len(result["rootCause"]) > 80
    assert rows["Diagnosis"]["status"] == "incomplete"
    assert rows["Target tests"]["status"] == "failed"
    assert rows["Mutation testing"]["status"] == "not_run"
    assert rows["Security re-scan"]["status"] == "not_run"
    assert result["recordedAttempts"] == 2
    assert result["executedValidations"] == 1
    assert result["impactScopeCount"] is None
    assert result["changedFiles"] == []
    assert result["latestFailure"] == "assert False"


@pytest.mark.parametrize("mutation,expected", [
    ({"mutation_status": "scored"}, "incomplete"),
    ({"mutation_status": "scored", "killed_mutants": 15, "survived_mutants": 0}, "passed"),
    ({"mutation_status": "scored", "killed_mutants": 7, "survived_mutants": 6}, "failed"),
    ({"mutation_status": "unavailable"}, "unavailable"),
])
def test_repair_summary_mutation_states(mutation, expected):
    state = RunStateModel(run_id="mutation-summary", repo_path="/repo", mutation_result=mutation)
    rows = {r["label"]: r for r in build_executive_summary(state, [])["verification"]}
    assert rows["Mutation testing"]["status"] == expected
