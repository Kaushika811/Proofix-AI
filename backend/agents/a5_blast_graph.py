from pathlib import Path

from backend.agents.base import AgentBase
from backend.models.blast import BlastGraphResult, TargetResolutionSummary
from backend.models.sig import SemanticIntentGraph
from backend.services.blast_traversal import resolve_origins, traverse_multi_origin
from backend.services.target_resolver import pin_resolved_target, resolve_patch_target
from backend.state.schema import RunStateModel


class A5BlastGraphAgent(AgentBase):
    agent_id = "A5"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Expanding blast graph scope")
        sig_data = state.sig or await self.store.get_json(state.run_id, "sig")
        root_cause = state.root_cause or {}
        citations = root_cause.get("citations", [])

        target = None
        pinned = False
        runtime_confirmed = (state.reproduction or {}).get("status") == "CONFIRMED"

        if not sig_data or (not citations and not state.issue_contract):
            result = BlastGraphResult(auto_patch_scope=[], scope=[], origins=[])
        else:
            sig = SemanticIntentGraph.model_validate(sig_data)
            repo = Path(state.repo_clone_path or state.repo_path)
            target = resolve_patch_target(repo, state, sig)

            if target.resolved_application_path:
                origins = [target.resolved_application_path]
            else:
                origins = resolve_origins(citations)

            result = traverse_multi_origin(sig, origins)
            if target.resolved_application_path:
                pinned = pin_resolved_target(result, target, runtime_confirmed)

            # Persisted, not just emitted on the transient WS event: the event
            # rolls off after 500 events and is invisible to a client opening a
            # finished run — exactly the reason `investigation` was added to
            # A4's state rather than left on the event alone.
            result.target_resolution = TargetResolutionSummary(
                original_path=target.original_path,
                normalized_path=target.normalized_path,
                resolved_path=target.resolved_application_path,
                source=target.resolution_source,
                confidence=target.confidence,
                runtime_confirmed=runtime_confirmed,
                pinned=pinned,
            )

        result_dict = result.model_dump(mode="json")
        state.blast_graph = result_dict
        state.human_review_files = result.human_review_required

        payload = {
            "scope_count": len(result.scope),
            "human_review": len(result.human_review_required),
            "origins": result.origins,
        }
        if target is not None:
            payload["target_resolution"] = {
                "runtime_confirmed": runtime_confirmed,
                "original_path": target.original_path,
                "normalized_path": target.normalized_path,
                "resolved_target": target.resolved_application_path,
                "resolution_source": target.resolution_source,
                "confidence": target.confidence,
            }

        await self.emit_status(
            state,
            "completed",
            f"Blast scope: {len(result.auto_patch_scope)} files auto-patchable from {len(result.origins)} origins",
            payload,
        )
        return state
