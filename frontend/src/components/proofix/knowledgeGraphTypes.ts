/**
 * Types for A0.5's Repository Knowledge Graph endpoints
 * (`backend/api/routes/knowledge.py`, `backend/services/graph_export.py`).
 *
 * These mirror the backend's JSON shape field-for-field — nothing here is
 * invented. Where the backend can omit a field (an unmeasured metric, an
 * absent workspace), the type says so with `| null` rather than assuming a
 * default the backend never sent.
 */

/** One node from `GET /api/knowledge/{runId}/export/{view}`. */
export interface KGNode {
  id: string;
  type: string;
  label: string;
  file: string;
  qualname: string;
  color: string;
  attributes: Record<string, unknown>;
}

/** One edge from the same export. */
export interface KGEdge {
  source: string;
  target: string;
  type: string;
  weight: number;
  provenance: string;
  evidence: string;
}

/** `GET /api/knowledge/{runId}/export/{view}?fmt=json` — a capped subgraph. */
export interface KnowledgeGraphExport {
  name: string;
  truncated: boolean;
  total_nodes: number;
  nodes: KGNode[];
  edges: KGEdge[];
}

export interface WorkspacePackage {
  name: string;
  path: string;
}

/** `GET /api/knowledge/{runId}/metrics`. */
export interface KnowledgeMetrics {
  repository_hash: string;
  node_count: number;
  edge_count: number;
  average_degree: number;
  nodes_by_type: Record<string, number>;
  edges_by_type: Record<string, number>;
  graph_build_ms: number;
  incremental_update_ms: number;
  index_build_ms: number;
  query_count: number;
  average_query_ms: number;
  cache_hit_rate: number;
  cache_hits: number;
  cache_misses: number;
  memory_bytes: number;
  index_size_bytes: number;
  repository_coverage: number;
  files_total: number;
  files_represented: number;
  workspace: {
    kind: string;
    packages: WorkspacePackage[];
    nested_repositories: string[];
    languages: Record<string, number>;
  };
}

/** One entry from `GET /api/knowledge/{runId}/capabilities`. */
export interface KnowledgeCapability {
  name: string;
  slug: string;
  confidence: number;
  files: string[];
  entry_points: string[];
  signals: Record<string, number>;
  why: string;
  evidence: string[];
}

/** One entry from `GET /api/knowledge/{runId}/hotspots`. */
export interface KnowledgeHotspot {
  kind: string;
  target: string;
  severity: number;
  summary: string;
  members: string[];
  why: string;
  evidence: string[];
}

/** One result row from `GET /api/knowledge/{runId}/query/{name}`. */
export interface KnowledgeQueryResultItem {
  id: string;
  type: string;
  name: string;
  file: string;
  qualname: string;
  weight?: number;
}

export interface KnowledgeQueryResult {
  query: string;
  file: string | null;
  qualname: string | null;
  count: number;
  results: KnowledgeQueryResultItem[];
  query_ms: number;
}
