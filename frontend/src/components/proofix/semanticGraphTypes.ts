/**
 * Types for A1's Semantic Intent Graph endpoint
 * (`GET /api/runs/{runId}/semantic-graph`, `services/ui_projection.py::build_semantic_graph`).
 *
 * This is A1's own artifact and is distinct from A0.5's Repository Knowledge
 * Graph (`knowledgeGraphTypes.ts`): A0.5 answers "what exists and how is it
 * wired" — structural containment, call graph, git ownership. A1 answers
 * "what does this code appear to do" — one architectural role per file. The
 * six roles below are the complete set A1 can assign
 * (`backend/models/sig.py::FileRole`); nothing here is invented client-side.
 */

export type SemanticRole =
  "auth-boundary" | "data-access" | "public-api" | "config-surface" | "test-only" | "internal-util";

export const SEMANTIC_ROLES: SemanticRole[] = [
  "auth-boundary",
  "data-access",
  "public-api",
  "config-surface",
  "test-only",
  "internal-util",
];

/** One file A1 classified. */
export interface SemanticFile {
  path: string;
  role: SemanticRole;
  imports: string[];
  importedBy: string[];
  churnWeight: number;
  criticality: number;
}

/** `GET /api/runs/{runId}/semantic-graph`. */
export interface SemanticGraphExport {
  generatedAt: string | null;
  sourceRoots: string[];
  files: SemanticFile[];
  edges: [string, string][];
  roleCounts: Record<SemanticRole, number>;
  totalFiles: number;
  totalEdges: number;
}
