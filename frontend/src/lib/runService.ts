/**
 * Run service — single seam between UI and data source.
 *
 * Today every function returns the bundled mock fixture. To swap to a real
 * backend, set VITE_DATA_SOURCE=api in .env. No UI component should ever
 * need to change.
 */
import { DATA_SOURCE, ENDPOINTS, apiFetch, isStatus } from "./api";
import {
  createMockEventSource,
  type EventSourceFactory,
} from "@/components/proofix/mockEventStream";
import { createLiveEventSource } from "@/components/proofix/liveEventStream";
import {
  MOCK_REPOSITORIES,
  MOCK_WORKSPACE_HEADER,
  MOCK_EXECUTIVE_SUMMARY,
  MOCK_REPAIR_ATTEMPTS,
  AGENTS,
  type RunReportModel,
  type WorkspaceHeaderModel,
  type ExecutiveSummaryModel,
  type RepairAttemptsModel,
  type RepoMetadata,
  type ContextPackageModel,
  type PatchBundleModel,
} from "@/mocks";
import type { SidebarRepo } from "@/components/proofix/Sidebar";
import type { AgentEntry } from "@/components/proofix/data";
import type {
  KnowledgeGraphExport,
  KnowledgeMetrics,
  KnowledgeCapability,
  KnowledgeHotspot,
  KnowledgeQueryResult,
} from "@/components/proofix/knowledgeGraphTypes";
import type { SemanticGraphExport } from "@/components/proofix/semanticGraphTypes";
import type { DependencyRiskReport } from "@/components/proofix/dependencyRiskTypes";
import type { StaticFindingsReport } from "@/components/proofix/staticFindingsTypes";
import type { SecurityRescanReport } from "@/components/proofix/securityRescanTypes";
import type { MutationValidationReport } from "@/components/proofix/mutationValidationTypes";
import type { MergeabilityDecision } from "@/components/proofix/mergeabilityTypes";
import type { ReproductionEvidence } from "@/components/proofix/reproductionTypes";
import type { InvestigationReport } from "@/components/proofix/investigationTypes";
import type { BlastImpact } from "@/components/proofix/blastTypes";
import type { RepairPlan } from "@/components/proofix/repairPlanTypes";

/** True when the UI is talking to a real backend rather than fixtures. */
export const isLive = DATA_SOURCE === "api";

/**
 * Start a pipeline run. Accepts a GitHub URL or a local repository path —
 * the backend resolves either.
 */
export type IssueInput = {
  description: string;
  targetTest?: string;
  expectedBehavior?: string;
  targetFile?: string;
};

export async function createRun(target: string, issue?: IssueInput): Promise<string> {
  if (DATA_SOURCE === "api") {
    const { run_id } = await apiFetch<{ run_id: string }>(ENDPOINTS.runs(), {
      method: "POST",
      json: {
        url: target,
        repo_path: target,
        ...(issue && {
          issue_hint: issue.description,
          target_test: issue.targetTest || null,
          expected_behavior: issue.expectedBehavior || null,
          target_file: issue.targetFile || null,
        }),
      },
    });
    return run_id;
  }
  return `run-${Date.now()}`;
}

/**
 * Event source for the execution journal: the backend WebSocket when live,
 * the bundled timing mock otherwise.
 */
export function eventSourceFor(runId: string): EventSourceFactory {
  return DATA_SOURCE === "api" ? createLiveEventSource(runId) : createMockEventSource;
}

/** Parse a GitHub URL into owner/name. Pure client-side gating only. */
export function parseGithubUrl(raw: string): { owner: string; name: string } | null {
  const s = raw.trim();
  if (!s) return null;
  try {
    const u = new URL(s.startsWith("http") ? s : `https://${s}`);
    if (!u.hostname.includes("github.com")) return null;
    const [owner, repoRaw] = u.pathname.split("/").filter(Boolean);
    if (!owner || !repoRaw) return null;
    return { owner, name: repoRaw.replace(/\.git$/, "") };
  } catch {
    return null;
  }
}

export async function listRepositories(): Promise<SidebarRepo[]> {
  if (DATA_SOURCE === "api") {
    return apiFetch<SidebarRepo[]>(ENDPOINTS.repositories());
  }
  return MOCK_REPOSITORIES;
}

/** Reports are supplied by the backend; mock mode has no report. */
export async function getRunReport(runId: string): Promise<RunReportModel | null> {
  if (DATA_SOURCE === "api") {
    return apiFetch<RunReportModel>(ENDPOINTS.runReport(runId));
  }
  return null;
}

export async function getWorkspaceHeader(runId: string): Promise<WorkspaceHeaderModel> {
  if (DATA_SOURCE === "api") {
    return apiFetch<WorkspaceHeaderModel>(ENDPOINTS.run(runId));
  }
  return MOCK_WORKSPACE_HEADER;
}

export async function getExecutiveSummary(runId: string): Promise<ExecutiveSummaryModel> {
  if (DATA_SOURCE === "api") {
    return apiFetch<ExecutiveSummaryModel>(ENDPOINTS.runSummary(runId));
  }
  return MOCK_EXECUTIVE_SUMMARY;
}

export async function getRunAgents(runId: string): Promise<AgentEntry[]> {
  if (DATA_SOURCE === "api") {
    return apiFetch<AgentEntry[]>(ENDPOINTS.runAgents(runId));
  }
  return AGENTS;
}

export async function getRepairAttempts(runId: string): Promise<RepairAttemptsModel> {
  if (DATA_SOURCE === "api") {
    return apiFetch<RepairAttemptsModel>(ENDPOINTS.retryAttempts(runId));
  }
  return MOCK_REPAIR_ATTEMPTS;
}

/**
 * A5.5's context package, or `null` when the stage has not produced one.
 *
 * The endpoint 404s until A5.5 completes and resolves a target, and that 404 is
 * an answer, not a failure — "no package yet", which the caller renders as
 * absence. Every other status still rejects, so a real outage is still an
 * outage. Collapsing the two would put "Could not load. Retry" on screen for a
 * stage that simply had not run, which is the same class of lie as rendering a
 * failed fetch as an empty one (B-F01).
 *
 * Mock mode has no fixture: inventing a ranking and a redaction list would put
 * fabricated privacy evidence on screen, which is the one thing this card
 * exists to make trustworthy.
 */
export async function getRunContext(runId: string): Promise<ContextPackageModel | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<ContextPackageModel>(ENDPOINTS.runContext(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A7's patch bundle, or `null` when it produced none.
 *
 * Same 404-is-an-answer contract as `getRunContext`. Unlike the context
 * package this is *not* write-once: A7 regenerates on every validation retry,
 * so the bundle genuinely changes while a run is in flight.
 */
export async function getRunPatch(runId: string): Promise<PatchBundleModel | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<PatchBundleModel>(ENDPOINTS.runPatch(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * Validate a repository URL. The frontend never talks to GitHub directly —
 * this goes through the ProoFix backend, which performs auth, rate-limiting
 * and metadata enrichment server-side.
 */
export async function validateRepository(url: string): Promise<RepoMetadata | null> {
  if (DATA_SOURCE === "api") {
    try {
      return await apiFetch<RepoMetadata>(ENDPOINTS.repoValidate(), {
        method: "POST",
        json: { url },
      });
    } catch (reason) {
      if (isStatus(reason, 404)) return null;
      throw reason;
    }
  }
  // Mock: synthesise metadata from the URL without any network call.
  const parsed = parseGithubUrl(url);
  if (!parsed) return null;
  return {
    owner: parsed.owner,
    name: parsed.name,
    language: null,
    branch: "main",
    visibility: "Public",
    htmlUrl: `https://github.com/${parsed.owner}/${parsed.name}`,
  };
}

/**
 * A1's Semantic Intent Graph — architectural role per file. 404s the same way
 * `getRunContext` does: A1 has not published a SIG for this run yet, which is
 * an answer, not a failure. Mock mode has no fixture — this is real role
 * classification over the actual repository, and there is nothing honest to
 * invent for a demo run.
 */
export async function getSemanticGraph(runId: string): Promise<SemanticGraphExport | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<SemanticGraphExport>(ENDPOINTS.runSemanticGraph(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A2's dependency/CVE reachability report. 404s the same way `getRunContext`
 * does: A2 has not completed for this run yet, which is an answer, not a
 * failure. Mock mode has no fixture — this is real OSV advisory data narrowed
 * by real import-graph reachability, and there is nothing honest to invent
 * for a demo run.
 */
export async function getDependencyRisk(runId: string): Promise<DependencyRiskReport | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<DependencyRiskReport>(ENDPOINTS.runDependencyRisk(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A3's static-analysis findings. 404s the same way `getRunContext` does: A3
 * has not completed for this run yet, which is an answer, not a failure.
 * Mock mode has no fixture — this is real scanner output and real ranking,
 * and there is nothing honest to invent for a demo run.
 */
export async function getStaticFindings(runId: string): Promise<StaticFindingsReport | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<StaticFindingsReport>(ENDPOINTS.runStaticFindings(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A9's post-patch security re-scan. 404s the same way `getStaticFindings`
 * does: A9 has not completed for this run yet, which is an answer, not a
 * failure. Mock mode has no fixture — this is a real differential scan
 * against a real patched repository, and there is nothing honest to invent
 * for a demo run.
 */
export async function getSecurityRescan(runId: string): Promise<SecurityRescanReport | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<SecurityRescanReport>(ENDPOINTS.runSecurity(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A8's scoped-validation + mutation gauntlet. 404s the same way
 * `getStaticFindings` does: A8 has not completed for this run yet, which is
 * an answer, not a failure. Mock mode has no fixture — this is a real
 * mutmut/pytest run against a real patched repository, and there is nothing
 * honest to invent for a demo run.
 */
export async function getMutationValidation(
  runId: string,
): Promise<MutationValidationReport | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<MutationValidationReport>(ENDPOINTS.runMutation(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A10's mergeability routing decision. 404s the same way `getSecurityRescan`
 * does: A10 has not completed for this run yet, which is an answer, not a
 * failure. Mock mode has no fixture — this is the real router's own gate
 * trace and PR outcome, and there is nothing honest to invent for a demo run.
 */
export async function getMergeabilityDecision(runId: string): Promise<MergeabilityDecision | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<MergeabilityDecision>(ENDPOINTS.runDecision(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A3.5's failure-reproduction evidence. 404s the same way `getRunContext`
 * does: A3.5 has not completed for this run yet, which is an answer, not a
 * failure. Mock mode has no fixture — this is real pytest execution evidence,
 * and there is nothing honest to invent for a demo run.
 */
export async function getReproductionEvidence(runId: string): Promise<ReproductionEvidence | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<ReproductionEvidence>(ENDPOINTS.runReproduction(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A4's evidence investigation. 404s the same way `getRunContext` does: A4 has
 * not completed for this run yet, which is an answer, not a failure. Mock mode
 * has no fixture — this is a correlation over real scanner, runtime and
 * citation evidence, and there is nothing honest to invent for a demo run.
 *
 * A 200 carrying `status: "no_finding"` is a different fact from a 404: A4 ran,
 * consulted its sources and had no subject to investigate.
 */
export async function getInvestigation(runId: string): Promise<InvestigationReport | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<InvestigationReport>(ENDPOINTS.runInvestigation(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A5's blast-radius impact. 404s the same way `getInvestigation` does: A5 has
 * not completed for this run yet, which is an answer, not a failure. Mock mode
 * has no fixture — this is a real traversal over the repository's own import
 * graph, and there is nothing honest to invent for a demo run.
 *
 * A 200 with `origin: null` is a different fact from a 404: A5 ran and
 * resolved no origin to traverse from.
 */
export async function getBlastImpact(runId: string): Promise<BlastImpact | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<BlastImpact>(ENDPOINTS.runBlast(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A6's repair plan. 404s the same way `getBlastImpact` does: A6 has not
 * completed for this run yet, which is an answer, not a failure. Mock mode has
 * no fixture — this is a real join over A3/A2/A5.5's own evidence, and there
 * is nothing honest to invent for a demo run.
 */
export async function getRepairPlan(runId: string): Promise<RepairPlan | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<RepairPlan>(ENDPOINTS.runRepairPlan(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/**
 * A0.5's Repository Knowledge Graph. All four 404 the same way `getRunContext`
 * does — the layer is disabled or has not published for this run yet, which is
 * an answer, not a failure — and mock mode has no fixture: this graph is real
 * indexed structure, and there is nothing honest to invent for a demo run.
 */
export async function getKnowledgeMetrics(runId: string): Promise<KnowledgeMetrics | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<KnowledgeMetrics>(ENDPOINTS.knowledgeMetrics(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

export async function getKnowledgeGraph(
  runId: string,
  view: string,
  maxNodes: number,
): Promise<KnowledgeGraphExport | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<KnowledgeGraphExport>(ENDPOINTS.knowledgeExport(runId, view, maxNodes));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

export async function getKnowledgeCapabilities(
  runId: string,
): Promise<KnowledgeCapability[] | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<KnowledgeCapability[]>(ENDPOINTS.knowledgeCapabilities(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

export async function getKnowledgeHotspots(runId: string): Promise<KnowledgeHotspot[] | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<KnowledgeHotspot[]>(ENDPOINTS.knowledgeHotspots(runId));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

/** One named, deterministic traversal (`functions_in_file`, `owners_of`, …). */
export async function getKnowledgeQuery(
  runId: string,
  name: string,
  params: Record<string, string>,
): Promise<KnowledgeQueryResult | null> {
  if (DATA_SOURCE !== "api") return null;
  try {
    return await apiFetch<KnowledgeQueryResult>(ENDPOINTS.knowledgeQuery(runId, name, params));
  } catch (reason) {
    if (isStatus(reason, 404)) return null;
    throw reason;
  }
}

export async function askChat(runId: string, question: string): Promise<string> {
  if (DATA_SOURCE === "api") {
    const { answer } = await apiFetch<{ answer: string }>(ENDPOINTS.runChat(runId), {
      method: "POST",
      json: { question },
    });
    return answer;
  }
  const { mockAnswerer } = await import("@/mocks/chat");
  return mockAnswerer(question);
}
