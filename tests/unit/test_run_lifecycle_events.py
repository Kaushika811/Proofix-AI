"""Tests for explicit run lifecycle events (G5).

Before these existed a client could only infer that a run had ended: the last
agent stopped emitting, which is indistinguishable from a dropped socket, and
inferring completion from A10 never fired for a run that failed earlier. These
tests pin the guarantees the workspace now depends on — emitted once, carrying
the decision or the reason, and never able to fail the run they describe.
"""

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.state.events import RunLifecycleEvent
from backend.state.redis_store import RedisStore

RUN_ID = "3f2b1c44-0d61-4a2e-9f10-2c5a7b8e1d33"


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisStore(client, Settings(redis_url="redis://localhost:6379/0"))
    await client.aclose()


async def test_lifecycle_event_is_persisted_and_readable(store):
    recorded = await store.append_lifecycle_event(
        RunLifecycleEvent(type="run.started", run_id=RUN_ID)
    )
    assert recorded is True

    events = await store.get_lifecycle_events(RUN_ID)
    assert [e.type for e in events] == ["run.started"]
    assert events[0].run_id == RUN_ID
    assert events[0].timestamp is not None


async def test_same_lifecycle_kind_is_recorded_exactly_once(store):
    first = await store.append_lifecycle_event(
        RunLifecycleEvent(type="run.completed", run_id=RUN_ID, decision="draft")
    )
    second = await store.append_lifecycle_event(
        RunLifecycleEvent(type="run.completed", run_id=RUN_ID, decision="auto_mergeable")
    )

    assert (first, second) == (True, False)
    events = await store.get_lifecycle_events(RUN_ID)
    assert len(events) == 1
    # The first writer wins; a late duplicate cannot rewrite the decision.
    assert events[0].decision == "draft"


async def test_start_and_end_are_separate_records(store):
    await store.append_lifecycle_event(RunLifecycleEvent(type="run.started", run_id=RUN_ID))
    await store.append_lifecycle_event(
        RunLifecycleEvent(type="run.completed", run_id=RUN_ID, decision="diff_only")
    )

    events = await store.get_lifecycle_events(RUN_ID)
    assert [e.type for e in events] == ["run.started", "run.completed"]


async def test_lifecycle_events_are_scoped_per_run(store):
    other = "9a1d7e55-1111-2222-3333-444455556666"
    await store.append_lifecycle_event(RunLifecycleEvent(type="run.started", run_id=RUN_ID))
    await store.append_lifecycle_event(RunLifecycleEvent(type="run.started", run_id=other))

    assert len(await store.get_lifecycle_events(RUN_ID)) == 1
    assert len(await store.get_lifecycle_events(other)) == 1


async def test_failed_event_carries_a_reason(store):
    await store.append_lifecycle_event(
        RunLifecycleEvent(type="run.failed", run_id=RUN_ID, reason="ValueError: repo missing")
    )
    event = (await store.get_lifecycle_events(RUN_ID))[0]
    assert event.type == "run.failed"
    assert event.reason == "ValueError: repo missing"
    assert event.decision is None


async def test_lifecycle_event_is_published_for_live_delivery(store):
    pubsub = store.client.pubsub()
    await pubsub.subscribe(f"bugfix:{RUN_ID}:live")
    # Drain the subscribe confirmation.
    await pubsub.get_message(timeout=1.0)

    await store.append_lifecycle_event(
        RunLifecycleEvent(type="run.completed", run_id=RUN_ID, decision="draft")
    )

    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    assert message is not None
    payload = message["data"]
    if isinstance(payload, bytes):
        payload = payload.decode()
    delivered = RunLifecycleEvent.model_validate_json(payload)
    assert delivered.type == "run.completed"
    assert delivered.decision == "draft"
    await pubsub.unsubscribe()
    await pubsub.close()


async def test_lifecycle_events_do_not_pollute_the_agent_timeline(store):
    # `get_events` backs `/runs/{id}/events`, which V1 replays. A lifecycle
    # frame appearing there would break parsing for every existing client.
    await store.append_lifecycle_event(RunLifecycleEvent(type="run.started", run_id=RUN_ID))
    assert await store.get_events(RUN_ID) == []


@pytest.mark.parametrize("event_type", ["run.started", "run.completed", "run.failed"])
def test_lifecycle_types_are_the_three_the_workspace_expects(event_type):
    event = RunLifecycleEvent(type=event_type, run_id=RUN_ID)
    assert event.type == event_type


def test_unknown_lifecycle_type_is_rejected():
    with pytest.raises(ValueError):
        RunLifecycleEvent(type="run.paused", run_id=RUN_ID)
