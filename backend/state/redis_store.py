import json
from typing import Any

import redis.asyncio as aioredis

from backend.config import Settings, get_settings
from backend.state.events import AgentStatusEvent, RunLifecycleEvent
from backend.state.schema import RunState, RunStateModel, state_to_model

RUN_INDEX_KEY = "bugfix:runs:index"


class RedisStore:
    def __init__(self, client: aioredis.Redis, settings: Settings | None = None):
        self.client = client
        self.settings = settings or get_settings()
        self.ttl = self.settings.state_ttl_seconds

    def _prefix(self, run_id: str) -> str:
        return f"bugfix:{run_id}"

    async def init_run(
        self, run_id: str, repo_path: str, issue_hint: str | None = None,
        issue_contract: dict | None = None,
    ) -> RunStateModel:
        state = RunStateModel(
            run_id=run_id, repo_path=repo_path, issue_hint=issue_hint,
            issue_contract=issue_contract, status="pending",
        )
        await self.save_state(state)
        meta = {
            "status": state.status,
            "repo_path": repo_path,
            "created_at": state.created_at.isoformat(),
            "retry_count": "0",
            "force_draft_pr": "false",
        }
        key = f"{self._prefix(run_id)}:meta"
        await self.client.hset(key, mapping=meta)
        await self.client.expire(key, self.ttl)
        await self.index_run(run_id, state.created_at.timestamp())
        return state

    async def index_run(self, run_id: str, score: float) -> None:
        """Record a run in the global index so the UI can list run history."""
        await self.client.zadd(RUN_INDEX_KEY, {run_id: score})

    async def list_run_ids(self, limit: int = 100) -> list[str]:
        """Return run ids, most recent first."""
        raw = await self.client.zrevrange(RUN_INDEX_KEY, 0, max(0, limit - 1))
        return [r.decode() if isinstance(r, bytes) else r for r in raw]

    async def prune_run_index(self, run_ids: list[str]) -> None:
        """Drop index entries whose state has expired out of Redis."""
        if run_ids:
            await self.client.zrem(RUN_INDEX_KEY, *run_ids)

    async def save_state(self, state: RunStateModel | RunState) -> None:
        if isinstance(state, RunStateModel):
            model = state
        else:
            model = state_to_model(state)
        key = f"{self._prefix(model.run_id)}:state"
        await self.client.set(key, model.model_dump_json(), ex=self.ttl)

    async def load_state(self, run_id: str) -> RunStateModel | None:
        key = f"{self._prefix(run_id)}:state"
        raw = await self.client.get(key)
        if not raw:
            return None
        return RunStateModel.model_validate_json(raw)

    async def set_json(self, run_id: str, suffix: str, data: dict | list) -> None:
        key = f"{self._prefix(run_id)}:{suffix}"
        await self.client.set(key, json.dumps(data), ex=self.ttl)

    async def get_json(self, run_id: str, suffix: str) -> Any | None:
        key = f"{self._prefix(run_id)}:{suffix}"
        raw = await self.client.get(key)
        if not raw:
            return None
        return json.loads(raw)

    async def append_event(self, event: AgentStatusEvent) -> None:
        key = f"{self._prefix(event.run_id)}:events"
        await self.client.xadd(
            key,
            {"data": event.model_dump_json()},
            maxlen=1000,
        )
        await self.client.expire(key, self.ttl)
        channel = f"bugfix:{event.run_id}:live"
        await self.client.publish(channel, event.model_dump_json())

    async def append_lifecycle_event(self, event: RunLifecycleEvent) -> bool:
        """Persist and publish a lifecycle event, at most once per run and kind.

        Returns True when this call is the one that recorded it. The guard is a
        Redis `SET NX`, so concurrent writers — a retrying runner, two workers
        racing on the same run — cannot produce a second `run.completed`.
        """
        guard = f"{self._prefix(event.run_id)}:lifecycle:{event.type}"
        if not await self.client.set(guard, "1", nx=True, ex=self.ttl):
            return False

        key = f"{self._prefix(event.run_id)}:lifecycle"
        await self.client.rpush(key, event.model_dump_json())
        await self.client.expire(key, self.ttl)
        await self.client.publish(
            f"bugfix:{event.run_id}:live", event.model_dump_json()
        )
        return True

    async def get_lifecycle_events(self, run_id: str) -> list[RunLifecycleEvent]:
        """Lifecycle events for a run, in the order they occurred."""
        key = f"{self._prefix(run_id)}:lifecycle"
        raw = await self.client.lrange(key, 0, -1)
        events: list[RunLifecycleEvent] = []
        for item in raw:
            if isinstance(item, bytes):
                item = item.decode()
            events.append(RunLifecycleEvent.model_validate_json(item))
        return events

    async def get_events(
        self,
        run_id: str,
        count: int = 100,
        after: int | None = None,
    ) -> list[AgentStatusEvent]:
        """Agent events in chronological order.

        `xrevrange` reads the *newest* `count`, so a run that emitted more than
        that silently lost its oldest events — and the client replays the
        timeline from the beginning, so what it lost was the start of the run,
        with nothing saying so (B-B15).

        `after` is an exclusive cursor on `AgentStatusEvent.sequence`, which
        `AgentBase.emit_status` increments once per event per run and is
        therefore monotonic. A caller that receives `count` events asks again
        from the last sequence it saw until a short page comes back. Reading
        forward from the cursor is what makes the whole timeline reachable
        rather than only its tail.
        """
        key = f"{self._prefix(run_id)}:events"
        entries = await self.client.xrevrange(key, count=count) if after is None else (
            list(reversed(await self.client.xrange(key)))
        )

        events = []
        for _entry_id, fields in reversed(entries):
            data = fields.get(b"data") or fields.get("data")
            if not data:
                continue
            if isinstance(data, bytes):
                data = data.decode()
            event = AgentStatusEvent.model_validate_json(data)
            if after is not None and event.sequence <= after:
                continue
            events.append(event)
            if after is not None and len(events) >= count:
                break
        return events

    async def update_meta(self, run_id: str, **fields: str) -> None:
        key = f"{self._prefix(run_id)}:meta"
        if fields:
            await self.client.hset(key, mapping=fields)
        await self.client.expire(key, self.ttl)

    #: Default patch-lock lease. The old 60 s was shorter than the work it
    #: guarded — A7 makes one to three LLM calls per invocation, any of which
    #: can exceed a minute on its own — so the lock routinely expired
    #: mid-generation and a second writer could enter the same clone. The lease
    #: is renewed per plan (`renew_lock`), so this bounds one iteration rather
    #: than the whole agent.
    LOCK_TTL_SECONDS = 600

    async def acquire_lock(self, run_id: str, ttl: int = LOCK_TTL_SECONDS) -> bool:
        key = f"{self._prefix(run_id)}:lock"
        return bool(await self.client.set(key, "1", nx=True, ex=ttl))

    async def renew_lock(self, run_id: str, ttl: int = LOCK_TTL_SECONDS) -> bool:
        """Extend a lease this process already holds.

        `xx=True` is what makes it a renewal rather than a re-acquire: if the
        lease has already expired the key is gone, nothing is written, and the
        caller learns it no longer holds the lock instead of silently taking it
        back from whoever picked it up.
        """
        key = f"{self._prefix(run_id)}:lock"
        return bool(await self.client.set(key, "1", xx=True, ex=ttl))

    async def release_lock(self, run_id: str) -> None:
        key = f"{self._prefix(run_id)}:lock"
        await self.client.delete(key)

    # -- generic cross-run cache -------------------------------------------
    # Namespaced, versioned, run-independent storage. The SIG cache predates
    # this and keeps its own method pair; new caches (repository intelligence,
    # repair memory) use these rather than growing another bespoke pair.

    def namespaced_key(self, namespace: str, version: str, key: str) -> str:
        return f"{namespace}:{version}:{key}"

    async def get_cached(self, namespace: str, version: str, key: str) -> str | None:
        raw = await self.client.get(self.namespaced_key(namespace, version, key))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw

    async def set_cached(
        self,
        namespace: str,
        version: str,
        key: str,
        payload_json: str,
        ttl: int | None = None,
    ) -> None:
        await self.client.set(
            self.namespaced_key(namespace, version, key),
            payload_json,
            ex=ttl or self.ttl,
        )

    async def delete_cached(self, namespace: str, version: str, key: str) -> None:
        await self.client.delete(self.namespaced_key(namespace, version, key))

    def sig_cache_redis_key(self, version: str, repo_hash: str) -> str:
        return f"sig_cache:{version}:{repo_hash}"

    async def get_sig_cache(self, version: str, repo_hash: str) -> str | None:
        key = self.sig_cache_redis_key(version, repo_hash)
        raw = await self.client.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode()
        return raw

    async def set_sig_cache(
        self,
        version: str,
        repo_hash: str,
        payload_json: str,
        ttl: int,
    ) -> None:
        key = self.sig_cache_redis_key(version, repo_hash)
        await self.client.set(key, payload_json, ex=ttl)


async def create_redis_client(settings: Settings | None = None) -> aioredis.Redis:
    settings = settings or get_settings()
    return aioredis.from_url(settings.redis_url, decode_responses=False)
