import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Sidebar, type SidebarRepo, type SidebarRun } from "@/components/proofix/Sidebar";
import { RepairSummary } from "@/components/proofix/RepairSummary";
import { RunReport } from "@/components/proofix/RunReport";
import { AgentCard } from "@/components/proofix/AgentCard";
import { AgentRail } from "@/components/proofix/AgentRail";
import { ChatPanel } from "@/components/proofix/ChatPanel";
import { StatusBadge } from "@/components/proofix/StatusBadge";
import { AnimatedNumber } from "@/components/proofix/AnimatedNumber";

import { useExecutionRun, type LiveAgent } from "@/components/proofix/useExecutionRun";
import { EvidenceHandoff } from "@/components/proofix/AgentVisualization";
import { EnvironmentPreflightBoard } from "@/components/proofix/EnvironmentPreflightBoard";
import { RepositoryIntelligencePanel } from "@/components/proofix/RepositoryIntelligencePanel";
import { SemanticArchitecturePanel } from "@/components/proofix/SemanticArchitecturePanel";
import { DependencyRiskPanel } from "@/components/proofix/DependencyRiskPanel";
import { StaticFindingsPanel } from "@/components/proofix/StaticFindingsPanel";
import { SecurityRescanPanel } from "@/components/proofix/SecurityRescanPanel";
import { MergeabilityDecisionPanel } from "@/components/proofix/MergeabilityDecisionPanel";
import { MutationValidationPanel } from "@/components/proofix/MutationValidationPanel";
import { ReproductionEvidencePanel } from "@/components/proofix/ReproductionEvidencePanel";
import { EvidenceInvestigationBoard } from "@/components/proofix/EvidenceInvestigationBoard";
import { BlastRadiusPanel } from "@/components/proofix/BlastRadiusPanel";
import { RepairPlanPanel } from "@/components/proofix/RepairPlanPanel";
import { ContextEngineeringPanel } from "@/components/proofix/ContextEngineeringPanel";
import type { IntelligencePayload } from "@/components/proofix/visualizationTypes";
import { NewRunScreen } from "@/components/proofix/NewRunScreen";
import { AnalyzingSequence } from "@/components/proofix/AnalyzingSequence";

import { RetrySequence } from "@/components/proofix/RetrySequence";
import {
  DiffView,
  diffStats,
  parseUnifiedDiff,
  splitDiffByFile,
} from "@/components/proofix/DiffView";
import type { PatchFileProvenance } from "@/components/proofix/visualizationTypes";

import { ProgressRing } from "@/components/proofix/ProgressRing";
import {
  GitBranch,
  Hash,
  Clock,
  RefreshCcw,
  MessageSquare,
} from "lucide-react";
import { useChatConversations } from "@/components/proofix/run-chat/useChatConversations";
import { LatestChatExchange } from "@/components/proofix/run-chat/LatestChatExchange";
import { ChatHistoryDrawer } from "@/components/proofix/run-chat/ChatHistoryDrawer";
import { VsCodeSidebarRightIcon } from "@/components/proofix/run-chat/VsCodeSidebarRightIcon";
import {
  RUN_NAME_POOL,
  mockAnswerer,
  type WorkspaceHeaderModel,
  type RunReportModel,
  type PatchBundleModel,
  type PatchCandidateModel,
} from "@/mocks";
import { scrollBehavior } from "@/hooks/useCountUp";
import { useRunData, type ModelState } from "@/components/proofix/useRunData";
import { resolveRunLifecycle, type RunLifecycleView } from "@/components/proofix/runLifecycle";
import { askChat, createRun, eventSourceFor, isLive, type IssueInput } from "@/lib/runService";

type View = "execution" | "new-run" | "analyzing" | "settings" | "home" | "runs";

/** Placeholder id used by the bundled fixtures; never exists on a backend. */
const MOCK_RUN_ID = "vulnapi-live";

const ACTIVE_AGENT_ANCHOR_PX = 140;
const ACTIVE_AGENT_ANCHOR_MIN_PX = 120;
const ACTIVE_AGENT_ANCHOR_MAX_PX = 150;
const AGENT_LAYOUT_SETTLE_TIMEOUT_MS = 1400;

function getAgentHeader(index: number): HTMLElement | null {
  return document.querySelector<HTMLElement>(
    `[data-execution-agent-index="${index}"] [data-execution-agent-header="true"]`,
  );
}

function scrollAgentHeaderToAnchor(header: HTMLElement, behavior: ScrollBehavior) {
  const top = header.getBoundingClientRect().top + window.scrollY - ACTIVE_AGENT_ANCHOR_PX;
  window.scrollTo({ top: Math.max(0, top), behavior });
}

function nextFrame() {
  return new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
}

async function waitForTimelineLayoutToSettle() {
  await nextFrame();
  await nextFrame();

  const animations = document
    .getAnimations()
    .filter((animation) => {
      const timing = animation.effect?.getComputedTiming();
      if (!timing || timing.iterations === Infinity) return false;
      return animation.playState === "running";
    })
    .map((animation) => animation.finished.catch(() => undefined));

  if (animations.length === 0) return;

  await Promise.race([
    Promise.all(animations),
    new Promise<void>((resolve) => window.setTimeout(resolve, AGENT_LAYOUT_SETTLE_TIMEOUT_MS)),
  ]);

  await nextFrame();
}

function parseRepoName(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.pathname.split("/").filter(Boolean);
    return parts[1]?.replace(/\.git$/, "") || parts[0] || "repository";
  } catch {
    const m = url.match(/([^/]+?)(?:\.git)?\/?$/);
    return m?.[1] || "repository";
  }
}

export interface WorkspaceProps {
  /**
   * Run to display, from the `/runs/$runId` path. Absent on `/workspace`, which is the
   * landing route: live mode opens the new-run screen there rather than
   * silently resurrecting whichever run happened to finish last.
   */
  runId?: string;
}

export function Workspace({ runId: routeRunId }: WorkspaceProps = {}) {
  const navigate = useNavigate();
  const [view, setView] = useState<View>(() => (isLive && !routeRunId ? "new-run" : "execution"));
  // Single source of truth for the currently visible run. The route param wins
  // when present so a shared link opens exactly the run it names.
  const [runId, setRunId] = useState<string>(routeRunId ?? MOCK_RUN_ID);
  const [runDone, setRunDone] = useState(false);

  // View models come from the backend when live, fixtures otherwise.
  const runData = useRunData(runId, runDone);

  // The journal replays backend events over the live agent list; in mock mode
  // this falls back to the bundled timing source.
  const eventSource = useMemo(() => eventSourceFor(runId), [runId]);
  const {
    agents,
    activeIndex,
    restart,
    done: journalDone,
    settledState,
  } = useExecutionRun({
    agents: runData.agents,
    createEventSource: eventSource,
    seedStatusFromAgents: isLive,
  });

  // How the backend says this run ended. The journal's own `done` only knows
  // that its replay reached the end, which never happens for a run that stopped
  // early — the header stayed on `Status · Running` for a run blocked minutes
  // ago. Either source settling the run is enough.
  const lifecycle = useMemo(
    () =>
      resolveRunLifecycle({
        status: runData.header.status,
        lifecycle: runData.header.lifecycle,
        environment: runData.header.environment,
        decisionLabel: runData.header.decisionLabel,
      }),
    [runData.header],
  );

  // The run is over. Deliberately shadowing the old `done`: every place that
  // asked "has the journal finished playing?" actually meant "is the run
  // over?", and the two only ever agreed for a run that reached A10. A blocked
  // run answered no to the first forever.
  const done = journalDone || lifecycle.terminal;

  // Which ending, preferring whichever source has spoken. Mock mode has no
  // backend status, so its journal completing means completed.
  const runState = lifecycle.terminal ? lifecycle.state : (settledState ?? "running");

  useEffect(() => {
    setRunDone(done);
  }, [done]);

  /**
   * Which agent holds the stage.
   *
   * `null` means "follow the pipeline" — the stage tracks whichever agent is
   * executing, which is the default and the whole point of the layout. A number
   * means the user pulled an earlier stage up from the rail to re-read it; the
   * stage stops following until they come back, so the pipeline advancing
   * underneath cannot yank the card out from under them mid-read.
   */
  const [reviewIndex, setReviewIndex] = useState<number | null>(null);
  const reviewing = reviewIndex !== null && !done;
  const focusIndex = reviewing ? reviewIndex : activeIndex;

  // Per-card user overrides on top of the default expansion rule.
  // Default: only the currently-running agent is expanded.
  const [overrides, setOverrides] = useState<Record<number, boolean>>({});
  const toggleOverride = (i: number, defaultExpanded: boolean) => {
    setOverrides((prev) => {
      const current = prev[i] ?? defaultExpanded;
      return { ...prev, [i]: !current };
    });
  };

  // Follow the URL: back/forward between runs swaps the workspace's run.
  useEffect(() => {
    if (!routeRunId || routeRunId === runId) return;
    setRunId(routeRunId);
    setOverrides({});
    setReviewIndex(null);
    setView("execution");
  }, [routeRunId]);

  const { repositories, setRepositories, refreshRepositories } = runData;
  const [pendingRepo, setPendingRepo] = useState<string>("");
  const [pendingUrl, setPendingUrl] = useState("");
  const [launchError, setLaunchError] = useState<string | null>(null);

  // Against a live backend the fixture run id does not exist, and there is no
  // run to show until the URL names one. Landing on `/` means "start
  // something" — the sidebar is there for opening past runs deliberately.
  useEffect(() => {
    if (!isLive || routeRunId || runId !== MOCK_RUN_ID) return;
    setView("new-run");
  }, [routeRunId, runId]);
  const [showRunReport, setShowRunReport] = useState(false);
  const prevDone = useRef(false);
  const repairAttemptsRef = useRef<HTMLDivElement | null>(null);
  const pendingRunIdRef = useRef<Promise<string | null> | null>(null);
  const executiveSummaryRef = useRef<HTMLDivElement | null>(null);
  const chatAnchorRef = useRef<HTMLDivElement | null>(null);
  const [chatHistoryOpen, setChatHistoryOpen] = useState(false);
  const chatConversations = useChatConversations();
  const activeScrollGenerationRef = useRef(0);
  const anchoredAgentRef = useRef<number | null>(null);
  const primaryAlignUntilRef = useRef(0);

  useEffect(() => {
    if (done && !prevDone.current) {
      if (isLive) {
        // Re-read the sidebar: the real outcome may be merge, draft or failed,
        // and only the backend knows which. Hardcoding "draft" here reported a
        // verdict the pipeline never reached.
        refreshRepositories();
      } else {
        setRepositories((prev) =>
          prev.map((r) => ({
            ...r,
            runs: r.runs.map((run) =>
              run.id === runId ? { ...run, status: "draft" as const, time: "just now" } : run,
            ),
          })),
        );
      }
      // Failed and blocked runs end at their explanation, not the last agent.
      // Scroll to that summary first, then reveal the same report panel used by
      // successful runs. The report preserves the findings, failed attempts and
      // stop reason even when no safe patch or PR was produced.
      if (runState !== "completed") {
        setShowRunReport(false);
        executiveSummaryRef.current?.scrollIntoView({
          behavior: scrollBehavior(),
          block: "start",
        });
        const revealStoppedReport = window.setTimeout(() => {
          setShowRunReport(true);
        }, 650);
        prevDone.current = done;
        return () => window.clearTimeout(revealStoppedReport);
      }

      // End-of-run storytelling:
      //  1) 700ms pause, then gently scroll to Repair Attempts (git-history reveal).
      //  2) Attempts stagger in (~540ms) + 700ms breathing room.
      //  3) Smooth-scroll to the AI Executive Summary.
      //  4) Once the summary is in view, fade in the Run Report panel from the right.
      setShowRunReport(false);
      const REVEAL_ATTEMPTS_AT = 700;
      const SCROLL_TO_SUMMARY_AT = REVEAL_ATTEMPTS_AT + 540 + 700; // ~1940ms
      const REVEAL_REPORT_AT = SCROLL_TO_SUMMARY_AT + 650;

      const t1 = window.setTimeout(() => {
        repairAttemptsRef.current?.scrollIntoView({
          behavior: scrollBehavior(),
          block: "start",
        });
      }, REVEAL_ATTEMPTS_AT);
      const t2 = window.setTimeout(() => {
        const el = executiveSummaryRef.current;
        if (el) {
          const top = el.getBoundingClientRect().top + window.scrollY - 24;
          window.scrollTo({ top, behavior: scrollBehavior() });
        } else {
          window.scrollTo({ top: 0, behavior: scrollBehavior() });
        }
      }, SCROLL_TO_SUMMARY_AT);
      const t3 = window.setTimeout(() => {
        setShowRunReport(true);
      }, REVEAL_REPORT_AT);
      prevDone.current = done;
      return () => {
        window.clearTimeout(t1);
        window.clearTimeout(t2);
        window.clearTimeout(t3);
      };
    }
    if (!done) setShowRunReport(false);
    prevDone.current = done;
    // setRepositories is a stable useState setter, surfaced via useRunData.
  }, [done, runState, runId, setRepositories]);

  useEffect(() => {
    if (done || view !== "execution") return;

    const generation = activeScrollGenerationRef.current + 1;
    activeScrollGenerationRef.current = generation;
    anchoredAgentRef.current = null;

    let cancelled = false;

    waitForTimelineLayoutToSettle().then(() => {
      if (cancelled || activeScrollGenerationRef.current !== generation) return;
      const header = getAgentHeader(activeIndex);
      if (!header) return;
      scrollAgentHeaderToAnchor(header, "smooth");
      primaryAlignUntilRef.current = performance.now() + 750;

      window.setTimeout(() => {
        requestAnimationFrame(() => {
          if (cancelled || activeScrollGenerationRef.current !== generation) return;
          const settledHeader = getAgentHeader(activeIndex);
          if (!settledHeader) return;
          const { top } = settledHeader.getBoundingClientRect();
          if (top < ACTIVE_AGENT_ANCHOR_MIN_PX || top > ACTIVE_AGENT_ANCHOR_MAX_PX) {
            scrollAgentHeaderToAnchor(settledHeader, "smooth");
          }
          anchoredAgentRef.current = activeIndex;
        });
      }, 780);
    });

    return () => {
      cancelled = true;
    };
  }, [activeIndex, done, view]);

  // Streaming auto-follow: as the active agent grows (new lines, graphs,
  // visualizations), gently scroll the viewport downward so the newest
  // content stays visible — like ChatGPT / Cursor. Never pushes the header
  // above ~80px from the top; never scrolls up.
  useEffect(() => {
    if (done || view !== "execution") return;
    const article = document.querySelector<HTMLElement>(
      `[data-execution-agent-index="${activeIndex}"]`,
    );
    if (!article) return;

    let raf = 0;
    let lastBottom = 0;

    const follow = () => {
      raf = 0;
      if (anchoredAgentRef.current !== activeIndex) return;
      if (performance.now() < primaryAlignUntilRef.current) return;
      const rect = article.getBoundingClientRect();
      const vh = window.innerHeight;
      const bottomPadding = 140;
      const overflow = rect.bottom - (vh - bottomPadding);
      if (overflow <= 4) {
        lastBottom = rect.bottom;
        return;
      }
      // Only follow when the card is actually growing downward.
      const grew = rect.bottom - lastBottom > 0.5;
      lastBottom = rect.bottom;
      if (!grew) return;

      const header = getAgentHeader(activeIndex);
      const headerTop = header?.getBoundingClientRect().top ?? rect.top;
      const minHeaderTop = 72; // don't push header above this
      const maxDelta = Math.max(0, headerTop - minHeaderTop);
      const delta = Math.min(overflow, maxDelta);
      if (delta < 4) return;
      window.scrollBy({ top: delta, behavior: scrollBehavior() });
    };

    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(follow);
    };

    lastBottom = article.getBoundingClientRect().bottom;
    const observer = new ResizeObserver(schedule);
    observer.observe(article);

    return () => {
      observer.disconnect();
      if (raf) cancelAnimationFrame(raf);
    };
  }, [activeIndex, done, view]);

  const handleNewRun = () => {
    pendingRunIdRef.current = null;
    setLaunchError(null);
    setView("new-run");
    if (isLive) void navigate({ to: "/workspace" });
  };

  const handleAnalyze = (url: string, issue?: IssueInput) => {
    setLaunchError(null);
    setPendingUrl(url);
    setPendingRepo(parseRepoName(url));
    setView("analyzing");
    // Start the pipeline immediately so the backend works while the
    // analyzing animation plays; the id resolves before the journal opens.
    const request = createRun(url, issue).then((id) => {
      if (!id?.trim()) throw new Error("The server did not return a run ID.");
      return id;
    }).catch((error: unknown) => {
      if (pendingRunIdRef.current === request) {
        setLaunchError(error instanceof Error ? error.message : "Could not reach the backend.");
        setView("new-run");
      }
      return null;
    });
    pendingRunIdRef.current = request;
    return request;
  };

  const handleAnalyzeComplete = async () => {
    const repoName = pendingRepo || "repository";
    const request = pendingRunIdRef.current;
    const newRunId = await request;
    if (!newRunId || pendingRunIdRef.current !== request) return;

    if (isLive) {
      // Ask the backend what this run is called rather than inventing a name.
      // The sidebar entry — its name, status and age — is the backend's to
      // report; synthesising one here produced a run that looked real but
      // described nothing that actually happened.
      refreshRepositories();
    } else {
      const existing = repositories.find((r) => r.name === repoName);
      const runName = RUN_NAME_POOL[(existing?.runs.length ?? 0) % RUN_NAME_POOL.length];
      const newRun: SidebarRun = { id: newRunId, name: runName, status: "running", time: "now" };
      setRepositories((prev) => {
        const exists = prev.some((r) => r.name === repoName);
        if (exists) {
          return prev.map((r) => (r.name === repoName ? { ...r, runs: [newRun, ...r.runs] } : r));
        }
        return [{ name: repoName, runs: [newRun] }, ...prev];
      });
    }

    setRunId(newRunId);
    setOverrides({});
    setReviewIndex(null);
    setView("execution");
    restart();
    // Give the new run its own URL so it is shareable and survives a reload.
    if (isLive) void navigate({ to: "/runs/$runId", params: { runId: newRunId } });
  };

  const handleSelectRun = (selectedRunId: string) => {
    pendingRunIdRef.current = null;
    setRunId(selectedRunId);
    setOverrides({});
    setReviewIndex(null);
    setView("execution");
    if (isLive) void navigate({ to: "/runs/$runId", params: { runId: selectedRunId } });
  };

  const handleNavigate = (key: "home" | "runs" | "settings") => {
    pendingRunIdRef.current = null;
    if (key === "home") {
      void navigate({ to: "/" });
      return;
    }
    setView(key);
  };

  return (
    <div className="flex min-h-screen w-full bg-background">
      <Sidebar
        onNewRun={handleNewRun}
        onNavigate={handleNavigate}
        currentView={view}
        repositories={repositories}
        activeRunId={done ? null : runId}
        selectedRunId={runId}
        onSelectRun={handleSelectRun}
      />

      <main className="min-w-0 flex-1">
        {view === "new-run" && (
          <NewRunScreen onAnalyze={handleAnalyze} initialUrl={pendingUrl} launchError={launchError} />
        )}
        {view === "analyzing" && (
          <AnalyzingSequence
            repo={pendingRepo}
            onComplete={handleAnalyzeComplete}
            waitFor={isLive ? pendingRunIdRef.current : null}
          />
        )}
        {(view === "home" || view === "runs" || view === "settings") && <SimpleView label={view} />}
        {view === "execution" && isLive && runData.loading && <WorkspaceLoading />}
        {view === "execution" && !(isLive && runData.loading) && (
          <div className="mx-auto flex max-w-[1480px] gap-6 px-6 py-6">
            <div
              ref={chatAnchorRef}
              className={`min-w-0 flex-1 space-y-6 ${done ? "pb-[160px]" : "pb-24"}`}
            >
              {runData.status.header.error && (
                <PanelError
                  panel="run header"
                  message={runData.status.header.error}
                  onRetry={runData.retry}
                />
              )}
              <WorkspaceHeader
                done={done}
                header={runData.header}
                lifecycle={lifecycle}
                onOpenChatHistory={() => setChatHistoryOpen((prev) => !prev)}
                chatOpen={chatHistoryOpen}
              />
              <div ref={executiveSummaryRef} className="scroll-mt-6 space-y-3">
                {runData.status.summary.error && (
                  <PanelError
                    panel="executive summary"
                    message={runData.status.summary.error}
                    onRetry={runData.retry}
                  />
                )}
                <RepairSummary
                  onAttempts={() => repairAttemptsRef.current?.scrollIntoView({ behavior: scrollBehavior(), block: "start" })}
                  done={done}
                  summary={runData.summary}
                  report={runData.report}
                  header={runData.header}
                />
              </div>

              <section>
                <SectionHeading
                  eyebrow="Execution Journal"
                  title={done ? "Full execution timeline" : "Live execution timeline"}
                />

                {runData.status.agents.error && (
                  <div className="mt-4">
                    <PanelError
                      panel="the execution journal"
                      message={runData.status.agents.error}
                      onRetry={runData.retry}
                    />
                  </div>
                )}

                {/* Stages already passed. Mid-run this is the only way back to
                    them, since the stage below shows one agent at a time. */}
                <div className="mt-4">
                  <AgentRail
                    agents={agents}
                    activeIndex={activeIndex}
                    focusIndex={focusIndex}
                    onFocus={setReviewIndex}
                    done={done}
                  />
                </div>

                {reviewing && (
                  <button
                    type="button"
                    onClick={() => setReviewIndex(null)}
                    className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-status-running/40
                      bg-status-running-bg px-2.5 py-1 text-xs font-medium text-status-running
                      transition-transform duration-150 ease-out hover:-translate-y-px"
                  >
                    <RefreshCcw className="h-3 w-3" strokeWidth={2.5} />
                    Back to live stage
                  </button>
                )}

                <div className="mt-4 space-y-3">
                  {agents.map((entry, i) => {
                    // One agent holds the stage at a time: the one executing, or
                    // whichever the user pulled up from the rail. A settled run
                    // is a record rather than a performance, so it opens out and
                    // shows every stage at once.
                    if (!done && i !== focusIndex) return null;
                    const prev = agents[i - 1];
                    const prevDone =
                      prev &&
                      (prev.liveStatus === "completed" ||
                        prev.liveStatus === "draft" ||
                        prev.liveStatus === "failed");
                    const showHandoff = i > 0 && prevDone && i <= activeIndex;
                    const isActive = i === activeIndex && !done;
                    // Mid-run only one card is on screen, so it is always open;
                    // collapsing it would leave the stage empty.
                    const defaultExpanded = done ? isActive : true;
                    const expanded = overrides[i] ?? defaultExpanded;
                    return (
                      <div key={entry.id}>
                        {showHandoff && (
                          <div className="animate-fade-in">
                            <EvidenceHandoff
                              label={prev?.handoff ?? "evidence"}
                              toLabel={entry.handoff}
                              live={isActive}
                              active
                            />
                          </div>
                        )}

                        <div
                          className="animate-fade-in"
                          style={
                            i > 0 && i === activeIndex && !done
                              ? {
                                  animationDelay: "550ms",
                                  animationFillMode: "both",
                                }
                              : undefined
                          }
                        >
                          <AgentCard
                            entry={entry}
                            agentIndex={i}
                            active={isActive}
                            expanded={expanded}
                            outputLabel={entry.handoff}
                            collapseActivity
                            collapseMetrics
                            liveView={
                              entry.id === "environment" ? (
                                <EnvironmentPreflightBoard
                                  environment={runData.header.environment}
                                  probeError={runData.header.environmentProbeError}
                                />
                              ) : entry.id === "intelligence" ? (
                                <RepositoryIntelligencePanel
                                  runId={runId}
                                  intelligence={
                                    entry.visualization?.kind === "intelligence"
                                      ? (entry.visualization.data as IntelligencePayload)
                                      : undefined
                                  }
                                />
                              ) : entry.id === "repo-intel" ? (
                                <SemanticArchitecturePanel
                                  runId={runId}
                                  status={entry.liveStatus}
                                />
                              ) : entry.id === "deps" ? (
                                <DependencyRiskPanel runId={runId} status={entry.liveStatus} />
                              ) : entry.id === "static" ? (
                                <StaticFindingsPanel runId={runId} status={entry.liveStatus} />
                              ) : entry.id === "reproduce" ? (
                                <ReproductionEvidencePanel
                                  runId={runId}
                                  status={entry.liveStatus}
                                />
                              ) : entry.id === "root" ? (
                                <EvidenceInvestigationBoard
                                  runId={runId}
                                  status={entry.liveStatus}
                                />
                              ) : entry.id === "blast" ? (
                                <BlastRadiusPanel runId={runId} status={entry.liveStatus} />
                              ) : entry.id === "context" ? (
                                <ContextEngineeringPanel runId={runId} status={entry.liveStatus} />
                              ) : entry.id === "planner" ? (
                                <RepairPlanPanel runId={runId} status={entry.liveStatus} />
                              ) : entry.id === "security" ? (
                                <SecurityRescanPanel runId={runId} status={entry.liveStatus} />
                              ) : entry.id === "mutation" ? (
                                <MutationValidationPanel runId={runId} status={entry.liveStatus} />
                              ) : entry.id === "patch" ? (
                                <PatchPanel
                                  bundle={runData.patch}
                                  state={runData.status.patch}
                                  onRetry={runData.retry}
                                  provenance={
                                    entry.visualization?.kind === "patch"
                                      ? entry.visualization.data.files
                                      : []
                                  }
                                />
                              ) : entry.id === "merge" ? (
                                <MergeabilityDecisionPanel
                                  runId={runId}
                                  status={entry.liveStatus}
                                />
                              ) : undefined
                            }
                            onSelect={() => {
                              toggleOverride(i, defaultExpanded);
                            }}
                          />
                        </div>

                        {/* Mid-run the stage shows one agent, so name what comes
                            next — otherwise the pipeline reads as a dead end
                            between stages. */}
                        {!done && i === focusIndex && agents[i + 1] && (
                          <div className="mt-3 flex items-center justify-center gap-2 text-xs text-ink-soft">
                            <span className="h-px w-8 bg-border" aria-hidden />
                            <span>
                              next · <span className="text-ink">{agents[i + 1].agent}</span>
                            </span>
                            <span className="h-px w-8 bg-border" aria-hidden />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>

              {(() => {
                const mutationIdx = agents.findIndex((a) => a.id === "mutation");
                const showRetries = (mutationIdx >= 0 && activeIndex >= mutationIdx)
                  || (runData.summary.recordedAttempts ?? 0) > 0;
                return showRetries ? (
                  <div ref={repairAttemptsRef} className="scroll-mt-6 space-y-3">
                    {runData.status.attempts.error && (
                      <PanelError
                        panel="repair attempts"
                        message={runData.status.attempts.error}
                        onRetry={runData.retry}
                      />
                    )}
                    <RetrySequence model={runData.attempts} />
                  </div>
                ) : null;
              })()}

              <footer className="pb-4 pt-2 text-center text-xs text-ink-soft">
                Run {runData.header.shortRunId} · evidence preserved · proof bundle signed
              </footer>
            </div>

            {chatHistoryOpen ? (
              <aside className="w-[380px] shrink-0 self-start sticky top-3 animate-panel-in-right">
                <ChatHistoryDrawer
                  open={chatHistoryOpen}
                  onClose={() => setChatHistoryOpen(false)}
                  conversations={chatConversations.conversations}
                  activeConversationId={chatConversations.activeConversationId}
                  onSelectConversation={chatConversations.selectConversation}
                  onNewChat={chatConversations.newChat}
                  onSendMessage={(q) =>
                    chatConversations.sendMessage(
                      q,
                      isLive ? (question) => askChat(runId, question) : mockAnswerer,
                      { updateLatestExchange: false },
                    )
                  }
                  isAnswering={chatConversations.isAnswering}
                  embedded={true}
                />
              </aside>
            ) : (
              done &&
              showRunReport &&
              // A report that never loaded must not render as an empty one —
              // "0 files changed, no trust score" is a claim, and we do not
              // have it. Once a report *has* loaded, a later failed poll shows
              // the stale-but-real report with the error beside it.
              (runData.status.report.error && !runData.status.report.loaded ? (
                <aside className="w-[380px] shrink-0">
                  <PanelError
                    panel="the run report"
                    message={runData.status.report.error}
                    onRetry={runData.retry}
                  />
                </aside>
              ) : (
                <div className="contents">
                  {runData.status.report.error && (
                    <PanelError
                      panel="the latest run report"
                      message={runData.status.report.error}
                      onRetry={runData.retry}
                    />
                  )}
                  {runData.report && (
                    <RunReport
                      done={done}
                      agents={agents}
                      activeIndex={activeIndex}
                      report={runData.report}
                    />
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </main>

      {view === "execution" && (
        <>
          {done && (
            <>
              {/* LatestChatExchange only appears above the bottom ChatPanel when
                  the side chat bar is NOT open, and only for questions submitted
                  directly into the bottom prompt bar. */}
              {!chatHistoryOpen && (
                <LatestChatExchange
                  latestUserMsg={chatConversations.latestUserMsg}
                  latestAIMsg={chatConversations.latestAIMsg}
                  isAnswering={chatConversations.isAnswering}
                  anchorRef={chatAnchorRef}
                />
              )}

              {/* ChatPanel is 100% unchanged. We only intercept its `answerer` prop
                  to also store each exchange in the conversation history state.
                  sendMessage() returns the reply string so ChatPanel's internal
                  display stays in sync. In mock mode mockAnswerer is passed through
                  just as ChatPanel's own default would have used it. */}
              <ChatPanel
                answerer={(q) =>
                  chatConversations.sendMessage(
                    q,
                    isLive ? (question) => askChat(runId, question) : mockAnswerer,
                    { updateLatestExchange: true },
                  )
                }
                anchorRef={chatAnchorRef}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}

function SimpleView({ label }: { label: string }) {
  const title = label.charAt(0).toUpperCase() + label.slice(1);
  return (
    <div className="flex min-h-[calc(100vh-3rem)] items-center justify-center px-6">
      <div className="text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        <p className="mt-2 text-sm text-ink-soft">
          Select a repository from the sidebar to view a run.
        </p>
      </div>
    </div>
  );
}

function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description?: string;
}) {
  return (
    <div>
      <div className="text-[11px] font-medium uppercase tracking-wider text-primary">{eyebrow}</div>
      <h2 className="mt-1 text-xl font-semibold tracking-tight text-ink">{title}</h2>
      {description && <p className="mt-1 max-w-2xl text-sm text-ink-soft">{description}</p>}
    </div>
  );
}

/** Shown while live view models are in flight, in place of fixture data. */
function WorkspaceLoading() {
  return (
    <div className="mx-auto max-w-[1480px] px-6 py-6">
      <div className="flex items-center gap-2.5 text-sm text-ink-soft">
        <span className="h-1.5 w-1.5 animate-soft-pulse rounded-full bg-status-running" />
        Loading run…
      </div>
    </div>
  );
}

/** A7's own word for "context_source", turned into a phrase — not a translation of meaning, just spacing. */
function contextSourceLabel(source: string | null): string | null {
  if (source === "a5_5_context_package") return "A5.5 context package";
  if (source === "a7_local_extraction") return "A7 local extraction";
  return source;
}

/**
 * The "⏺ Update(file) / ⎿ Updated file with N additions and M removals"
 * header a coding-assistant terminal prints above a file edit — real counts
 * from the real diff, real target-function name only when A7 resolved one,
 * no invented detail. Fixed dark/mono styling matches `DiffView`'s terminal
 * palette immediately below it, independent of the host page's theme.
 */
function ClaudeStyleEditHeader({
  patch,
  provenance,
  stats,
}: {
  patch: PatchCandidateModel;
  provenance: PatchFileProvenance | undefined;
  stats: { added: number; removed: number };
}) {
  return (
    <div
      className="rounded-lg px-3 py-2 font-mono text-[12px]"
      style={{ backgroundColor: "#000000" }}
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-[#d4d4d4]">●</span>
        <span className="font-semibold text-[#d4d4d4]">
          Update(
          <span className="font-normal">{patch.file}</span>
          {provenance?.targetFunction && (
            <span className="text-[#6e7681]"> :: {provenance.targetFunction}()</span>
          )}
          )
        </span>
      </div>
      <div className="mt-0.5 flex items-baseline gap-1.5 pl-3 text-[11px] text-[#6e7681]">
        <span aria-hidden>⎿</span>
        <span>
          Updated {patch.file} with{" "}
          <span className="text-[#4ec97c]">
            {stats.added} addition{stats.added === 1 ? "" : "s"}
          </span>{" "}
          and{" "}
          <span className="text-[#f16d6d]">
            {stats.removed} removal{stats.removed === 1 ? "" : "s"}
          </span>
        </span>
      </div>
    </div>
  );
}

/**
 * Generation facts for one patched file.
 *
 * `method` comes straight from the persisted `PatchCandidate` — always
 * present, since A7 records it for every write — so "AST validated ✓" shows
 * even on a run from before per-file provenance existed. `provenance` is the
 * separate, newer source (`a7_patch_metrics`) and may genuinely be `undefined`
 * for such a run; its facts (LLM vs. stub, context, retry) are simply omitted
 * rather than guessed, never backfilled from `method`.
 */
function PatchProvenanceRow({
  method,
  provenance,
}: {
  method: string | null;
  provenance: PatchFileProvenance | undefined;
}) {
  const items: { label: string; value: string }[] = [];

  if (provenance?.generationSource === "stub") {
    items.push({ label: "Generated", value: "Stub mode — no LLM configured, file unchanged" });
  } else if (provenance?.generationSource === "llm") {
    items.push({ label: "Generated", value: "LLM" });
  }
  const context = contextSourceLabel(provenance?.contextSource ?? null);
  if (context) items.push({ label: "Context", value: context });
  if (method === "ast_validated_write") {
    items.push({ label: "Validation", value: "AST validated ✓" });
  }
  if (provenance) {
    items.push({
      label: "Retry",
      value: provenance.retryNumber
        ? `Retry ${provenance.retryNumber}${provenance.retryReason ? ` — ${provenance.retryReason}` : ""}`
        : "No retry",
    });
  }

  if (items.length === 0) return null;

  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.label}>
          <dt className="uppercase tracking-wider text-ink-soft">{it.label}</dt>
          <dd className="mt-0.5 text-ink">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A7 → A8 handoff. A statement of what happens next, not a validation
 * result — A8 has not run yet from this panel's point of view, so nothing
 * here may read as "passed" or "failed".
 */
function A7ToA8Handoff() {
  return (
    <div className="flex items-center justify-center gap-2 border-t border-border/60 pt-2.5 text-[10px] text-ink-soft">
      <span className="rounded-md bg-surface-muted px-2 py-1 font-medium uppercase tracking-wider text-ink">
        A7 generated patch
      </span>
      <span aria-hidden>↓</span>
      <span className="rounded-md border border-border px-2 py-1 font-medium uppercase tracking-wider">
        A8 validation
      </span>
    </div>
  );
}

/**
 * The reject pile: plans A7 attempted but never wrote to disk, because every
 * retry the integrity guard saw came back a no-op, an abbreviated patch, or
 * invalid Python (`retryReason`, from the same `failed` emit the guard makes
 * for that plan). Rendered in the same terminal transcript idiom as a
 * successful `Update(...)`, just in the failed-status colour and with no
 * diff to show — the point is that the guard's rejection is not silently
 * dropped from the run the way it is when only written files reach the UI.
 */
function RejectedAttempts({ provenance }: { provenance: PatchFileProvenance[] }) {
  const rejected = provenance.filter((p) => p.written === false);
  if (rejected.length === 0) return null;

  return (
    <div className="space-y-1.5">
      {rejected.map((p) => (
        <div
          key={p.file}
          className="rounded-lg px-3 py-2 font-mono text-[12px]"
          style={{ backgroundColor: "#000000" }}
        >
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[#f16d6d]">●</span>
            <span className="font-semibold text-[#d4d4d4]">
              Update(<span className="font-normal">{p.file}</span>)
            </span>
          </div>
          <div className="mt-0.5 flex items-baseline gap-1.5 pl-3 text-[11px] text-[#6e7681]">
            <span aria-hidden>⎿</span>
            <span>
              Rejected — {p.retryReason ?? "no candidate cleared the integrity check"}
              {typeof p.retryNumber === "number" && p.retryNumber > 0
                ? ` after ${p.retryNumber} ${p.retryNumber === 1 ? "retry" : "retries"}`
                : ""}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * The diff A7 produced, which is the product.
 *
 * The patch card lists filenames; `/runs/{id}/patch` has served both sides of
 * every file all along and nothing rendered them. Same three states as the
 * context panel — a 404 is "generation never completed", distinct from a bundle
 * whose patch list is empty, which is "generation completed and changed
 * nothing". Those are different facts and the panel says which.
 *
 * `provenance` is a second, independent source — A7's own `a7_patch_metrics`,
 * projected onto the "patch" agent card — carrying facts the bundle itself
 * does not: which file was the resolved target, whether generation was real
 * LLM output or stub mode, and what context A7 reasoned over. It is additive;
 * the diff renders in full even when provenance for a file is unavailable.
 */
function PatchPanel({
  bundle,
  state,
  onRetry,
  provenance,
}: {
  bundle: PatchBundleModel | null;
  state: ModelState;
  onRetry: () => void;
  provenance: PatchFileProvenance[];
}) {
  const provenanceByFile = useMemo(() => new Map(provenance.map((p) => [p.file, p])), [provenance]);
  const diffChunks = useMemo(
    () => (bundle ? splitDiffByFile(bundle.diff_text) : new Map<string, string>()),
    [bundle],
  );
  const targetFile = provenance.find((p) => p.isTarget)?.file ?? bundle?.patches[0]?.file ?? null;
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const activeFile = selectedFile ?? targetFile;

  if (state.error && !state.loaded) {
    return <PanelError panel="the patch" message={state.error} onRetry={onRetry} />;
  }

  if (!bundle) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">Patch</h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          Pending — patch generation has not produced a bundle for this run yet.
        </p>
      </section>
    );
  }

  const stats = diffStats(parseUnifiedDiff(bundle.diff_text));
  const activePatch = bundle.patches.find((p) => p.file === activeFile) ?? bundle.patches[0];
  const activeDiff = activeFile
    ? (diffChunks.get(activeFile) ?? bundle.diff_text)
    : bundle.diff_text;

  return (
    <section className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">Patch</h3>
        {bundle.patches.length > 0 && (
          <span className="font-mono text-[11px]">
            <span className="text-status-completed">+{stats.added}</span>{" "}
            <span className="text-status-failed">−{stats.removed}</span>{" "}
            <span className="text-ink-soft">
              across {bundle.patches.length} file{bundle.patches.length === 1 ? "" : "s"}
            </span>
          </span>
        )}
      </div>

      <RejectedAttempts provenance={provenance} />

      {bundle.patches.length === 0 ? (
        provenance.length === 0 && (
          <p className="text-[11px] text-ink-soft">
            Patch generation completed and produced no change. This is not the same as generation
            having failed — no candidate cleared the integrity check.
          </p>
        )
      ) : (
        <>
          {/* File selector — only when there is a real choice to make. A
              single file stays compact (the header stat line already carries
              its counts); with more than one, each gets its own real diff
              rather than being merged into one ambiguous view. */}
          {bundle.patches.length > 1 && (
            <ul className="flex flex-wrap gap-1">
              {bundle.patches.map((p) => {
                const prov = provenanceByFile.get(p.file);
                const fileStats = diffStats(parseUnifiedDiff(diffChunks.get(p.file) ?? ""));
                const isActive = p.file === activeFile;
                return (
                  <li key={p.file}>
                    <button
                      type="button"
                      onClick={() => setSelectedFile(p.file)}
                      aria-pressed={isActive}
                      className={`flex items-center gap-1.5 rounded px-1.5 py-0.5 font-mono text-[10px] transition-colors ${
                        isActive
                          ? "bg-primary/15 text-primary"
                          : "bg-surface-muted text-ink-soft hover:bg-surface-muted/70"
                      }`}
                    >
                      {p.file}
                      {prov?.isTarget && (
                        <span className="rounded bg-primary/20 px-1 text-[9px] font-semibold uppercase tracking-wider">
                          target
                        </span>
                      )}
                      <span className="text-status-completed">+{fileStats.added}</span>
                      <span className="text-status-failed">−{fileStats.removed}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {activePatch && (
            <ClaudeStyleEditHeader
              patch={activePatch}
              provenance={provenanceByFile.get(activePatch.file)}
              stats={diffStats(parseUnifiedDiff(activeDiff))}
            />
          )}

          {/* Keyed by file so switching the selector remounts a fresh,
              collapsed `DiffView` rather than carrying one file's "expanded"
              choice onto the next file's unrelated diff. */}
          <DiffView key={activeFile ?? "diff"} diff={activeDiff} />

          <PatchProvenanceRow
            method={activePatch?.method ?? null}
            provenance={activePatch ? provenanceByFile.get(activePatch.file) : undefined}
          />

          {bundle.contracts.length > 0 && (
            <div>
              <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
                Repair contracts
              </div>
              <ul className="space-y-0.5 text-[11px] text-ink-soft">
                {bundle.contracts.map((c, i) => (
                  <li key={`${c.location}:${i}`}>
                    <span className="font-mono text-ink">{c.location}</span> — {c.assertion}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <A7ToA8Handoff />
        </>
      )}

      {state.error && (
        <PanelError panel="the latest patch" message={state.error} onRetry={onRetry} />
      )}
    </section>
  );
}

function PanelError({
  panel,
  message,
  onRetry,
}: {
  panel: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load {panel}</span>
      <span className="font-mono text-ink-soft">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="ml-auto rounded-md border border-border px-2 py-0.5 font-medium text-ink transition-colors hover:bg-surface-muted"
      >
        Retry
      </button>
    </div>
  );
}

function WorkspaceHeader({
  done,
  header,
  lifecycle,
  onOpenChatHistory,
  chatOpen = false,
}: {
  done: boolean;
  /** Always supplied by `useRunData` — blank in live mode until the backend answers. */
  header: WorkspaceHeaderModel;
  /** How the backend says this run ended. Drives every label in this header. */
  lifecycle: RunLifecycleView;
  /** When supplied, a Chat / History button is rendered in the top-right of the header. */
  onOpenChatHistory?: () => void;
  /** Whether the chat sidebar is currently open. */
  chatOpen?: boolean;
}) {
  // Mock mode has no backend status, so `done` alone settles it as completed.
  const state = lifecycle.terminal ? lifecycle.state : done ? "completed" : "running";
  const settled = state !== "running";
  // "Current Agent" must name an agent, never a run/state word. A terminal
  // run's `header.currentAgent` is the backend's own record of which agent
  // last owned it (e.g. "A0.7" for blocked, "A10" for completed) — rendering
  // a synthetic "Completed"/"Stopped" here instead was exactly the "state
  // wearing an agent identity" contradiction this pass exists to remove.
  const currentAgentValue = !settled
    ? "Streaming…"
    : header.currentAgent && header.currentAgent !== "A0"
      ? header.currentAgent
      : "Not active";
  const items = [
    { label: "Repository", value: header.repository, mono: true },
    { label: "Branch", value: header.branch, icon: <GitBranch className="h-3 w-3" />, mono: true },
    { label: "Run", value: header.shortRunId, icon: <Hash className="h-3 w-3" />, mono: true },
    { label: "Current Agent", value: currentAgentValue },
    { label: "Retries", value: String(header.retries), icon: <RefreshCcw className="h-3 w-3" /> },
    { label: "Execution Time", value: header.executionTime, icon: <Clock className="h-3 w-3" /> },
  ];
  return (
    <section className="rounded-2xl border border-border bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                state === "completed"
                  ? "bg-status-completed"
                  : state === "failed"
                    ? "bg-status-failed"
                    : state === "blocked"
                      ? "bg-status-draft"
                      : "bg-status-running animate-soft-pulse"
              }`}
            />
            {state === "completed"
              ? "Execution complete"
              : state === "failed"
                ? "Execution stopped"
                : state === "blocked"
                  ? "Execution halted before repair"
                  : "Executing"}
          </div>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
            {header.repository} <span className="text-ink-soft">·</span>{" "}
            <span className="font-mono text-lg text-ink-soft">{header.branch}</span>
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge
            status={state}
            pulse={!settled}
            label={
              state === "running"
                ? "Status · Running"
                : state === "completed"
                  ? "Status · Completed"
                  : state === "failed"
                    ? "Status · Failed"
                    : "Status · Blocked"
            }
          />
          <StatusBadge
            // `blocked` has its own tone now (B-F05). It used to fall into the
            // draft branch here, painting "a PR awaits review" over a run that
            // never produced one.
            status={state === "failed" || state === "blocked" ? state : "draft"}
            label={`Decision · ${lifecycle.terminal ? lifecycle.decisionLabel : header.decisionLabel}`}
          />
          {onOpenChatHistory && (
            <button
              type="button"
              onClick={onOpenChatHistory}
              className={`inline-flex h-7 w-7 items-center justify-center rounded-lg border transition ${
                chatOpen
                  ? "border-white/30 bg-[#1e2330] text-white shadow-xs"
                  : "border-white/10 bg-[#0d0f14] text-gray-400 hover:border-white/25 hover:bg-[#141720] hover:text-white"
              }`}
              aria-label={chatOpen ? "Close side chat" : "Open side chat"}
              title={chatOpen ? "Close side chat" : "Toggle side chat (VS Code layout)"}
            >
              <VsCodeSidebarRightIcon active={chatOpen} className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Why the run ended, in the backend's words. Only ever rendered when the
          backend supplied a reason — there is no fallback sentence, because a
          composed one would be this UI describing a verdict it did not reach. */}
      {lifecycle.reason && (
        <div
          className={`mt-4 rounded-lg border px-3 py-2.5 ${
            state === "failed"
              ? "border-status-failed/30 bg-status-failed-bg/40"
              : state === "blocked"
                ? "border-status-blocked/30 bg-status-blocked-bg/40"
                : "border-status-draft/30 bg-status-draft-bg/40"
          }`}
        >
          <div className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            {state === "blocked" ? "Environment" : "Reason"}
          </div>
          <p className="mt-1 text-sm text-ink">{lifecycle.reason}</p>
        </div>
      )}

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {items.map((it) => (
          <div
            key={it.label}
            className="rounded-lg border border-border bg-surface-muted/60 px-3 py-2.5"
          >
            <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-ink-soft">
              {it.icon}
              {it.label}
            </div>
            <div
              className={`mt-1 truncate text-sm font-semibold text-ink tabular-nums ${it.mono ? "font-mono" : ""}`}
              title={it.value}
            >
              <AnimatedNumber value={it.value} />
            </div>
          </div>
        ))}
      </div>

      <RepositoryIdentity header={header} />
    </section>
  );
}

/**
 * Which repository, at which commit, indexed as what.
 *
 * The backend has published these since the index layer landed and nothing
 * rendered them. `repositoryId` is the key every cross-run store is keyed by, so
 * it is the only thing that makes "this repository has been seen before"
 * checkable rather than asserted — which is exactly the claim A0.5's card makes.
 *
 * Fields are omitted when the backend sends null. A commit the run never
 * observed is not rendered as a dash that implies a value was measured and
 * empty; it simply is not there, and when nothing is known the strip is absent.
 */
function RepositoryIdentity({ header }: { header: WorkspaceHeaderModel }) {
  const entries = [
    { label: "Repository ID", value: header.repositoryId, short: (v: string) => v.slice(0, 12) },
    { label: "HEAD", value: header.headSha, short: (v: string) => v.slice(0, 7) },
    { label: "Index hash", value: header.repositoryHash, short: (v: string) => v.slice(0, 12) },
  ].filter((e): e is { label: string; value: string; short: (v: string) => string } =>
    Boolean(e.value),
  );

  if (entries.length === 0) return null;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-border pt-3">
      {entries.map((e) => (
        <span key={e.label} className="flex items-center gap-1.5 text-[11px]">
          <span className="font-medium uppercase tracking-wider text-ink-soft">{e.label}</span>
          <span className="font-mono text-ink" title={e.value}>
            {e.short(e.value)}
          </span>
        </span>
      ))}
    </div>
  );
}
