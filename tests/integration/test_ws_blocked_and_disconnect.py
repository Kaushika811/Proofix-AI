"""WebSocket handling of blocked runs and of client disconnects (integration).

Two separate defects are pinned here.

**Blocked runs never ended.** `_TERMINAL_STATUSES` and `_TERMINAL_LIFECYCLE`
listed only `completed` and `failed`, so a run stopped by the environment
precheck kept its socket open, pinging, while the client kept rendering it as
executing. `blocked` is a terminal outcome and must end the socket like the
other two — without being relabelled as either of them.

**Normal browser disconnects logged tracebacks.** Writing to a socket the
browser already closed (a reload, a navigation, an HMR round) surfaces as a
broken pipe — `write EPIPE`. That is ordinary, not a pipeline failure, and the
traceback it produced buried real errors. It is now handled quietly. Anything
unexpected still propagates, which the last test pins: a handler that swallowed
everything would be worse than the noise it replaced.
"""

from unittest.mock import patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket

from backend.api.routes.ws import _is_expected_disconnect
from backend.config import Settings
from backend.main import create_app
from backend.state.events import AgentStatusEvent, RunLifecycleEvent
from backend.state.redis_store import RedisStore

RUN_ID = "7a8b9c0d-1e2f-3a4b-5c6d-7e8f9a0b1c2d"

_TERMINAL_TYPES = {"run.completed", "run.failed", "run.blocked"}


@pytest.fixture
def app_client():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    settings = Settings(stub_mode=True, redis_url="redis://localhost:6379/0")

    async def _fake_client(*_args, **_kwargs):
        return redis

    with patch("backend.main.create_redis_client", _fake_client):
        app = create_app()
        yield TestClient(app), RedisStore(redis, settings)


async def _seed_blocked(store: RedisStore):
    """A run exactly as the precheck leaves it: A0.7 emitted, nothing after."""
    state = await store.init_run(RUN_ID, "/tmp/clones/quant_med")
    state.status = "blocked"
    state.environment = {
        "status": "blocked",
        "language": "python",
        "blocking": True,
        "reason": "No dependency manifest found in the repository.",
    }
    await store.save_state(state)
    await store.append_event(
        AgentStatusEvent(
            run_id=RUN_ID,
            agent_id="A0.7",
            status="failed",
            message="No dependency manifest found in the repository.",
        )
    )


def _drain(websocket, limit: int = 10) -> list[dict]:
    frames: list[dict] = []
    for _ in range(limit):
        frame = websocket.receive_json()
        frames.append(frame)
        if frame.get("type") in _TERMINAL_TYPES:
            break
    return frames


def test_blocked_run_announces_run_blocked_with_the_backend_reason(app_client):
    client, store = app_client

    with client:
        client.portal.call(_seed_blocked, store)
        client.portal.call(
            store.append_lifecycle_event,
            RunLifecycleEvent(
                type="run.blocked",
                run_id=RUN_ID,
                decision_label="Environment not prepared",
                reason="No dependency manifest found in the repository.",
            ),
        )

        with client.websocket_connect(f"/ws/runs/{RUN_ID}") as websocket:
            frames = _drain(websocket)

    terminal = frames[-1]
    assert terminal["type"] == "run.blocked"
    assert terminal["decision_label"] == "Environment not prepared"
    assert terminal["reason"] == "No dependency manifest found in the repository."
    # A blocked run carries no routing outcome, and must not borrow one.
    assert terminal["decision"] is None

    # The stage the run actually stopped at is on the timeline.
    agent_frames = [f for f in frames if "agent_id" in f]
    assert [f["agent_id"] for f in agent_frames] == ["A0.7"]


def test_blocked_run_without_a_lifecycle_event_still_ends_the_socket(app_client):
    """The state-poll fallback must recognise `blocked` as terminal.

    The fallback frame keeps the name `run.completed` for backwards
    compatibility with clients that identify it by that name plus the absence
    of `sequence`; which ending it was is stated on `status`, and a client must
    read that rather than assume the name means success.
    """
    client, store = app_client

    with client:
        client.portal.call(_seed_blocked, store)

        with client.websocket_connect(f"/ws/runs/{RUN_ID}") as websocket:
            frames = _drain(websocket)

    terminal = frames[-1]
    assert terminal["status"] == "blocked"
    assert "sequence" not in terminal


def test_client_disconnect_during_send_is_handled_quietly(app_client):
    """A browser that goes away mid-write must not raise out of the endpoint."""
    client, store = app_client

    calls = {"n": 0}
    real_send = WebSocket.send_json

    async def flaky_send(self, data, mode="text"):
        calls["n"] += 1
        if calls["n"] == 1:
            # What the OS reports when the peer has already closed: `write EPIPE`.
            raise BrokenPipeError(32, "Broken pipe")
        return await real_send(self, data, mode)

    with client:
        client.portal.call(_seed_blocked, store)

        with patch.object(WebSocket, "send_json", flaky_send):
            # No exception escapes: the endpoint returns and the socket closes.
            with client.websocket_connect(f"/ws/runs/{RUN_ID}"):
                pass

    assert calls["n"] >= 1


def test_unexpected_websocket_errors_are_not_swallowed(app_client):
    """The quiet path is for disconnects only, never for real bugs."""
    client, store = app_client

    class Unexpected(Exception):
        pass

    async def broken_send(self, data, mode="text"):
        raise Unexpected("serialization exploded")

    with client:
        client.portal.call(_seed_blocked, store)

        with patch.object(WebSocket, "send_json", broken_send):
            with pytest.raises(Unexpected):
                with client.websocket_connect(f"/ws/runs/{RUN_ID}"):
                    pass


@pytest.mark.parametrize(
    "exc",
    [
        BrokenPipeError(32, "Broken pipe"),
        ConnectionResetError("peer reset"),
        ConnectionAbortedError("aborted"),
        # The exact message `starlette.websockets.WebSocket.send` raises once
        # the close handshake is done. This used to be unmatched (the test
        # asserted an invented wording with a "websocket." prefix that the
        # installed library never produces), so a normal browser reload mid-send
        # escaped as an unhandled 500 traceback instead of being recognised here.
        RuntimeError('Cannot call "send" once a close message has been sent.'),
        RuntimeError('Cannot call "receive" once a disconnect message has been received.'),
    ],
)
def test_expected_disconnects_are_recognised(exc):
    assert _is_expected_disconnect(exc)


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("event loop is closed"),
        ValueError("not json serializable"),
        KeyError("payload"),
    ],
)
def test_unexpected_errors_are_not_treated_as_disconnects(exc):
    assert not _is_expected_disconnect(exc)
