"""A0.5's product card — Phase 2.

A0.5 indexes the repository into a cross-run knowledge graph and repair memory,
and until now published only to the `v2` API surface: real work, paid for on
every run, that the workspace could not show. These tests pin what the card says
and, more importantly, what it must not say when the layer did not run.

A0.5 is projected differently from every other agent, and the tests follow that:
it deliberately never mutates `RunStateModel` — its index lives in the cross-run
cache — so its numbers exist only in its own emitted event payload.
"""

from datetime import datetime, timedelta

import pytest

from backend.services.ui_projection import (
    SURFACE_V1,
    build_agent_entries,
)
from backend.state.events import AgentStatusEvent
from backend.state.schema import RunStateModel

RUN_ID = "e8fac24c-13e5-415f-add4-a64b759e414a"

FULL_REBUILD = {
    "repository_id": "repo-abc123",
    "repository_nodes": 412,
    "repository_edges": 903,
    "call_graph_nodes": 288,
    "git_commits_indexed": 120,
    "repair_memory_entries": 0,
    "documentation_entries": 44,
    "cache_hits": 0,
    "incremental_updates": 0,
    "graph_build_ms": 300,
    "call_graph_ms": 120,
    "ownership_ms": 0,
    "history_ms": 80,
    "documentation_ms": 0,
    "total_ms": 500,
}


def _event(agent_id: str, status: str, message: str = "", offset: int = 0, payload=None):
    return AgentStatusEvent(
        run_id=RUN_ID,
        agent_id=agent_id,
        status=status,  # type: ignore[arg-type]
        timestamp=datetime(2026, 8, 9, 12, 0, 0) + timedelta(seconds=offset),
        message=message,
        payload=payload,
        sequence=offset,
    )


def _state(**overrides) -> RunStateModel:
    return RunStateModel(run_id=RUN_ID, repo_path="vulnapi", status="completed", **overrides)


def _events(published: dict | None, *, graph: dict | None = None) -> list[AgentStatusEvent]:
    payload: dict = {}
    if published is not None:
        payload["repository_intelligence"] = published
    if graph is not None:
        payload["knowledge_graph"] = graph
    return [
        _event("A0.5", "started", "Loading repository intelligence", 0),
        _event("A0.5", "completed", "Repository index full rebuild: 412 nodes", 2, payload or None),
    ]


def _card(state: RunStateModel, events: list[AgentStatusEvent]) -> dict:
    entries = build_agent_entries(state, events, surface=SURFACE_V1)
    return next(e for e in entries if e["id"] == "intelligence")


class TestCardIsPublished:
    def test_the_product_surface_carries_the_card(self):
        entries = build_agent_entries(_state(), _events(FULL_REBUILD), surface=SURFACE_V1)
        cards = [e["id"] for e in entries]

        assert "intelligence" in cards
        # Ordering is the registry's, and A0.5 runs before A1.
        assert cards.index("intelligence") < cards.index("repo-intel")

    def test_card_carries_its_registry_identity(self):
        card = _card(_state(), _events(FULL_REBUILD))

        assert card["agentId"] == "A0.5"
        assert card["stage"] == "repository"
        assert card["handoff"] == "Repository Index"


class TestVisualization:
    def test_full_rebuild_reports_its_phases(self):
        viz = _card(_state(), _events(FULL_REBUILD))["visualization"]

        assert viz["kind"] == "intelligence"
        assert viz["data"]["mode"] == "full rebuild"
        # Only phases that took measurable time. A zero-millisecond segment is
        # not a fast phase, it is one this index did not need.
        assert [p["label"] for p in viz["data"]["phases"]] == ["Graph", "Call graph", "History"]
        assert viz["data"]["totalMs"] == 500
        assert viz["data"]["metrics"]["nodes"] == 412
        assert viz["data"]["metrics"]["callables"] == 288

    def test_cache_hit_outranks_everything_else(self):
        """Precedence mirrors the agent's own summary: cache > incremental > full."""
        published = {**FULL_REBUILD, "cache_hits": 1, "incremental_updates": 1}
        viz = _card(_state(), _events(published))["visualization"]

        assert viz["data"]["mode"] == "cache hit"
        assert "reused" in viz["data"]["modeDetail"]

    def test_incremental_names_what_changed(self):
        published = {
            **FULL_REBUILD,
            "incremental_updates": 1,
            "files_added": 3,
            "files_modified": 1,
            "files_deleted": 0,
            "files_renamed": 2,
        }
        viz = _card(_state(), _events(published))["visualization"]

        assert viz["data"]["mode"] == "incremental"
        detail = viz["data"]["modeDetail"]
        assert "3 added" in detail and "1 modified" in detail and "2 renamed" in detail

    def test_capabilities_come_from_the_knowledge_graph(self):
        viz = _card(
            _state(),
            _events(FULL_REBUILD, graph={"capabilities": [{"name": "auth"}, {"name": "billing"}]}),
        )["visualization"]

        assert viz["data"]["capabilities"] == ["auth", "billing"]

    def test_no_payload_means_no_visualization(self):
        """A0.5 that emitted nothing has no numbers, so it draws nothing."""
        card = _card(_state(), _events(None))

        assert "visualization" not in card


class TestAbsenceIsNotZero:
    def test_disabled_layer_renders_absence_not_a_zeroed_index(self):
        """`repository_intelligence_enabled=False` emits no events at all.

        The card must not claim an index of nought nodes — that is a measured
        statement about a repository nobody indexed.
        """
        card = _card(_state(), [])

        assert card["status"] == "skipped"
        assert "visualization" not in card
        # The only metric is how long the stage took, which is nothing.
        assert [m["label"] for m in card["metrics"]] == ["Duration"]
        assert card["evidence"]["subtitle"] == "Not yet published by this run."

    def test_a_swallowed_failure_is_reported_as_failed_not_running(self):
        """A0.5's failure is caught so the pipeline continues (`nodes.py`).

        It therefore emits `started` and never a terminal event. The card sat on
        "running" forever on a run that had finished.
        """
        state = _state(errors=[{"agent": "A0.5", "error": "index build exploded"}])
        card = _card(state, [_event("A0.5", "started", "Loading repository intelligence", 0)])

        assert card["status"] == "failed"

    def test_an_unfinished_agent_with_no_error_is_skipped(self):
        card = _card(_state(), [_event("A0.5", "started", "Loading repository intelligence", 0)])

        assert card["status"] == "skipped"

    def test_a_live_run_still_reports_running(self):
        state = _state()
        state.status = "running"
        card = _card(state, [_event("A0.5", "started", "Loading repository intelligence", 0)])

        assert card["status"] == "running"


class TestNarrative:
    def test_lines_say_whether_work_was_reused(self):
        lines = _card(_state(), _events({**FULL_REBUILD, "cache_hits": 1}))["lines"]

        assert any("reused from a previous run" in line for line in lines)

    def test_lines_report_remembered_repairs_honestly(self):
        none_remembered = _card(_state(), _events(FULL_REBUILD))["lines"]
        assert any("No previous repairs remembered" in line for line in none_remembered)

        some_remembered = _card(_state(), _events({**FULL_REBUILD, "repair_memory_entries": 4}))[
            "lines"
        ]
        assert any("4 repair(s) remembered" in line for line in some_remembered)

    def test_the_agents_own_message_is_kept(self):
        lines = _card(_state(), _events(FULL_REBUILD))["lines"]
        assert "Repository index full rebuild: 412 nodes" in lines


class TestHeaderIdentity:
    """`repositoryId` / `headSha` / `repositoryHash` were published, never shown."""

    def test_identity_is_published_from_the_index_pointer(self):
        from backend.services.ui_projection import build_workspace_header

        header = build_workspace_header(
            _state(),
            [],
            {"repository_id": "repo-abc123", "head_sha": "9c2d1f4", "repository_hash": "h-77"},
        )

        assert header["repositoryId"] == "repo-abc123"
        assert header["headSha"] == "9c2d1f4"
        assert header["repositoryHash"] == "h-77"

    def test_a_commit_the_run_never_observed_is_null_not_guessed(self):
        from backend.services.ui_projection import build_workspace_header

        header = build_workspace_header(_state(), [], None)

        assert header["headSha"] is None
        assert header["repositoryHash"] is None
        # The id is always derivable from the repository path, so it survives
        # the index layer being switched off.
        assert header["repositoryId"]


@pytest.mark.parametrize(
    "published,expected",
    [
        ({"cache_hits": 2}, "cache hit"),
        ({"incremental_updates": 1}, "incremental"),
        ({}, "full rebuild"),
    ],
)
def test_every_index_mode_is_reachable(published, expected):
    from backend.services.ui_projection import _index_mode

    mode, detail = _index_mode(published)
    assert mode == expected
    assert detail  # never an empty sentence
