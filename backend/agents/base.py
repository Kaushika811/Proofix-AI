from abc import ABC, abstractmethod
from datetime import datetime

from backend.config import Settings, get_settings
from backend.state.events import AgentStatusEvent
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel


class AgentBase(ABC):
    agent_id: str = "A0"

    def __init__(self, store: RedisStore, settings: Settings | None = None):
        self.store = store
        self.settings = settings or get_settings()

    async def emit_status(
        self,
        state: RunStateModel,
        status: str,
        message: str = "",
        payload: dict | None = None,
    ) -> None:
        state.ws_sequence += 1
        event = AgentStatusEvent(
            run_id=state.run_id,
            agent_id=self.agent_id,
            status=status,  # type: ignore[arg-type]
            timestamp=datetime.utcnow(),
            message=message,
            payload=payload,
            sequence=state.ws_sequence,
        )
        # One delivery path. `append_event` persists to the run's stream *and*
        # publishes on `bugfix:{run_id}:live`, which every replica's WebSocket
        # subscribes to. The in-memory `WSBroadcaster` that used to be notified
        # here delivered the same event a second time to same-process clients
        # only — invisible because the socket deduplicates frames, and useless
        # to any client attached to a different replica (B-B13).
        await self.store.append_event(event)

    @abstractmethod
    async def run(self, state: RunStateModel) -> RunStateModel:
        ...
