/**
 * Fetches every view model for a run through the `runService` seam.
 *
 * In mock mode the service returns fixtures synchronously, so the workspace
 * renders identically with no backend running. In API mode the models are
 * polled while the run is live so header, summary and report stay current as
 * agents progress.
 *
 * Each model carries its own `{data, error, loading}` (B-F01, B-F08). The
 * previous version awaited a single `Promise.allSettled` and read only the
 * fulfilled results, so a rejected `/report` was indistinguishable from a run
 * that produced none: the panel rendered the empty model and said nothing. That
 * is the same defect class as the fabricated values already removed from this
 * codebase — the UI asserting a fact it does not have. A failed fetch is now a
 * reportable state with a retry, and one model failing never blanks the others.
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import {
  getExecutiveSummary,
  getRepairAttempts,
  getRunAgents,
  getRunContext,
  getRunPatch,
  getRunReport,
  getWorkspaceHeader,
  isLive,
  listRepositories,
} from "@/lib/runService";
import type { AgentEntry } from "./data";
import type { SidebarRepo } from "./Sidebar";
import {
  EMPTY_AGENTS,
  EMPTY_EXECUTIVE_SUMMARY,
  EMPTY_REPAIR_ATTEMPTS,
  EMPTY_WORKSPACE_HEADER,
} from "./emptyModels";
import {
  AGENTS as MOCK_AGENTS,
  MOCK_EXECUTIVE_SUMMARY,
  MOCK_REPAIR_ATTEMPTS,
  MOCK_REPOSITORIES,
  MOCK_WORKSPACE_HEADER,
  type ContextPackageModel,
  type PatchBundleModel,
  type ExecutiveSummaryModel,
  type RepairAttemptsModel,
  type RunReportModel,
  type WorkspaceHeaderModel,
} from "@/mocks";

const POLL_INTERVAL_MS = 2500;

/** Fixture run id — requesting it from a real backend would always 404. */
const MOCK_RUN_ID = "vulnapi-live";

/** The run-scoped models, each independently fetchable and independently failable. */
export type RunModelKey =
  "agents" | "header" | "summary" | "report" | "attempts" | "context" | "patch";

/**
 * Load state for one model.
 *
 * `error` and `data` are not exclusive. A poll that fails after a successful
 * one keeps the last good data on screen and raises the error beside it —
 * blanking a panel because the newest refresh failed loses information the user
 * already had.
 */
export interface ModelState {
  error: string | null;
  loading: boolean;
  /**
   * True once this model has been fetched successfully at least once, so a
   * panel can distinguish "failed and I have nothing to show" from "failed but
   * what is on screen is real, just stale".
   */
  loaded: boolean;
}

const PENDING: ModelState = { error: null, loading: true, loaded: false };
const SETTLED: ModelState = { error: null, loading: false, loaded: true };

function messageOf(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  return typeof reason === "string" && reason ? reason : "Request failed";
}

/**
 * Replace state only when the payload actually changed, serialising once.
 *
 * Each poll deserialises fresh objects, so a plain `setAgents` would hand the
 * execution journal a new array identity every 2.5s. `useExecutionRun`
 * re-subscribes its event source whenever `agents` changes, which would tear
 * down and replay the live stream mid-animation. So the comparison has to be
 * structural — reference equality always differs here and would be useless.
 *
 * The previous version stringified *both* sides on every comparison, and the
 * previous value has by definition not changed since it was accepted, so it was
 * re-serialised every 2.5 seconds, for every model, for the life of the run
 * (B-F10). Remembering the string we accepted removes that half entirely.
 *
 * The comparison itself stays structural. Reference equality would be useless
 * here: every poll deserialises fresh objects, so identity always differs and
 * the execution journal would re-subscribe its event source on every tick.
 */
function makeChangeGate() {
  const lastSerialized = new Map<string, string>();

  return function setIfChangedCached<T>(
    key: RunModelKey | "repositories",
    setter: Dispatch<SetStateAction<T>>,
    next: T,
  ) {
    const serialized = JSON.stringify(next);
    if (lastSerialized.get(key) === serialized) return;
    lastSerialized.set(key, serialized);
    setter(next);
  };
}

export interface RunData {
  agents: AgentEntry[];
  /**
   * A5.5's context package, or `null` when the stage has not produced one.
   *
   * Null is a real answer here, not a placeholder: the endpoint 404s until A5.5
   * completes and resolves a target. `status.context.loaded` distinguishes
   * "asked, and there is none" from "not asked yet".
   */
  context: ContextPackageModel | null;
  /**
   * A7's patch bundle, or `null` when it produced none.
   *
   * Unlike `context` this is re-fetched every poll: A7 regenerates on each
   * validation retry, so the diff on screen genuinely changes while a run is in
   * flight.
   */
  patch: PatchBundleModel | null;
  header: WorkspaceHeaderModel;
  summary: ExecutiveSummaryModel;
  report: RunReportModel | null;
  attempts: RepairAttemptsModel;
  repositories: SidebarRepo[];
  setRepositories: (next: SidebarRepo[] | ((prev: SidebarRepo[]) => SidebarRepo[])) => void;
  /** Re-fetch the sidebar from the backend, e.g. after starting a run. */
  refreshRepositories: () => void;
  /** Per-model load state. Panels read their own entry to show error/skeleton. */
  status: Record<RunModelKey, ModelState>;
  /**
   * Re-fetch every run-scoped model now. Bound to the retry control that
   * appears whenever a panel is in error — including after the run has settled,
   * where polling has stopped and a retry is the only way to recover.
   */
  retry: () => void;
  /** True until the first load of a fresh run resolves, for the page-level splash. */
  loading: boolean;
}

export function useRunData(runId: string, done: boolean): RunData {
  // Live mode starts blank: every value on screen must have come from the
  // backend. Mock mode starts from the fixtures, which are the whole point.
  const [agents, setAgents] = useState<AgentEntry[]>(isLive ? EMPTY_AGENTS : MOCK_AGENTS);
  const [header, setHeader] = useState<WorkspaceHeaderModel>(
    isLive ? EMPTY_WORKSPACE_HEADER : MOCK_WORKSPACE_HEADER,
  );
  const [summary, setSummary] = useState<ExecutiveSummaryModel>(
    isLive ? EMPTY_EXECUTIVE_SUMMARY : MOCK_EXECUTIVE_SUMMARY,
  );
  const [report, setReport] = useState<RunReportModel | null>(null);
  const [attempts, setAttempts] = useState<RepairAttemptsModel>(
    isLive ? EMPTY_REPAIR_ATTEMPTS : MOCK_REPAIR_ATTEMPTS,
  );
  const [context, setContext] = useState<ContextPackageModel | null>(null);
  const [patch, setPatch] = useState<PatchBundleModel | null>(null);
  const [repositories, setRepositories] = useState<SidebarRepo[]>(isLive ? [] : MOCK_REPOSITORIES);

  const [status, setStatus] = useState<Record<RunModelKey, ModelState>>(() => {
    const initial = isLive ? PENDING : SETTLED;
    return {
      agents: initial,
      header: initial,
      summary: initial,
      report: initial,
      attempts: initial,
      context: initial,
      patch: initial,
    };
  });

  const [repoRefreshToken, setRepoRefreshToken] = useState(0);
  const refreshRepositories = useCallback(() => setRepoRefreshToken((t) => t + 1), []);

  const [retryToken, setRetryToken] = useState(0);
  const retry = useCallback(() => setRetryToken((t) => t + 1), []);

  // Only the *first* load of a given run shows the page-level splash. Later
  // polls and retries update in place; swapping the whole workspace back to a
  // splash every 2.5s would be worse than showing slightly stale data.
  const firstLoadDone = useRef(false);
  // Holds the package once fetched, so later polls can skip the request.
  const contextRef = useRef<ContextPackageModel | null>(null);
  // Per-run cache of the last payload accepted for each model (B-F10).
  const changed = useRef(makeChangeGate());
  const [loading, setLoading] = useState(isLive);

  // Repository list — refreshed when a run finishes so statuses stay accurate.
  useEffect(() => {
    if (!isLive) return;
    let cancelled = false;
    listRepositories()
      .then((repos) => {
        // Set even when empty — a backend with no runs must render as none,
        // not as whatever the previous fetch happened to return.
        if (!cancelled) changed.current("repositories", setRepositories, repos);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [done, repoRefreshToken]);

  // A fresh run id is a fresh load: re-arm the splash.
  useEffect(() => {
    firstLoadDone.current = false;
    contextRef.current = null;
    changed.current = makeChangeGate();
    setReport(null);
    setContext(null);
    setPatch(null);
  }, [runId]);

  // Run-scoped models. Polled while the run is in flight, fetched once after.
  useEffect(() => {
    if (!isLive || !runId || runId === MOCK_RUN_ID) {
      setLoading(false);
      setStatus({
        agents: SETTLED,
        header: SETTLED,
        summary: SETTLED,
        report: SETTLED,
        attempts: SETTLED,
        context: SETTLED,
        patch: SETTLED,
      });
      return;
    }

    let cancelled = false;

    const load = async () => {
      // Mark loading only on the first pass. Flagging every poll as `loading`
      // would make panels flicker into skeletons twice a second.
      if (!firstLoadDone.current) {
        setStatus((prev) => ({
          agents: { ...prev.agents, loading: true },
          header: { ...prev.header, loading: true },
          summary: { ...prev.summary, loading: true },
          report: { ...prev.report, loading: true },
          attempts: { ...prev.attempts, loading: true },
          context: { ...prev.context, loading: true },
          patch: { ...prev.patch, loading: true },
        }));
      }

      const results = await Promise.allSettled([
        getRunAgents(runId),
        getWorkspaceHeader(runId),
        getExecutiveSummary(runId),
        getRepairAttempts(runId),
        getRunReport(runId),
        // Write-once: A5.5 runs a single time per run, so once a package has
        // been read there is nothing to re-poll. Skipping the request keeps the
        // poll from growing by a round trip for an answer that cannot change.
        contextRef.current ? Promise.resolve(contextRef.current) : getRunContext(runId),
        getRunPatch(runId),
      ]);
      if (cancelled) return;

      const [agentsRes, headerRes, summaryRes, attemptsRes, reportRes, contextRes, patchRes] =
        results;

      // An empty agent list is not an answer worth adopting — the backend
      // returns one before the registry is populated — but it is not a failure
      // either, so the model settles clean.
      if (agentsRes.status === "fulfilled" && agentsRes.value.length)
        changed.current("agents", setAgents, agentsRes.value);
      if (headerRes.status === "fulfilled") changed.current("header", setHeader, headerRes.value);
      if (summaryRes.status === "fulfilled")
        changed.current("summary", setSummary, summaryRes.value);
      if (attemptsRes.status === "fulfilled")
        changed.current("attempts", setAttempts, attemptsRes.value);
      if (reportRes.status === "fulfilled") changed.current("report", setReport, reportRes.value);
      if (contextRes.status === "fulfilled" && contextRes.value) {
        contextRef.current = contextRes.value;
        changed.current<ContextPackageModel | null>("context", setContext, contextRes.value);
      }
      // Adopted even when null: A7 having produced no bundle is an answer, and
      // keeping a previous run's diff on screen would be worse than blank.
      if (patchRes.status === "fulfilled")
        changed.current<PatchBundleModel | null>("patch", setPatch, patchRes.value);

      setStatus((prev) => {
        const settle = (key: RunModelKey, r: PromiseSettledResult<unknown>): ModelState =>
          r.status === "fulfilled"
            ? SETTLED
            : {
                error: messageOf((r as PromiseRejectedResult).reason),
                loading: false,
                // A failed refresh does not un-load data an earlier poll got.
                loaded: prev[key].loaded,
              };
        return {
          agents: settle("agents", agentsRes),
          header: settle("header", headerRes),
          summary: settle("summary", summaryRes),
          attempts: settle("attempts", attemptsRes),
          report: settle("report", reportRes),
          context: settle("context", contextRes),
          patch: settle("patch", patchRes),
        };
      });

      firstLoadDone.current = true;
      setLoading(false);
    };

    void load();
    if (done)
      return () => {
        cancelled = true;
      };

    const timer = setInterval(load, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [runId, done, retryToken]);

  return useMemo(
    () => ({
      agents,
      context,
      patch,
      header,
      summary,
      report,
      attempts,
      repositories,
      setRepositories,
      refreshRepositories,
      status,
      retry,
      loading,
    }),
    [
      agents,
      context,
      patch,
      header,
      summary,
      report,
      attempts,
      repositories,
      refreshRepositories,
      status,
      retry,
      loading,
    ],
  );
}
