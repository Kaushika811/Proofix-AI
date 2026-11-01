"""Stage status correctness (R4).

Two defects made the stage rail describe a finished run as still working:
`retrying` survived past the end of a run, and the agent-less `learning` stage
claimed completion from run terminality alone rather than from evidence that
learning ran.
"""

import pytest

from backend.services.ui_projection import (
    _learning_observed,
    _stage_status,
    build_stage_progress,
)
from backend.state.events import AgentStatusEvent
from backend.state.schema import RunStateModel

RUN_ID = "a1b2c3d4-0000-0000-0000-000000000001"


def _state(status: str = "completed") -> RunStateModel:
    return RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status=status)


def _event(agent_id: str, status: str, payload: dict | None = None) -> AgentStatusEvent:
    return AgentStatusEvent(
        run_id=RUN_ID, agent_id=agent_id, status=status, payload=payload  # type: ignore[arg-type]
    )


# -- in-flight states cannot outlive the run -------------------------------


@pytest.mark.parametrize("run_status", ["completed", "failed"])
def test_terminal_run_never_reports_retrying(run_status):
    assert _stage_status(["retry", "completed"], _state(run_status)) != "retrying"
    assert _stage_status(["retry", "completed"], _state(run_status)) == "completed"


@pytest.mark.parametrize("run_status", ["completed", "failed"])
def test_terminal_run_never_reports_running(run_status):
    assert _stage_status(["running", "completed"], _state(run_status)) != "running"


@pytest.mark.parametrize("run_status", ["pending", "running", "validation_retry"])
def test_live_run_still_reports_in_flight_states(run_status):
    assert _stage_status(["retry", "completed"], _state(run_status)) == "retrying"
    assert _stage_status(["running", "completed"], _state(run_status)) == "running"


def test_failure_outranks_everything_in_every_run_state():
    for run_status in ("running", "completed", "failed"):
        assert _stage_status(["failed", "retry", "running"], _state(run_status)) == "failed"


def test_all_skipped_stays_skipped():
    assert _stage_status(["skipped", "skipped"], _state("completed")) == "skipped"


def test_mixed_skipped_and_completed_is_completed():
    assert _stage_status(["skipped", "completed"], _state("completed")) == "completed"


# -- agent-less stages require evidence ------------------------------------


def test_agentless_stage_without_evidence_is_skipped_on_a_terminal_run():
    # Previously this claimed `completed`, asserting work that never happened.
    assert _stage_status([], _state("completed"), executed=False) == "skipped"


def test_agentless_stage_with_evidence_is_completed():
    assert _stage_status([], _state("completed"), executed=True) == "completed"


def test_agentless_stage_on_a_live_run_is_waiting():
    assert _stage_status([], _state("running"), executed=False) == "waiting"


def test_agentless_stage_defaults_to_no_evidence():
    # `executed=None` means nothing observed it — never an assumed completion.
    assert _stage_status([], _state("completed")) == "skipped"


# -- the learning evidence signal ------------------------------------------


def test_learning_observed_reads_the_a05_payload():
    events = [_event("A0.5", "completed", {"learning": {"framework": "fastapi"}})]
    assert _learning_observed(events) is True


def test_learning_not_observed_when_the_section_is_absent():
    events = [_event("A0.5", "completed", {"repository_intelligence": {"repository_nodes": 4}})]
    assert _learning_observed(events) is False


def test_learning_not_observed_without_payloads():
    assert _learning_observed([_event("A1", "completed")]) is False


def test_learning_section_must_be_a_mapping():
    assert _learning_observed([_event("A0.5", "completed", {"learning": None})]) is False


# -- end to end through the projection -------------------------------------


def test_learning_stage_reports_skipped_when_learning_did_not_run():
    events = [_event("A1", "completed"), _event("A10", "completed")]
    stages = {s["id"]: s for s in build_stage_progress(_state(), events)}
    assert stages["learning"]["status"] == "skipped"


def test_learning_stage_reports_completed_when_learning_ran():
    events = [
        _event("A0.5", "completed", {"learning": {"framework": "fastapi"}}),
        _event("A10", "completed"),
    ]
    stages = {s["id"]: s for s in build_stage_progress(_state(), events)}
    assert stages["learning"]["status"] == "completed"


def test_finished_run_shows_no_stage_in_flight():
    events = [
        _event("A8", "retry"),
        _event("A10", "completed"),
    ]
    stages = build_stage_progress(_state("completed"), events)
    assert all(s["status"] not in {"running", "retrying"} for s in stages), [
        (s["id"], s["status"]) for s in stages
    ]


def test_stage_shape_is_unchanged():
    stages = build_stage_progress(_state(), [_event("A1", "completed")])
    for stage in stages:
        assert set(stage) == {"id", "label", "order", "purpose", "status", "agents"}
