"""Tests for the agent/stage registry — the single source of truth (G1).

The registry defines agent identity, ordering and stage membership for every
surface. Nothing downstream may hardcode that mapping, so these tests guard the
properties clients are entitled to rely on.
"""

import pytest

from backend.services.ui_projection import (
    AGENT_BY_BACKEND_ID,
    AGENT_REGISTRY,
    BACKEND_BY_CARD_ID,
    CARD_BY_BACKEND_ID,
    STAGE_BY_ID,
    STAGE_REGISTRY,
    SURFACE_V1,
    SURFACE_V2,
    agents_for_surface,
    build_stage_registry,
)

# The pipeline as `orchestrator/graph.py` actually executes it. A0.7 runs at
# `environment_precheck`, A0.5 at `index_repository` and A5.5 at
# `engineer_context`; all three emitted status events long before the registry
# acknowledged them, which is what kept them invisible.
EXPECTED_AGENT_IDS = [
    "A0.7", "A0.5", "A1", "A2", "A3", "A3.5", "A4", "A5", "A5.5", "A6", "A7", "A8", "A9", "A10",
]


def test_registry_covers_every_pipeline_agent():
    assert [a.agent_id for a in AGENT_REGISTRY] == EXPECTED_AGENT_IDS


@pytest.mark.parametrize("agent_id", ["A0.7", "A0.5", "A5.5"])
def test_new_agents_are_registered_with_full_display_information(agent_id):
    definition = AGENT_BY_BACKEND_ID[agent_id]
    assert definition.card
    assert definition.name
    assert definition.purpose.endswith(".")
    assert definition.handoff
    assert definition.stage in STAGE_BY_ID


def test_every_agent_belongs_to_a_declared_stage():
    for definition in AGENT_REGISTRY:
        assert definition.stage in STAGE_BY_ID, definition.agent_id


def test_card_and_agent_ids_are_unique():
    cards = [a.card for a in AGENT_REGISTRY]
    agent_ids = [a.agent_id for a in AGENT_REGISTRY]
    assert len(set(cards)) == len(cards)
    assert len(set(agent_ids)) == len(agent_ids)


def test_lookup_tables_agree_with_the_registry():
    assert CARD_BY_BACKEND_ID == {a.agent_id: a.card for a in AGENT_REGISTRY}
    assert BACKEND_BY_CARD_ID == {a.card: a.agent_id for a in AGENT_REGISTRY}


def test_registry_entries_remain_positionally_unpackable():
    # Existing consumers unpack the first five fields positionally. Adding stage
    # and surface must not break them.
    card, agent_id, name, purpose, handoff = AGENT_REGISTRY[2][:5]
    assert (card, agent_id) == ("repo-intel", "A1")
    assert name and purpose and handoff


def test_the_product_surface_publishes_the_whole_pipeline():
    """The v1/v2 split is closed.

    It existed because the product surface had no cards for A0.5 and A5.5, so
    publishing them would have added cards that never rendered. A0.5 gained one
    in Phase 2 and A5.5 in Phase 4. Every agent the graph executes is now on the
    surface the workspace reads.
    """
    assert [a.agent_id for a in agents_for_surface(SURFACE_V1)] == EXPECTED_AGENT_IDS


def test_the_two_surfaces_stay_identical():
    """The guard against the split quietly reopening.

    `surface` is still accepted — it is in the published schema and a client may
    send it — but it must not change the response. A new registry entry
    published to only one surface would break this, which is the point.
    """
    assert agents_for_surface(SURFACE_V1) == agents_for_surface(SURFACE_V2)


def test_v2_surface_publishes_the_whole_pipeline():
    assert [a.agent_id for a in agents_for_surface(SURFACE_V2)] == EXPECTED_AGENT_IDS


def test_surfaces_preserve_pipeline_order():
    for surface in (SURFACE_V1, SURFACE_V2):
        published = agents_for_surface(surface)
        positions = [AGENT_REGISTRY.index(a) for a in published]
        assert positions == sorted(positions)


def test_stage_registry_is_ordered_and_complete():
    orders = [s.order for s in STAGE_REGISTRY]
    assert orders == sorted(orders)
    assert orders == list(range(1, len(STAGE_REGISTRY) + 1))
    assert [s.id for s in STAGE_REGISTRY] == [
        "repository",
        "investigation",
        "context",
        "planning",
        "patch",
        "validation",
        "learning",
    ]


def test_build_stage_registry_groups_agents_under_their_stage():
    stages = {s["id"]: s for s in build_stage_registry(SURFACE_V2)}

    assert [a["agentId"] for a in stages["repository"]["agents"]] == ["A0.7", "A0.5", "A1", "A2", "A3"]
    assert [a["agentId"] for a in stages["investigation"]["agents"]] == ["A3.5", "A4", "A5"]
    assert [a["agentId"] for a in stages["context"]["agents"]] == ["A5.5"]
    assert [a["agentId"] for a in stages["validation"]["agents"]] == ["A8", "A9", "A10"]


def test_learning_stage_is_published_with_no_agents():
    # Learning runs outside the agent graph. An empty list is a fact about the
    # stage; omitting the stage would misrepresent the workflow.
    stages = {s["id"]: s for s in build_stage_registry(SURFACE_V2)}
    assert stages["learning"]["agents"] == []
    assert stages["learning"]["label"] == "Learning"


def test_build_stage_registry_carries_display_information():
    for stage in build_stage_registry(SURFACE_V2):
        assert stage["label"]
        assert stage["purpose"]
        assert isinstance(stage["order"], int)
        for agent in stage["agents"]:
            assert agent["id"] and agent["agentId"] and agent["name"]
            assert agent["purpose"] and agent["handoff"]
