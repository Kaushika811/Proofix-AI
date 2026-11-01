/**
 * Centralized API configuration.
 *
 * Reads from Vite env so a single env change swaps every screen from the
 * bundled mock data source to a real backend. No hardcoded localhost URLs.
 */
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export type DataSource = "mock" | "api";

export const DATA_SOURCE: DataSource =
  ((import.meta.env.VITE_DATA_SOURCE as string | undefined) ?? "mock") === "api" ? "api" : "mock";

/**
 * Endpoint registry. Keep every backend path in one place so swapping the
 * backend never requires hunting through component files.
 */
export const ENDPOINTS = {
  repositories: () => `/api/repositories`,
  repoValidate: () => `/api/repositories/validate`,
  runs: () => `/api/runs`,
  run: (runId: string) => `/api/runs/${encodeURIComponent(runId)}`,
  runEvents: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/events`,
  runChat: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/chat`,
  runReport: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/report`,
  runDraftPr: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/draft-pr`,
  runAgents: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/agents`,
  runSummary: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/summary`,
  retryAttempts: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/attempts`,
  runContext: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/context`,
  runPlan: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/plan`,
  runPatch: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/patch`,
  // A1's Semantic Intent Graph — read-only, projected from `state.sig`.
  // Distinct from the `knowledge*` endpoints below, which are A0.5's.
  runSemanticGraph: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/semantic-graph`,
  // A2's dependency/CVE reachability report — read-only, projected from
  // `state.cve_report`.
  runDependencyRisk: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/dependency-risk`,
  // A3's static-analysis findings — read-only, projected from
  // `state.static_report`.
  runStaticFindings: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/static-findings`,
  // A3.5's failure-reproduction evidence — read-only, projected from
  // `state.reproduction`.
  runReproduction: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/reproduction`,
  // A9's post-patch security re-scan — read-only, projected from
  // `state.security_result`.
  runSecurity: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/security`,
  // A8's scoped-validation + mutation gauntlet — read-only, projected from
  // `state.mutation_result`.
  runMutation: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/mutation`,
  // A10's mergeability routing decision — read-only, projected from
  // `state.pr_decision` and `state.proof_bundle`.
  runDecision: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/decision`,
  // A4's evidence investigation — read-only, projected from
  // `state.investigation`.
  runInvestigation: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/investigation`,
  // A5's blast-radius impact — read-only, projected from `state.blast_graph`,
  // joined against A1's SIG and A3's findings.
  runBlast: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/blast`,
  // A6's repair plan — read-only, projected from `state.fix_dag`, joined
  // against A3's findings, A2's CVE records and A5.5's context package.
  // Distinct from `runPlan` below, which returns `state.fix_dag` verbatim.
  runRepairPlan: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/repair-plan`,
  // A0.5's Repository Knowledge Graph — read-only, derived on demand from the
  // cached index (`backend/api/routes/knowledge.py`). Never mutated, safe to
  // call at any point in a run's life.
  knowledgeMetrics: (runId: string) => `/api/knowledge/${encodeURIComponent(runId)}/metrics`,
  knowledgeCapabilities: (runId: string) =>
    `/api/knowledge/${encodeURIComponent(runId)}/capabilities`,
  knowledgeHotspots: (runId: string) => `/api/knowledge/${encodeURIComponent(runId)}/hotspots`,
  knowledgeExport: (runId: string, view: string, maxNodes: number) =>
    `/api/knowledge/${encodeURIComponent(runId)}/export/${encodeURIComponent(view)}?fmt=json&max_nodes=${maxNodes}`,
  knowledgeQuery: (runId: string, name: string, params: Record<string, string>) =>
    `/api/knowledge/${encodeURIComponent(runId)}/query/${encodeURIComponent(name)}?${new URLSearchParams(params).toString()}`,
  /** Sarvam AI Speech-to-Text — accepts multipart/form-data with an `audio` field. */
  speechToText: () => `/api/v1/speech-to-text`,
} as const;

export interface FetcherOptions extends RequestInit {
  /** Optional path under API_BASE_URL. If `url` is absolute it is used as-is. */
  json?: unknown;
}

/**
 * A non-OK response, carrying the status the caller needs to interpret it.
 *
 * Some 404s are failures and some are facts. `/runs/{id}/context` 404s until
 * A5.5 has produced a package — that is the stage not having run, not the
 * request having gone wrong, and rendering "Could not load context. Retry" over
 * it would be the UI reporting a problem that does not exist. Callers that know
 * which is which need the status, and a message string cannot be parsed for it
 * without inventing a contract.
 *
 * The message is unchanged (`API 404: Not Found`) so every existing consumer
 * and test that reads `.message` keeps working.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, statusText: string) {
    super(`API ${status}: ${statusText}`);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Whether a rejection was this exact HTTP status. */
export function isStatus(reason: unknown, status: number): boolean {
  return reason instanceof ApiError && reason.status === status;
}

export async function apiFetch<T = unknown>(url: string, options: FetcherOptions = {}): Promise<T> {
  const { json, headers, ...rest } = options;
  const full = /^https?:/i.test(url) ? url : `${API_BASE_URL}${url}`;
  const response = await fetch(full, {
    ...rest,
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      ...(headers ?? {}),
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return (await response.json()) as T;
}
