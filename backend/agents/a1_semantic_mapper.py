import logging
import time
from datetime import datetime
from pathlib import Path

from backend.agents.base import AgentBase
from backend.agents.repository_intelligence import load_repository_intelligence
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.ast_import_graph import (
    build_import_graph,
    build_import_graph_reusing,
    compute_criticality,
)
from backend.services.git_service import get_churn_weights
from backend.services.repo_layout import discover_source_roots
from backend.services.role_classifier import (
    RolePrediction,
    accept_local_prediction,
    classify_file_role,
)
from backend.services.role_llm_classifier import classify_ambiguous_batch
from backend.services.sig_cache import (
    build_cache_payload,
    compute_repo_hash,
    deserialize_payload,
    payload_to_graph,
    payload_to_parsed,
    payload_to_roles,
    serialize_payload,
)
from backend.state.schema import RunStateModel

logger = logging.getLogger(__name__)


class A1SemanticMapperAgent(AgentBase):
    agent_id = "A1"

    async def run(self, state: RunStateModel) -> RunStateModel:
        t0 = time.monotonic()
        await self.emit_status(state, "started", "Building Semantic Intent Graph")

        repo = Path(state.repo_clone_path or state.repo_path).resolve()
        source_roots = state.source_roots or discover_source_roots(repo)
        state.source_roots = source_roots

        metrics: dict = {
            "total_files": 0,
            "filename_classified": 0,
            "ast_classified": 0,
            "llm_classified": 0,
            "ambiguous_files": 0,
            "llm_calls": 0,
            "batch_size": 0,
            "llm_calls_saved": 0,
            "cache_hit": False,
            "cache_miss": True,
            "cache_version": self.settings.sig_cache_key_version,
            "cache_age_seconds": None,
            "cached_files": None,
            "repo_hash": "",
            "ast_build_ms": 0,
            "role_classification_ms": 0,
            "cache_lookup_ms": 0,
            "llm_ms": 0,
            "total_a1_ms": 0,
            "parse_count": 0,
            "repository_index_available": False,
            "repository_index_reused_parses": 0,
        }

        repo_hash = compute_repo_hash(repo, source_roots)
        metrics["repo_hash"] = repo_hash

        roles: dict[str, RolePrediction] = {}
        parsed_modules: dict = {}
        graph = None
        cache_payload = None

        t_cache = time.monotonic()
        use_cache = (
            self.settings.sig_cache_enabled
            and not self.settings.stub_mode
        )
        if use_cache:
            raw = await self.store.get_sig_cache(
                self.settings.sig_cache_key_version,
                repo_hash,
            )
            if raw:
                cache_payload = deserialize_payload(raw)
                metrics["cache_hit"] = True
                metrics["cache_miss"] = False
                metrics["cached_files"] = len(cache_payload.files)
                age = (datetime.utcnow() - cache_payload.cached_at).total_seconds()
                metrics["cache_age_seconds"] = int(max(0, age))
                roles = payload_to_roles(cache_payload)
                parsed_modules = payload_to_parsed(cache_payload)
                graph = payload_to_graph(cache_payload)
                metrics["parse_count"] = 0
        metrics["cache_lookup_ms"] = int((time.monotonic() - t_cache) * 1000)

        if cache_payload is None:
            t_ast = time.monotonic()
            # A0.5 already parsed these files this run. Reusing that parse is
            # pure saving: the graph it produces is identical either way.
            indexed_modules = await self._indexed_modules(state)
            metrics["repository_index_available"] = indexed_modules is not None
            if indexed_modules:
                graph, parsed_modules, reused = build_import_graph_reusing(
                    repo, source_roots, indexed_modules
                )
                metrics["repository_index_reused_parses"] = reused
            else:
                graph, parsed_modules = build_import_graph(repo, source_roots=source_roots)
            metrics["ast_build_ms"] = int((time.monotonic() - t_ast) * 1000)
            metrics["parse_count"] = len(parsed_modules)

            t_classify = time.monotonic()
            ambiguous_queue: list[tuple[str, object]] = []
            local_predictions: dict[str, RolePrediction] = {}

            for path, parsed in parsed_modules.items():
                if "test" in path.lower():
                    roles[path] = RolePrediction(
                        role="test-only", confidence=1.0, role_source="filename"
                    )
                    continue

                prediction = classify_file_role(path, parsed, settings=self.settings)
                local_predictions[path] = prediction

                if accept_local_prediction(prediction, path, self.settings):
                    roles[path] = prediction
                else:
                    ambiguous_queue.append((path, parsed))

            metrics["ambiguous_files"] = len(ambiguous_queue)

            if ambiguous_queue and not self.settings.stub_mode:
                t_llm = time.monotonic()
                llm_results = await classify_ambiguous_batch(
                    ambiguous_queue,
                    settings=self.settings,
                    ast_predictions=local_predictions,
                    run_id=state.run_id,
                    agent_id=self.agent_id,
                )
                metrics["llm_ms"] = int((time.monotonic() - t_llm) * 1000)
                if llm_results:
                    metrics["llm_calls"] = 1
                    metrics["batch_size"] = len(ambiguous_queue)
                roles.update(llm_results)
            elif ambiguous_queue and self.settings.stub_mode:
                for path, parsed in ambiguous_queue:
                    roles[path] = classify_file_role(path, parsed, settings=self.settings)

            for path in parsed_modules:
                if path not in roles:
                    roles[path] = local_predictions.get(
                        path,
                        RolePrediction(role="internal-util", confidence=0.4, role_source="ast"),
                    )

            metrics["role_classification_ms"] = int((time.monotonic() - t_classify) * 1000)

            if use_cache and graph is not None:
                imported_by_map = _build_imported_by(graph.files)
                payload = build_cache_payload(
                    source_roots,
                    graph,
                    roles,
                    parsed_modules,
                    imported_by_map,
                )
                await self.store.set_sig_cache(
                    self.settings.sig_cache_key_version,
                    repo_hash,
                    serialize_payload(payload),
                    self.settings.sig_cache_ttl_seconds,
                )

        assert graph is not None
        churn = get_churn_weights(repo)
        imported_by_map = _build_imported_by(graph.files)

        files: dict[str, FileNode] = {}
        for path in graph.files:
            pred = roles.get(path)
            role = pred.role if pred else "internal-util"
            churn_w = churn.get(path, 0.0)
            crit = compute_criticality(role, churn_w)
            files[path] = FileNode(
                path=path,
                role=role,  # type: ignore[arg-type]
                imports=graph.files.get(path, []),
                imported_by=imported_by_map.get(path, []),
                churn_weight=churn_w,
                criticality=crit,
            )
            if pred:
                if pred.role_source == "filename":
                    metrics["filename_classified"] += 1
                elif pred.role_source == "ast":
                    metrics["ast_classified"] += 1
                elif pred.role_source == "llm":
                    metrics["llm_classified"] += 1

        metrics["total_files"] = len(files)
        metrics["llm_calls_saved"] = max(0, metrics["total_files"] - metrics["llm_calls"])
        metrics["total_a1_ms"] = int((time.monotonic() - t0) * 1000)

        sig = SemanticIntentGraph(
            repo_path=str(repo),
            source_roots=source_roots,
            files=files,
            edges=[(e[0], e[1]) for e in graph.edges],
            generated_at=datetime.utcnow(),
        )
        sig_dict = sig.model_dump(mode="json")
        await self.store.set_json(state.run_id, "sig", sig_dict)
        state.sig = sig_dict

        logger.info("a1_metrics", extra=metrics)
        await self.emit_status(
            state,
            "completed",
            f"SIG built with {len(files)} files",
            {
                "file_count": len(files),
                "source_roots": source_roots,
                "a1_metrics": metrics,
            },
        )
        return state


    async def _indexed_modules(self, state: RunStateModel) -> dict | None:
        """Parsed modules from the repository index, or None when unavailable.

        Failure to load is silent by design — this is an optimisation, and A1
        must build the same SIG with or without it.
        """
        if not (
            self.settings.repository_intelligence_enabled
            and self.settings.repository_reuse_in_a1
        ):
            return None
        try:
            index = await load_repository_intelligence(self.store, state.run_id, self.settings)
        except Exception:  # noqa: BLE001 — never fail A1 for a cache read
            return None
        return index.parsed_modules if index and index.parsed_modules else None


def _build_imported_by(import_map: dict[str, list[str]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for path in import_map:
        result[path] = [
            p
            for p, imps in import_map.items()
            if p != path and any(Path(path).stem in imp or imp in path for imp in imps)
        ]
    return result
