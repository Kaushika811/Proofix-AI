"""Read-only endpoints over the Repository Knowledge Graph.

Additive: every route lives under `/api/knowledge`, and nothing here changes an
existing endpoint's shape. All of it is derived data — the graph is rebuilt from
the cached index on demand, never mutated — so these are safe to call at any
point in a run's life, including after it finishes.

The graph is cached per repository hash in `graph_cache`, because rebuilding it
per request would make the metrics endpoint report its own cost.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from backend.agents.repository_intelligence import load_repository_intelligence
from backend.api.deps import get_settings_dep, get_store
from backend.config import Settings
from backend.services.architecture_analyzer import analyze_architecture, explain_hotspot
from backend.services.capability_layer import infer_capabilities
from backend.services.graph_cache import get_knowledge_graph
from backend.services.graph_export import (
    EXPORTERS,
    NAMED_VIEWS,
    export,
    hotspot_map,
)
from backend.services.knowledge_graph import KnowledgeQueryEngine
from backend.services.risk_engine import assess_repository, explain_risk
from backend.state.redis_store import RedisStore

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


async def _graph_for(run_id: str, store: RedisStore, settings: Settings):
    index = await load_repository_intelligence(store, run_id, settings)
    if index is None:
        raise HTTPException(
            status_code=404,
            detail="No repository intelligence for this run — the layer is disabled or has not run yet",
        )
    return get_knowledge_graph(index)


@router.get("/{run_id}/metrics")
async def graph_metrics(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Build time, node and edge counts, degree, query latency, cache, memory."""
    graph = await _graph_for(run_id, store, settings)
    metrics = graph.metrics
    return {
        "repository_hash": graph.intelligence.repository_hash,
        "node_count": metrics.node_count,
        "edge_count": metrics.edge_count,
        "average_degree": metrics.average_degree,
        "nodes_by_type": metrics.nodes_by_type,
        "edges_by_type": metrics.edges_by_type,
        "graph_build_ms": metrics.build_ms,
        "incremental_update_ms": metrics.incremental_update_ms,
        "index_build_ms": graph.intelligence.metrics.total_ms,
        "query_count": metrics.query_count,
        "average_query_ms": metrics.average_query_ms,
        "cache_hit_rate": metrics.cache_hit_rate,
        "cache_hits": metrics.cache_hits,
        "cache_misses": metrics.cache_misses,
        "memory_bytes": metrics.memory_bytes,
        "index_size_bytes": graph.intelligence.metrics.index_size,
        "repository_coverage": metrics.repository_coverage,
        "files_total": metrics.files_total,
        "files_represented": metrics.files_represented,
        "workspace": {
            "kind": graph.intelligence.workspace.kind,
            "packages": [p.model_dump() for p in graph.intelligence.workspace.packages],
            "nested_repositories": graph.intelligence.workspace.nested_repositories,
            "languages": graph.intelligence.workspace.languages,
        },
    }


@router.get("/{run_id}/capabilities")
async def capabilities(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> list[dict]:
    """Inferred business capabilities, each with its confidence and evidence."""
    graph = await _graph_for(run_id, store, settings)
    return [
        {
            "name": c.name,
            "slug": c.slug,
            "confidence": c.confidence,
            "files": c.files,
            "entry_points": c.entry_points,
            "signals": c.signal_counts,
            "why": c.explanation.summary,
            "evidence": c.explanation.reasons(),
        }
        for c in infer_capabilities(graph)
    ]


@router.get("/{run_id}/risk")
async def repository_risk(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int = Query(20, ge=1, le=200),
) -> list[dict]:
    """Per-module risk, riskiest first, with every contributing signal itemised."""
    graph = await _graph_for(run_id, store, settings)
    return [explain_risk(a) for a in assess_repository(graph, limit=limit)]


@router.get("/{run_id}/hotspots")
async def hotspots(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit_per_kind: int = Query(10, ge=1, le=50),
) -> list[dict]:
    """Architectural findings: god objects, cycles, dead code, unowned modules."""
    graph = await _graph_for(run_id, store, settings)
    return [explain_hotspot(h) for h in analyze_architecture(graph, limit_per_kind)]


@router.get("/{run_id}/query/{name}")
async def run_query(
    run_id: str,
    name: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    file: str | None = None,
    qualname: str | None = None,
    limit: int = Query(10, ge=1, le=100),
) -> dict:
    """Run one named graph query. Deterministic traversal, no LLM."""
    graph = await _graph_for(run_id, store, settings)
    engine = KnowledgeQueryEngine(graph)

    file_queries = {
        "functions_in_file": lambda: engine.functions_in_file(file or ""),
        "documentation_for": lambda: engine.documentation_for(file or "", qualname),
        "supporting_tests": lambda: engine.supporting_tests(file or "", qualname),
        "functions_called_by": lambda: engine.functions_called_by(file or "", qualname),
        "callers_of": lambda: engine.callers_of(file or "", qualname),
        "owners_of": lambda: engine.owners_of(file or ""),
        "co_changed_files": lambda: engine.co_changed_files(file or "", limit),
        "commits_touching": lambda: engine.commits_touching(file or "", limit),
        "repairs_touching": lambda: engine.repairs_touching(file or ""),
        "validated_repairs": lambda: engine.validated_repairs(file, limit),
    }
    global_queries = {
        "historical_bug_hotspots": lambda: engine.historical_bug_hotspots(limit),
        "recent_high_churn_modules": lambda: engine.recent_high_churn_modules(limit),
        "api_surface": engine.api_surface,
        "unowned_files": engine.unowned_files,
    }

    if name in file_queries:
        if not file:
            raise HTTPException(status_code=400, detail=f"query '{name}' requires a 'file' parameter")
        results = file_queries[name]()
    elif name in global_queries:
        results = global_queries[name]()
    else:
        raise HTTPException(
            status_code=404,
            detail=f"unknown query '{name}'; available: {sorted(file_queries) + sorted(global_queries)}",
        )

    return {
        "query": name,
        "file": file,
        "qualname": qualname,
        "count": len(results),
        "results": [_serialize(r) for r in results],
        "query_ms": round(graph.metrics.query_total_ms, 4),
    }


def _serialize(result) -> dict:
    """Queries return either nodes or (node, weight) pairs."""
    if isinstance(result, tuple):
        node, weight = result
        return {**_serialize(node), "weight": weight}
    return {
        "id": result.id,
        "type": result.type,
        "name": result.name,
        "file": result.file,
        "qualname": result.qualname,
    }


@router.get("/{run_id}/export/{view}", response_class=PlainTextResponse)
async def export_graph(
    run_id: str,
    view: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    fmt: str = Query("json", pattern="^(json|graphml|dot|mermaid)$"),
    max_nodes: int = Query(300, ge=1, le=2000),
) -> PlainTextResponse:
    """Export a named view as JSON, GraphML, DOT, or Mermaid."""
    graph = await _graph_for(run_id, store, settings)

    if view == "hotspots":
        rendered = export(
            hotspot_map(graph, analyze_architecture(graph), max_nodes=max_nodes), fmt
        )
    elif view in NAMED_VIEWS:
        if view == "architecture":
            # PART_OF edges only exist once capabilities are attached.
            from backend.services.capability_layer import attach_capabilities

            attach_capabilities(graph, infer_capabilities(graph))
        rendered = export(NAMED_VIEWS[view](graph, max_nodes=max_nodes), fmt)
    else:
        raise HTTPException(
            status_code=404,
            detail=f"unknown view '{view}'; available: {sorted(NAMED_VIEWS) + ['hotspots']}",
        )

    media = {
        "json": "application/json",
        "graphml": "application/xml",
        "dot": "text/vnd.graphviz",
        "mermaid": "text/plain",
    }[fmt]
    return PlainTextResponse(rendered, media_type=media)


@router.get("/formats")
async def formats() -> dict:
    """Discoverability: what views and formats exist."""
    return {
        "views": sorted(NAMED_VIEWS) + ["hotspots"],
        "formats": sorted(EXPORTERS),
    }
