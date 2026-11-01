import asyncio
import json
import logging
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from backend.state.events import AgentStatusEvent, RunLifecycleEvent
from backend.state.redis_store import RedisStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# Frames can still repeat without the second delivery path that originally
# required this: the socket replays history on connect and again on reconnect,
# and the idle poll re-reads lifecycle events it may already have sent. Track
# recently sent frames so the client sees each exactly once. Bounded so a long
# run cannot grow it forever.
_DEDUPE_WINDOW = 512

# Replay the same depth the REST timeline returns, so a client that reads
# `GET /api/runs/{id}/events` and one that attaches here see the same history.
_HISTORY_COUNT = 500

# A run reaching one of these is over; nothing further will ever be emitted.
# `blocked` belongs here: the environment precheck stopped the pipeline, and
# omitting it left the socket pinging a finished run forever while the client
# kept rendering it as still executing.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "blocked"})

# Lifecycle events that end a run.
_TERMINAL_LIFECYCLE = frozenset({"run.completed", "run.failed", "run.blocked"})

class _ClientGone(Exception):
    """The peer closed the socket. Expected; not a pipeline failure."""


# Writing to a socket the browser already closed is ordinary: a reload, a
# navigation and a dev-server HMR round all do it. Starlette surfaces it as a
# `WebSocketDisconnect`, as a `RuntimeError` once the close frame has been sent,
# or as an OS-level broken pipe / connection reset (`write EPIPE`). None of
# those is an error in this pipeline, and dumping a traceback for each one
# buried real failures. Anything *else* still propagates.
_EXPECTED_DISCONNECT_ERRORS = (
    WebSocketDisconnect,
    ConnectionResetError,
    BrokenPipeError,
    ConnectionAbortedError,
)


def _is_expected_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, _EXPECTED_DISCONNECT_ERRORS):
        return True
    # Starlette raises a bare RuntimeError once the close handshake is done.
    # Matched on its message rather than its type so an unrelated RuntimeError
    # is still reported. The exact wording
    # (`starlette/websockets.py::WebSocket.send`) is
    # 'Cannot call "send" once a close message has been sent.' — this used to
    # be unmatched, so a normal browser reload mid-send surfaced as an
    # unhandled 500 traceback instead of the ordinary disconnect it is.
    return isinstance(exc, RuntimeError) and (
        'websocket.close' in str(exc)
        or 'response already completed' in str(exc)
        or 'close message has been sent' in str(exc)
        or 'disconnect message has been received' in str(exc)
    )

# How long the event queue must be idle before we check whether the run has
# finished. Agents emit nothing between stages, so this doubles as the latency
# between the last agent event and the client learning the run is over.
_IDLE_POLL_SECONDS = 2.0

# Keep-alive cadence for a run that is still in flight but quiet.
_PING_SECONDS = 30.0


class _SentFrames:
    """Bounded set of frames already delivered on this connection."""

    def __init__(self, limit: int = _DEDUPE_WINDOW) -> None:
        self._seen: set[str] = set()
        self._order: list[str] = []
        self._limit = limit

    def is_new(self, frame: str) -> bool:
        if frame in self._seen:
            return False
        self._seen.add(frame)
        self._order.append(frame)
        if len(self._order) > self._limit:
            evicted = self._order.pop(0)
            self._seen.discard(evicted)
        return True


@router.websocket("/ws/runs/{run_id}")
async def ws_run_timeline(websocket: WebSocket, run_id: str) -> None:
    store = RedisStore(websocket.app.state.redis)
    state = await store.load_state(run_id)
    if not state:
        await websocket.close(code=4004, reason="Run not found")
        return

    await websocket.accept()
    sent = _SentFrames()

    # Set once a real lifecycle terminal frame has been delivered, so the
    # state-poll fallback below does not announce the same ending twice.
    terminal_announced = False

    async def send_json(payload: dict) -> None:
        """Write one frame, translating an expected disconnect into `_ClientGone`."""
        try:
            await websocket.send_json(payload)
        except Exception as exc:  # noqa: BLE001 — re-raised unless expected
            if _is_expected_disconnect(exc):
                logger.debug(
                    "ws_client_disconnected",
                    extra={"ws": {"run_id": run_id, "error": str(exc)}},
                )
                raise _ClientGone from exc
            raise

    async def send_event(event: AgentStatusEvent) -> None:
        frame = event.model_dump_json()
        if sent.is_new(frame):
            await send_json(event.model_dump(mode="json"))

    async def send_lifecycle(event: RunLifecycleEvent) -> None:
        nonlocal terminal_announced
        frame = event.model_dump_json()
        if sent.is_new(frame):
            await send_json(event.model_dump(mode="json"))
        if event.type in _TERMINAL_LIFECYCLE:
            terminal_announced = True

    try:
        history = await store.get_events(run_id, count=_HISTORY_COUNT)
        for event in history:
            await send_event(event)

        # Lifecycle frames replay after the agent timeline so a client attaching
        # to a finished run sees the ending last, in the order it happened.
        for lifecycle in await store.get_lifecycle_events(run_id):
            await send_lifecycle(lifecycle)
    except _ClientGone:
        return

    # Redis pub/sub is the only delivery path (B-B13). An in-memory
    # `WSBroadcaster` used to run alongside it, reaching same-process clients a
    # second time — invisible thanks to `_SentFrames`, and useless to a client
    # attached to any other replica, which was the single-process assumption
    # that stopped this from scaling.
    #
    # The main loop below no longer has a queue to block on, so the listener
    # signals activity instead: the loop treats a period with no frames as idle,
    # which is what licenses it to check whether the run has ended. Without this
    # the terminal frame could overtake an event still in flight.
    activity = asyncio.Event()

    redis_client: aioredis.Redis = websocket.app.state.redis
    pubsub = redis_client.pubsub()
    channel = f"bugfix:{run_id}:live"
    await pubsub.subscribe(channel)

    async def redis_listener() -> None:
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    # Agent and lifecycle events share this channel. They are
                    # told apart by shape: only a lifecycle frame carries
                    # `type`. An unparseable frame is dropped rather than
                    # allowed to kill the listener and silence the run.
                    try:
                        decoded = json.loads(data)
                    except ValueError:
                        continue
                    try:
                        if isinstance(decoded, dict) and "type" in decoded:
                            await send_lifecycle(RunLifecycleEvent.model_validate(decoded))
                        else:
                            await send_event(AgentStatusEvent.model_validate(decoded))
                    except ValidationError:
                        continue
                    activity.set()
        except asyncio.CancelledError:
            pass
        except _ClientGone:
            # The main loop notices the same disconnect and closes down; this
            # task simply stops rather than surfacing as an unhandled task
            # exception with a traceback for a normal browser reload.
            pass

    listener_task = asyncio.create_task(redis_listener())

    # The pipeline has no "run finished" event of its own — the last agent
    # simply stops emitting. Without an explicit terminal frame the client can
    # only infer completion from the socket closing, which is indistinguishable
    # from a dropped connection. So once the queue goes idle we re-read the run
    # state and, if it settled, say so outright and hang up.
    last_ping = time.monotonic()
    try:
        while True:
            try:
                await asyncio.wait_for(activity.wait(), timeout=_IDLE_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            else:
                # Frames arrived while we waited; the listener has already sent
                # them. Go round again rather than asking whether the run is
                # over, so a terminal frame can never overtake a pending event.
                activity.clear()
                continue

            # A lifecycle frame may have arrived on the channel while the queue
            # was idle; deliver it before falling back to inference.
            for lifecycle in await store.get_lifecycle_events(run_id):
                await send_lifecycle(lifecycle)
            if terminal_announced:
                break

            current = await store.load_state(run_id)
            if current and current.status in _TERMINAL_STATUSES:
                # Fallback for runs whose lifecycle event never landed (an old
                # run, or a Redis failure at emit time). Kept so a terminal run
                # always reports as terminal, and suppressed above when the
                # authoritative frame was delivered.
                # `type` stays `run.completed` — clients identify this legacy
                # fallback by that name plus the absence of a `sequence`, and
                # renaming it per status would have made a blocked run's
                # fallback unrecognisable to them. Which ending it was is
                # carried where it always was: `status`. A client must read
                # that field rather than assume the name means success.
                await send_json({"type": "run.completed", "status": current.status})
                break

            now = time.monotonic()
            if now - last_ping >= _PING_SECONDS:
                await send_json({"type": "ping"})
                last_ping = now
    except (WebSocketDisconnect, _ClientGone):
        pass
    finally:
        listener_task.cancel()
        await pubsub.unsubscribe(channel)
        await pubsub.close()
