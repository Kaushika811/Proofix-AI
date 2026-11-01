"""WebSocket delivery of lifecycle events (G5, integration).

The socket is how the workspace learns a run ended. These tests pin that the
authoritative lifecycle frame is delivered, that it is not duplicated by the
older state-poll fallback, and that a run predating lifecycle events still
reports as terminal.
"""

from unittest.mock import patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.state.events import AgentStatusEvent, RunLifecycleEvent
from backend.state.redis_store import RedisStore

RUN_ID = "5e6f7a8b-9c0d-1e2f-3a4b-5c6d7e8f9a0b"


@pytest.fixture
def app_client():
    # The socket runs inside the app's lifespan, which builds its own Redis
    # client. Patch the factory rather than assigning `app.state.redis`, or
    # lifespan startup replaces the fake and every run reads as "not found".
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    settings = Settings(stub_mode=True, redis_url="redis://localhost:6379/0")

    async def _fake_client(*_args, **_kwargs):
        return redis

    with patch("backend.main.create_redis_client", _fake_client):
        app = create_app()
        yield TestClient(app), RedisStore(redis, settings)


async def _seed(store: RedisStore, terminal: bool = True):
    state = await store.init_run(RUN_ID, "/tmp/clones/vulnapi")
    if terminal:
        state.status = "completed"
    await store.save_state(state)
    await store.append_event(
        AgentStatusEvent(run_id=RUN_ID, agent_id="A1", status="completed", message="SIG built")
    )


def _drain(websocket, limit: int = 10) -> list[dict]:
    """Read until the socket announces the run is over."""
    frames: list[dict] = []
    for _ in range(limit):
        frame = websocket.receive_json()
        frames.append(frame)
        if frame.get("type") in {"run.completed", "run.failed"}:
            break
    return frames


def test_socket_delivers_the_authoritative_lifecycle_frame(app_client):
    client, store = app_client

    with client:
        client.portal.call(_seed, store, True)
        client.portal.call(
            store.append_lifecycle_event,
            RunLifecycleEvent(
                type="run.completed", run_id=RUN_ID, decision="draft", decision_label="Draft PR"
            ),
        )

        with client.websocket_connect(f"/ws/runs/{RUN_ID}") as websocket:
            frames = _drain(websocket)

    agent_frames = [f for f in frames if "agent_id" in f]
    terminal = frames[-1]

    assert [f["agent_id"] for f in agent_frames] == ["A1"]
    assert terminal["type"] == "run.completed"
    # The real event carries the decision; the inferred fallback never could.
    assert terminal["decision"] == "draft"
    assert terminal["decision_label"] == "Draft PR"


def test_terminal_frame_is_not_duplicated_by_the_fallback(app_client):
    client, store = app_client

    with client:
        client.portal.call(_seed, store, True)
        client.portal.call(
            store.append_lifecycle_event,
            RunLifecycleEvent(type="run.completed", run_id=RUN_ID, decision="draft"),
        )

        with client.websocket_connect(f"/ws/runs/{RUN_ID}") as websocket:
            frames = _drain(websocket)

    terminals = [f for f in frames if f.get("type") in {"run.completed", "run.failed"}]
    assert len(terminals) == 1


def test_run_without_lifecycle_events_still_reports_terminal(app_client):
    """Runs that predate lifecycle events must still end cleanly.

    The state-poll fallback is kept for exactly this case; removing it would
    leave an old run's socket open forever.
    """
    client, store = app_client

    with client:
        client.portal.call(_seed, store, True)

        with client.websocket_connect(f"/ws/runs/{RUN_ID}") as websocket:
            frames = _drain(websocket)

    terminal = frames[-1]
    assert terminal["type"] == "run.completed"
    assert terminal["status"] == "completed"
    # The fallback is an inference, so it carries no decision — and says so by
    # omission rather than by inventing one.
    assert "decision" not in terminal


def test_failed_run_announces_failure_with_a_reason(app_client):
    client, store = app_client

    with client:
        client.portal.call(_seed, store, True)
        client.portal.call(
            store.append_lifecycle_event,
            RunLifecycleEvent(type="run.failed", run_id=RUN_ID, reason="RuntimeError: boom"),
        )

        with client.websocket_connect(f"/ws/runs/{RUN_ID}") as websocket:
            frames = _drain(websocket)

    terminal = frames[-1]
    assert terminal["type"] == "run.failed"
    assert terminal["reason"] == "RuntimeError: boom"
