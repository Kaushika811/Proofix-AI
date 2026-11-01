"""Repository Intelligence — the layer that remembers between runs.

Runs once, before A1, and publishes a `RepositoryIntelligence` object: the
repository graph, call graph, ownership, git history, documentation index and
repair memory. Downstream agents read it as an optimisation and as extra ranking
signal; none of them require it.

Hard boundaries, mirroring A5.5:

* **Never calls an LLM.** Every figure is derived from the working tree and local
  git history.
* **Never mutates RunState.** The index is published to the run store under its
  own key, and the index itself lives in a cross-run cache.
* **Never blocks a run.** Any failure is caught, recorded, and the pipeline
  proceeds exactly as it did before this layer existed.

Storage is split deliberately. The full index is megabytes and identical across
runs of the same repository, so it lives once in the cross-run cache keyed by
repository id. The run store holds only a pointer plus this run's metrics, and
consumers follow the pointer. Copying the whole index into every run's state
would multiply Redis memory by the run count for no benefit.
"""

from __future__ import annotations

from pathlib import Path

from backend.agents.base import AgentBase
from backend.models.knowledge_graph import KnowledgeGraphSummary
from backend.models.repository_graph import RepositoryIntelligence
from backend.services.architecture_analyzer import analyze_architecture
from backend.services.capability_layer import attach_capabilities, infer_capabilities
from backend.services.graph_cache import get_knowledge_graph
from backend.services.repair_memory import repository_id as compute_repository_id
from backend.services.repo_layout import discover_source_roots
from backend.services.repository_cache import RepositoryCache
from backend.services.repository_indexer import index_repository
from backend.services.risk_engine import assess_repository
from backend.state.schema import RunStateModel

INTELLIGENCE_STORE_KEY = "repository_intelligence"
KNOWLEDGE_STORE_KEY = "knowledge_graph"


class RepositoryIntelligenceAgent(AgentBase):
    agent_id = "A0.5"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Loading repository intelligence")

        repo = Path(state.repo_clone_path or state.repo_path).resolve()
        source_roots = state.source_roots or discover_source_roots(repo)
        state.source_roots = source_roots

        # The clone path changes every run, so identity comes from the original
        # repository path — that is what makes the cache and repair memory
        # persistent rather than per-run.
        repo_id = compute_repository_id(state.repo_path or repo)

        cache = RepositoryCache(
            self.store,
            ttl_seconds=self.settings.state_ttl_seconds,
            version=self.settings.repository_cache_version,
        )

        cached = await cache.load_index(repo_id)
        index = index_repository(
            repo,
            source_roots,
            cached=cached,
            history_window_days=self.settings.repository_history_window_days,
            max_files=self.settings.repository_index_max_files,
            repository_id=repo_id,
        )

        # Repair memory is stored separately and outlives any commit, so the
        # authoritative copy always replaces whatever the index carried forward.
        index.repair_memory = await cache.load_repair_memory(repo_id)
        index.repair_memory.repository_id = repo_id
        index.metrics.repair_memory_entries = len(index.repair_memory.records)

        await cache.save_index(index)
        await self.store.set_json(state.run_id, INTELLIGENCE_STORE_KEY, self._pointer(index))

        summary = self._build_graph_summary(index)
        if summary is not None:
            await self.store.set_json(
                state.run_id, KNOWLEDGE_STORE_KEY, summary.model_dump(mode="json")
            )

        payload = {"repository_intelligence": self._metrics_payload(index, cache)}
        if summary is not None:
            payload["knowledge_graph"] = self._graph_payload(summary)

        learning = self._observe_for_learning(state, repo, index)
        if learning is not None:
            payload["learning"] = learning

        await self.emit_status(state, "completed", self._summary(index, summary), payload)
        return state

    def _observe_for_learning(self, state: RunStateModel, repo, index) -> dict | None:
        """Refresh the repository's style and framework profiles.

        Done here because A0.5 has already parsed every file, so learning adds
        no AST work of its own — which is what keeps the update inside its
        100 ms budget on a real repository.
        """
        if not self.settings.learning_enabled or not index.parsed_modules:
            return None
        try:
            from backend.services.learning_pipeline import get_learning_pipeline

            pipeline = get_learning_pipeline(self.settings, self.store)
            profile = pipeline.observe_repository(
                index.repository_id, repo, index.parsed_modules, repository_name=repo.name
            )
            if profile is None:
                return None
            return {
                "framework": profile.framework.primary_framework,
                "framework_confidence": profile.framework.confidence,
                "style_confidence": profile.style.confidence,
                "function_naming": profile.style.function_naming,
                "repository_maturity": profile.maturity,
                "repairs_recorded": profile.repairs_recorded,
            }
        except Exception:  # noqa: BLE001 — learning is advisory
            return None

    def _build_graph_summary(self, index: RepositoryIntelligence) -> KnowledgeGraphSummary | None:
        """Derive capabilities, risk and hotspots from the unified graph.

        The graph itself is not stored: it is derived data, rebuilt on demand
        from the cached index in tens of milliseconds. Only the small summary is
        published, which is what keeps this layer from duplicating the six
        structures it unifies.
        """
        if not self.settings.knowledge_graph_enabled:
            return None
        try:
            graph = get_knowledge_graph(index)
            capabilities = infer_capabilities(graph)
            attach_capabilities(graph, capabilities)
            return KnowledgeGraphSummary(
                repository_hash=index.repository_hash,
                metrics=graph.metrics,
                capabilities=capabilities,
                top_risks=assess_repository(graph, limit=self.settings.knowledge_graph_top_risks),
                hotspots=analyze_architecture(
                    graph, limit_per_kind=self.settings.knowledge_graph_hotspots_per_kind
                ),
            )
        except Exception:  # noqa: BLE001 — the graph is advisory, like everything here
            return None

    # -- publication ------------------------------------------------------

    @staticmethod
    def _pointer(index: RepositoryIntelligence) -> dict:
        """What the run store holds: identity, provenance and this run's metrics."""
        return {
            "schema_version": index.schema_version,
            "repository_id": index.repository_id,
            "repository_hash": index.repository_hash,
            "head_sha": index.head_sha,
            "source_roots": list(index.source_roots),
            "generated_at": index.generated_at.isoformat(),
            "metrics": index.metrics.model_dump(),
            "delta": index.delta.model_dump(),
        }

    @staticmethod
    def _graph_payload(summary: KnowledgeGraphSummary) -> dict:
        metrics = summary.metrics
        return {
            "node_count": metrics.node_count,
            "edge_count": metrics.edge_count,
            "average_degree": metrics.average_degree,
            "nodes_by_type": metrics.nodes_by_type,
            "edges_by_type": metrics.edges_by_type,
            "graph_build_ms": metrics.build_ms,
            "memory_bytes": metrics.memory_bytes,
            "repository_coverage": metrics.repository_coverage,
            "capabilities": [
                {"name": c.name, "confidence": c.confidence, "files": len(c.files)}
                for c in summary.capabilities
            ],
            "top_risks": [
                {"module": r.module, "risk": r.risk, "band": r.band, "reason": r.reason}
                for r in summary.top_risks
            ],
            "hotspots": [
                {"kind": h.kind, "target": h.target, "severity": h.severity}
                for h in summary.hotspots
            ],
        }

    @staticmethod
    def _summary(index: RepositoryIntelligence, graph: KnowledgeGraphSummary | None = None) -> str:
        metrics = index.metrics
        if metrics.cache_hits:
            mode = "cache hit"
        elif metrics.incremental_updates:
            mode = (
                f"incremental (+{metrics.files_added} ~{metrics.files_modified} "
                f"-{metrics.files_deleted} ->{metrics.files_renamed})"
            )
        else:
            mode = "full rebuild"
        text = (
            f"Repository index {mode}: {metrics.repository_nodes} nodes, "
            f"{metrics.repository_edges} edges, {metrics.call_graph_nodes} callables, "
            f"{metrics.repair_memory_entries} remembered repair(s)"
        )
        if graph is not None:
            text += (
                f"; knowledge graph {graph.metrics.node_count} nodes / "
                f"{graph.metrics.edge_count} edges, "
                f"{len(graph.capabilities)} capability group(s)"
            )
        return text

    @staticmethod
    def _metrics_payload(index: RepositoryIntelligence, cache: RepositoryCache) -> dict:
        payload = index.metrics.model_dump()
        payload["cache"] = cache.metrics.to_dict()
        payload["repository_id"] = index.repository_id
        # Known the moment the index runs (`discover_source_roots` in `run()`),
        # but never published anywhere a client can read it — the Repository DNA
        # panel needs it and there is no reason to add an endpoint for one field
        # already sitting on the object this event already serializes.
        payload["source_roots"] = list(index.source_roots)
        return payload


# -- consumer helpers ------------------------------------------------------


async def load_repository_intelligence(
    store,
    run_id: str,
    settings=None,
) -> RepositoryIntelligence | None:
    """Read the index this run published, or None when the layer did not run.

    Follows the run-store pointer into the cross-run cache. Every consumer must
    treat None as normal and fall back to its pre-Phase-3 behaviour.
    """
    try:
        pointer = await store.get_json(run_id, INTELLIGENCE_STORE_KEY)
    except Exception:  # noqa: BLE001 — consumers degrade, never fail
        return None
    if not pointer or not pointer.get("repository_id"):
        return None

    version = getattr(settings, "repository_cache_version", "v1") if settings else "v1"
    cache = RepositoryCache(store, version=version)
    return await cache.load_index(pointer["repository_id"])
