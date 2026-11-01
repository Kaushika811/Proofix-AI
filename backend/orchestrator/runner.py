import logging
import traceback
from typing import get_args

from langgraph.checkpoint.memory import MemorySaver

from backend.config import Settings
from backend.orchestrator.graph import build_graph
from backend.services.git_service import discard_clone
from backend.services.ui_projection import run_decision
from backend.state.events import PRType, RunLifecycleEvent
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

logger = logging.getLogger(__name__)

# Values `RunLifecycleEvent.decision` accepts. An unexpected routing outcome is
# reported as no decision rather than crashing the announcement.
PR_TYPES = frozenset(get_args(PRType))


class PipelineRunner:
    def __init__(self, store: RedisStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.checkpointer = MemorySaver()
        self.graph = build_graph(store, settings).compile(checkpointer=self.checkpointer)

    async def execute(self, run_id: str) -> RunStateModel:
        state = await self.store.load_state(run_id)
        if not state:
            raise ValueError(f"Run {run_id} not found")

        state.status = "running"
        await self.store.save_state(state)
        await self._emit_lifecycle(RunLifecycleEvent(type="run.started", run_id=run_id))

        config = {"configurable": {"thread_id": run_id}}
        initial = state.model_dump(exclude_none=False)

        try:
            final = await self.graph.ainvoke(initial, config)
            result = RunStateModel(**{k: v for k, v in final.items() if k in RunStateModel.model_fields})

            # `blocked` is a real terminal outcome and must survive. The blanket
            # coercion below exists so a run that reached the end is not left
            # reporting an intermediate status, but applying it to `blocked`
            # would have relabelled "we could not run your tests" as a completed
            # repair.
            if result.status == "blocked":
                await self.store.save_state(result)
                await self._emit_blocked(result)
                return result

            if result.status != "completed":
                result.status = "completed"
            await self.store.save_state(result)
            await self._emit_completed(result)
            return result
        except Exception as e:
            # Re-read before marking failed. `state` is the snapshot taken
            # *before* the graph ran, and every node persists its own progress
            # as it goes — so saving this object overwrote all of it. A run that
            # failed at A4 came back reporting no environment report, no SIG, no
            # static findings and no reproduction, none of which was true: five
            # agents had completed and their work was discarded by the failure
            # handler. The failure annotates the real state instead.
            latest = await self.store.load_state(run_id) or state
            latest.status = "failed"
            latest.errors.append({"error": str(e), "trace": traceback.format_exc()})
            state = latest
            await self.store.save_state(state)
            await self._emit_lifecycle(
                RunLifecycleEvent(
                    type="run.failed",
                    run_id=run_id,
                    reason=f"{type(e).__name__}: {e}",
                    sequence=state.ws_sequence + 1,
                )
            )
            raise
        finally:
            # Every terminal path — completed, blocked, failed — reaches here,
            # which is the point: the leak was worst on the paths nobody thinks
            # about. Deliberately after the state is saved and the lifecycle
            # event emitted, so cleanup can never delay or displace the run's
            # own result. Prepared drafts are retained for the explicit user
            # publication action; expired copies are swept by the app lifecycle.
            await self._discard_clone(run_id)

    async def _discard_clone(self, run_id: str) -> None:
        """Remove this run's working copy. Never fails the run.

        Reads the freshly persisted state rather than trusting the in-scope
        object, because the failure path re-reads state and the clone path is
        set by a node partway through the graph.
        """
        try:
            latest = await self.store.load_state(run_id)
            if latest and (latest.publication_result or {}).get("prepared_head_sha") and (latest.publication_result or {}).get("status") != "published":
                return  # retained until user publishes or the publication deadline expires
            if latest and discard_clone(latest.repo_clone_path):
                logger.info(
                    "clone_discarded",
                    extra={"cleanup": {"run_id": run_id, "path": latest.repo_clone_path}},
                )
        except Exception as exc:  # noqa: BLE001 — cleanup never fails a run
            logger.warning(
                "clone_cleanup_failed",
                extra={"cleanup": {"run_id": run_id, "error": str(exc)}},
            )

    async def _emit_blocked(self, result: RunStateModel) -> None:
        """Announce a run stopped by the environment precheck.

        Carries the probe's own `reason`, so the client renders why in the
        backend's words rather than composing a sentence of its own.
        """
        env = result.environment or {}
        _decision, label = run_decision(result)
        try:
            event = RunLifecycleEvent(
                type="run.blocked",
                run_id=result.run_id,
                decision_label=label,
                reason=str(result.recovery_blocked_reason or env.get("reason") or
                           "The repository's tests could not be executed."),
                sequence=result.ws_sequence + 1,
            )
        except Exception as exc:  # noqa: BLE001 — reported, never propagated
            logger.warning(
                "lifecycle_build_failed",
                extra={"lifecycle": {"run_id": result.run_id, "error": str(exc)}},
            )
            return
        await self._emit_lifecycle(event)

    async def _emit_completed(self, result: RunStateModel) -> None:
        """Announce completion without letting the announcement fail the run.

        Building the event is inside the caller's success path, so anything that
        could raise here — an unexpected `pr_type`, a projection error — is
        contained. A run that succeeded must never be reported as failed because
        its telemetry could not be assembled.
        """
        try:
            _decision, label = run_decision(result)
            pr_type = (result.pr_decision or {}).get("pr_type")
            event = RunLifecycleEvent(
                type="run.completed",
                run_id=result.run_id,
                decision=pr_type if pr_type in PR_TYPES else None,
                decision_label=label,
                sequence=result.ws_sequence + 1,
            )
        except Exception as exc:  # noqa: BLE001 — reported, never propagated
            logger.warning(
                "lifecycle_build_failed",
                extra={"lifecycle": {"run_id": result.run_id, "error": str(exc)}},
            )
            return
        await self._emit_lifecycle(event)

    async def _emit_lifecycle(self, event: RunLifecycleEvent) -> None:
        """Publish a lifecycle event. Never let telemetry fail a run.

        A run that finished but could not announce it is still a run that
        finished; raising here would turn a Redis hiccup into a failed repair.
        The WebSocket's state-poll fallback still reports terminal runs, so the
        client degrades rather than hangs.
        """
        try:
            await self.store.append_lifecycle_event(event)
        except Exception as exc:  # noqa: BLE001 — reported, never propagated
            logger.warning(
                "lifecycle_emit_failed",
                extra={"lifecycle": {"run_id": event.run_id, "type": event.type, "error": str(exc)}},
            )
