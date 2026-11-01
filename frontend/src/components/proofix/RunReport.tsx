import { useState, useRef, useEffect } from "react";
import { apiFetch, ApiError, ENDPOINTS } from "@/lib/api";
import { Download, Copy, Check, GitPullRequest, ShieldCheck, FileCode } from "lucide-react";
import type { LiveAgent } from "./useExecutionRun";
import { StatusBadge } from "./StatusBadge";
import { StatusIcon } from "./StatusIcon";
import { ProgressRing } from "./ProgressRing";
import { AnimatedNumber } from "./AnimatedNumber";
import { type RunReportModel } from "@/mocks";

const recoveryAction: Record<string, string> = {
  generate_code: "Revise patch",
  blast_scope: "Resolve target and replan",
  investigate: "Reinvestigate evidence",
  recovery_stop: "Stop for review",
  validate_security: "Continue validation",
  route_pr: "Route for review",
};

/**
 * Formats and sanitizes a download filename for a run report.
 * Format: `<repository-name>-run-report-<run-id>.json`
 */
export function getRunReportFilename(report: RunReportModel): string {
  const sanitize = (str: string) =>
    str
      .trim()
      .replace(/[^a-zA-Z0-9._-]/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-+|-+$/g, "");

  const repo = sanitize(report.repository || "");
  const runId = sanitize(report.shortRunId || report.runId || "");

  if (repo && runId) {
    return `${repo}-run-report-${runId}.json`;
  }
  if (repo) {
    return `${repo}-run-report.json`;
  }
  if (runId) {
    return `run-report-${runId}.json`;
  }
  return "run-report.json";
}

/**
 * Serializes the complete RunReportModel to formatted JSON.
 */
export function serializeRunReport(report: RunReportModel): string {
  return JSON.stringify(report, null, 2);
}

export function RunReport({
  done,
  agents,
  activeIndex,
  report: initialReport,
}: {
  done: boolean;
  agents?: LiveAgent[];
  activeIndex?: number;
  /**
   * The run's report. Required (B-F02): this used to default to
   * `MOCK_RUN_REPORT`, so omitting the prop rendered another repository's trust
   * scores, file list and PR decision as if they belonged to this run. There is
   * no safe default for a component whose whole job is reporting measurements.
   */
  report: RunReportModel;
}) {
  const [updatedReport, setUpdatedReport] = useState<RunReportModel | null>(null);
  const report = updatedReport?.runId === initialReport.runId ? updatedReport : initialReport;
  const [creating, setCreating] = useState(false);
  const [publicationError, setPublicationError] = useState<string | null>(null);
  const requestActive = useRef(false);
  const requestVersion = useRef(0);
  useEffect(() => {
    requestVersion.current += 1;
    requestActive.current = false;
    setCreating(false);
    setPublicationError(null);
  }, [initialReport.runId]);
  const publishing = creating || report.publication?.status === "publishing";
  const canPublish = done && !!report.publication?.prepared_head_sha && !report.prUrl;
  useEffect(() => {
    if (report.publication?.status !== "publishing") return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      apiFetch<RunReportModel>(ENDPOINTS.runReport(report.runId))
        .then((next) => {
          if (!cancelled) {
            setUpdatedReport(next);
            setPublicationError(null);
          }
        })
        .catch(() => {
          if (!cancelled)
            setPublicationError(
              "Could not refresh publication status. Refresh the page to check before retrying.",
            );
        });
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [report.runId, report.publication?.status]);

  const handleCreateDraftPr = async () => {
    if (requestActive.current || publishing || !canPublish) return;
    requestActive.current = true;
    const version = requestVersion.current;
    setCreating(true);
    setPublicationError(null);
    try {
      const next = await apiFetch<RunReportModel>(ENDPOINTS.runDraftPr(report.runId), {
        method: "POST",
      });
      if (version === requestVersion.current) setUpdatedReport(next);
    } catch (error) {
      if (version === requestVersion.current) {
        if (error instanceof ApiError && (error.status === 410 || error.status === 404)) {
          setUpdatedReport({
            ...report,
            publication: {
              ...report.publication,
              status: "disabled",
              prepared_head_sha: null,
              reason: "The prepared repair is no longer available. Start a new repair run.",
            },
          });
          return;
        }
        setPublicationError(
          "Could not create the draft PR. Check the run status and retry; an existing PR will be reused.",
        );
        try {
          const next = await apiFetch<RunReportModel>(ENDPOINTS.runReport(report.runId));
          if (version === requestVersion.current) {
            setUpdatedReport(next);
            if (next.prUrl) setPublicationError(null);
          }
        } catch {
          /* Keep the last report and the retry action. */
        }
      }
    } finally {
      if (version === requestVersion.current) {
        requestActive.current = false;
        setCreating(false);
      }
    }
  };
  const decision = report.decision;
  // `blocked` now has its own badge tone (B-F05) — it used to borrow `draft`,
  // which said "a PR is waiting for review" about a run that produced no PR at
  // all. The label beside it is the backend's own wording.
  const decisionBadge = decision === "merge" ? "completed" : decision;
  const current = !done && agents && activeIndex !== undefined ? agents[activeIndex] : null;

  const [copied, setCopied] = useState(false);
  const copyTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        window.clearTimeout(copyTimeoutRef.current);
      }
    };
  }, []);

  const handleCopyJson = async () => {
    if (!report) return;
    const json = serializeRunReport(report);
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(json);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = json;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }
      setCopied(true);
      if (copyTimeoutRef.current) {
        window.clearTimeout(copyTimeoutRef.current);
      }
      copyTimeoutRef.current = window.setTimeout(() => {
        setCopied(false);
      }, 1800);
    } catch (err) {
      console.error("Failed to copy report JSON to clipboard:", err);
    }
  };

  const handleDownloadJson = () => {
    if (!report) return;
    try {
      const json = serializeRunReport(report);
      const blob = new Blob([json], { type: "application/json" });
      const filename = getRunReportFilename(report);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Failed to download report JSON:", err);
    }
  };

  const handleOpenGithubPr = () => {
    const prUrl = report.prUrl?.trim();
    if (!prUrl) return;
    window.open(prUrl, "_blank", "noopener,noreferrer");
  };

  return (
    <aside className="hidden w-[380px] shrink-0 self-start sticky top-3 xl:block animate-panel-in-right">
      <div className="flex h-[calc(100vh-1.5rem)] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-[0_1px_0_hsl(var(--border)),0_20px_40px_-24px_rgba(0,0,0,0.35)]">
        <header className="border-b border-border px-6 py-5">
          <div className="flex items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
              Run Report
            </span>
            {current ? (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-status-running-bg px-2 py-0.5 text-[10px] font-medium text-status-running">
                <span className="h-1.5 w-1.5 rounded-full bg-status-running animate-soft-pulse" />
                Live
              </span>
            ) : (
              <span className="font-mono text-[11px] text-ink-soft">{report.shortRunId}</span>
            )}
          </div>
          <h3 className="mt-3 font-heading text-[17px] font-semibold tracking-tight text-ink">
            {report.repository} <span className="text-ink-soft">·</span>{" "}
            <span className="font-mono text-[15px] font-medium text-ink-soft">{report.branch}</span>
          </h3>
          <p className="mt-1 text-[13px] text-ink-soft">
            {done
              ? decision === "blocked" || decision === "failed"
                ? "Execution stopped · review needed"
                : "Execution complete · awaiting review"
              : "Execution in progress…"}
          </p>
        </header>

        {current ? (
          <LiveObserving agent={current} />
        ) : (
          <div className="flex-1 overflow-y-auto px-6 py-6 divide-y divide-border/60">
            {report.reportedIssue && (
              <Section title="Reported Issue">
                <p className="text-sm leading-relaxed text-ink">
                  {report.reportedIssue.description}
                </p>
                {report.reportedIssue.expected_behavior && (
                  <p className="mt-2 text-xs leading-relaxed text-ink-soft">
                    Expected: {report.reportedIssue.expected_behavior}
                  </p>
                )}
                <p className="mt-2 break-all font-mono text-[11px] text-ink-soft">
                  Acceptance test: {report.reportedIssue.target_test || "Not established"}
                </p>
                {report.reportedIssue.target_file && (
                  <p className="mt-1 break-all font-mono text-[11px] text-ink-soft">
                    Suspected file: {report.reportedIssue.target_file}
                  </p>
                )}
              </Section>
            )}
            {report.generatedTest && (
              <Section title="Generated Regression Test">
                <p className="text-xs leading-relaxed text-ink-soft">
                  AI-generated acceptance test. Review whether its assertion matches the reported
                  issue.
                </p>
                <p className="mt-2 text-sm text-ink">
                  Before repair:{" "}
                  {report.generatedTest.before_status === "CONFIRMED"
                    ? "Assertion failed"
                    : "Unverified"}
                  {" · "}After repair:{" "}
                  {report.generatedTest.after_passed === true
                    ? "Passed"
                    : report.generatedTest.after_passed === false
                      ? "Failed"
                      : "Unverified"}
                </p>
                {(report.generatedTest.reason || report.generatedTest.validation_detail) && (
                  <p className="mt-2 text-xs text-ink-soft">
                    {report.generatedTest.reason || report.generatedTest.validation_detail}
                  </p>
                )}
                {report.generatedTest.content && (
                  <details className="mt-3 text-xs text-ink-soft">
                    <summary className="cursor-pointer">
                      Inspect test: {report.generatedTest.path}
                    </summary>
                    <p className="mt-2">{report.generatedTest.rationale}</p>
                    <p className="mt-2 break-all font-mono">
                      SHA-256: {report.generatedTest.sha256}
                    </p>
                    <pre className="mt-2 overflow-x-auto rounded-lg bg-surface-muted p-3 text-ink">
                      {report.generatedTest.content}
                    </pre>
                  </details>
                )}
              </Section>
            )}
            {/* Final decision */}
            <Section title="Repair Decision">
              <div className="rounded-xl border border-border bg-surface-muted/40 px-4 py-4">
                <div className="flex items-start justify-between gap-4">
                  <StatusBadge status={decisionBadge} label={report.decisionLabel} />
                  <div className="text-right">
                    <div className="font-mono text-[26px] font-semibold leading-none tracking-tight text-ink tabular-nums">
                      {report.trustScore === null ? (
                        <span className="text-[15px] font-medium text-ink-soft">Not measured</span>
                      ) : (
                        <AnimatedNumber value={report.trustScore.toFixed(2)} duration={500} />
                      )}
                    </div>
                    <div className="mt-1.5 text-[9px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
                      Trust score
                    </div>
                  </div>
                </div>
                <div className="mt-4 border-t border-border/50 pt-3 flex items-center justify-between text-[11px] text-ink-soft">
                  {/* A run with no measured axis has not fallen below the
                    threshold — it has not been compared to one. Saying
                    "Confidence below threshold" there stated a verdict the
                    pipeline never reached. */}
                  <span>
                    {report.trustScore === null
                      ? "No axis was measured, so confidence could not be established"
                      : report.trustScore >= report.trustThreshold
                        ? "Confidence meets threshold"
                        : "Confidence below threshold"}
                  </span>
                  {report.trustScore !== null && (
                    <span className="font-mono tabular-nums text-ink">
                      {report.trustScore.toFixed(2)}
                      <span className="text-ink-soft"> / {report.trustThreshold.toFixed(2)}</span>
                    </span>
                  )}
                </div>
              </div>
            </Section>

            {/* Root cause. The analysis is prose the backend already wrote, so it
              is rendered as prose. An earlier version slotted fragments of it
              into a fixed sentence about token expiry, which described the mock
              fixture's bug no matter what the run had actually found. */}
            <Section title="Root Cause">
              <div className="space-y-2">
                {report.rootCause.statement && (
                  <p className="text-[13.5px] font-medium leading-relaxed text-ink">
                    {report.rootCause.statement}
                  </p>
                )}
                {report.rootCause.summary && (
                  <p className="text-[13.5px] leading-relaxed text-ink-soft">
                    {report.rootCause.summary}
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-2 pt-0.5">
                  <span className="font-mono text-[12.5px] text-ink">
                    {report.rootCause.location.split(":")[0]}
                    <span className="text-ink-soft">:</span>
                    <span className="font-semibold">{report.rootCause.location.split(":")[1]}</span>
                  </span>
                  {report.rootCause.claim && (
                    <span className="rounded bg-surface-muted/70 px-1 py-0.5 font-mono text-[12.5px] text-ink">
                      {report.rootCause.claim}
                    </span>
                  )}
                  {/* A source anchor does not verify the diagnosis claim. */}
                  <span
                    className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11.5px] font-medium ${
                      report.rootCause.verified
                        ? "bg-status-completed-bg text-status-completed"
                        : "bg-status-retry-bg text-status-retry"
                    }`}
                  >
                    {report.rootCause.verified
                      ? "source located · claim unverified"
                      : "source not located"}
                  </span>
                </div>
              </div>
            </Section>

            {/* Why this decision — both paragraphs come from the backend. They used
              to be hardcoded, and asserted mutation-testing rejection and an
              authentication-logic rationale for every run regardless of cause. */}
            <Section title="Why this decision">
              <div className="space-y-3 text-[13.5px] leading-relaxed text-ink">
                <p>{report.rejection.reason}</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-soft">
                  <span>
                    attempts{" "}
                    <span className="font-mono font-semibold tabular-nums text-ink">
                      {report.rejection.attempts}
                    </span>
                  </span>
                  <span>
                    survivors{" "}
                    <span className="font-mono font-semibold tabular-nums text-ink">
                      {report.rejection.survivors}
                    </span>
                  </span>
                  <span>
                    mutation score{" "}
                    <span className="font-mono font-semibold tabular-nums text-ink">
                      {/* Never print 0.00 for "never measured". */}
                      {report.rejection.score === null
                        ? "not scored"
                        : report.rejection.score.toFixed(2)}
                    </span>
                    {/* No threshold is printed beside it: the pipeline publishes
                      no mutation gate, and the figure previously shown here was
                      read from `rejection.threshold` — a field the backend has
                      never sent. Against a live run it was `undefined`, and
                      `.toFixed()` on it crashed the whole report into the
                      router's error page. */}
                  </span>
                  <span>
                    correctness{" "}
                    <span className="font-mono font-semibold tabular-nums text-ink">
                      {/* Nullable for the same reason the mutation score is: A8
                        only records a correctness score once validation runs.
                        The threshold is withheld alongside it — a bar shown
                        against no measurement reads as a failed measurement. */}
                      {report.rejection.correctnessScore === null
                        ? "not scored"
                        : report.rejection.correctnessScore.toFixed(0)}
                    </span>
                    {report.rejection.correctnessScore !== null && (
                      <span className="text-ink-soft">
                        {" "}
                        / {report.rejection.correctnessThreshold.toFixed(0)}
                      </span>
                    )}
                  </span>
                </div>
                {report.decisionReason && <p className="text-ink-soft">{report.decisionReason}</p>}
                <DraftReasons reasons={report.draftReasons} />
              </div>
            </Section>

            {report.recoveryAttempts && report.recoveryAttempts.length > 0 && (
              <Section title="Recovery Attempts">
                <ol className="space-y-3">
                  {report.recoveryAttempts.map((attempt) => (
                    <li
                      key={attempt.number}
                      className="rounded-xl border border-border/60 bg-surface-muted/30 p-3 text-xs leading-relaxed"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-ink">Attempt {attempt.number}</span>
                        <span className="text-ink-soft">
                          {attempt.validationStatus === "not_executed"
                            ? "not executed"
                            : (attempt.category?.replaceAll("_", " ") ?? "validation passed")}
                        </span>
                      </div>
                      <p className="mt-1 text-ink-soft">{attempt.reason}</p>
                      <p className="mt-1 text-ink-soft">
                        Decision: {recoveryAction[attempt.decision] ?? attempt.decision}
                      </p>
                      {attempt.target && (
                        <p className="mt-1 break-all font-mono text-ink-soft">
                          Target: {attempt.target}
                        </p>
                      )}
                      {attempt.diagnosis && (
                        <p className="mt-1 text-ink-soft">Diagnosis: {attempt.diagnosis}</p>
                      )}
                      {attempt.failure && (
                        <p className="mt-1 break-words text-ink">{attempt.failure}</p>
                      )}
                      {attempt.patchHash && (
                        <p className="mt-1 font-mono text-ink-soft">
                          Patch SHA-256: {attempt.patchHash.slice(0, 12)}…
                        </p>
                      )}
                      {attempt.changedFromPrevious && (
                        <p className="mt-1 text-ink-soft">
                          Changed:{" "}
                          {[
                            attempt.changedFromPrevious.target && "target",
                            attempt.changedFromPrevious.diagnosis && "diagnosis",
                            attempt.changedFromPrevious.patch && "patch",
                          ]
                            .filter(Boolean)
                            .join(", ") || "nothing"}
                        </p>
                      )}
                    </li>
                  ))}
                </ol>
              </Section>
            )}

            {/* Evidence summary */}
            <Section title="Evidence Summary">
              <ul className="space-y-2.5">
                {report.evidence.map((e) => (
                  <li
                    key={e.text}
                    className="flex items-start gap-2.5 text-[13.5px] leading-relaxed text-ink"
                  >
                    <StatusIcon ok={e.ok} size="sm" className="mt-0.5" />
                    <span>{e.text}</span>
                  </li>
                ))}
              </ul>
            </Section>

            {/* Trust metrics */}
            <Section title="Trust Metrics">
              <div className="grid grid-cols-2 gap-x-2 gap-y-6 rounded-xl border border-border/60 bg-surface-muted/30 p-5">
                {report.trust.map((b) => (
                  <ProgressRing
                    key={b.label}
                    value={b.value}
                    label={b.label}
                    tone={b.tone}
                    size={76}
                  />
                ))}
              </div>
            </Section>

            {/* Files affected */}
            <Section title="Files Affected">
              <ul className="space-y-1">
                {report.files.map((f) => (
                  <li
                    key={f}
                    className="group flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors hover:bg-surface-muted/60"
                  >
                    <FileCode className="h-3.5 w-3.5 shrink-0 text-ink-soft transition-colors group-hover:text-primary" />
                    <span className="truncate font-mono text-[12.5px] text-ink">{f}</span>
                  </li>
                ))}
              </ul>
            </Section>

            {/* Proof bundle */}
            <Section title="Proof Bundle">
              <div className="group relative overflow-hidden rounded-xl border border-status-completed/25 bg-gradient-to-br from-status-completed-bg/50 to-status-completed-bg/10 px-4 py-3.5 transition-colors hover:border-status-completed/40">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-status-completed/30 bg-status-completed/10">
                    <ShieldCheck className="h-4 w-4 text-status-completed" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-status-completed">
                        Proof artifact
                      </span>
                      <span className="text-[10px] text-ink-soft">·</span>
                      <span className="text-[10px] text-ink-soft">
                        {report.publication?.status === "published" ? "Published" : "Unpublished"}
                      </span>
                    </div>
                    <div className="mt-1 truncate font-mono text-[12px] text-ink">
                      {report.proofBundle}
                    </div>
                  </div>
                </div>
              </div>
            </Section>

            <div className="pt-5 text-[11px] font-medium text-ink-soft">
              <span className="font-mono tabular-nums text-ink">{report.agentCount}</span> agents
              <span className="mx-1.5 text-ink-soft/60">·</span>
              <span className="font-mono tabular-nums text-ink">
                {report.totalDurationSeconds.toFixed(1)}s
              </span>{" "}
              execution
            </div>
          </div>
        )}

        {!current && (
          <footer className="border-t border-border bg-surface-muted/40 px-6 py-4 space-y-2.5">
            <p className="text-xs text-ink-soft" aria-live="polite">
              GitHub publication:{" "}
              {publishing ? "creating draft PR" : report.prUrl ? "published" : "not published"}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleDownloadJson}
                className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-xs font-medium text-ink transition-all duration-150 hover:border-primary/40 hover:bg-surface-muted/60 active:scale-[0.98]"
              >
                <Download className="h-3.5 w-3.5" /> Download
              </button>
              <button
                type="button"
                onClick={handleCopyJson}
                className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-xs font-medium text-ink transition-all duration-150 hover:border-primary/40 hover:bg-surface-muted/60 active:scale-[0.98]"
              >
                {copied ? (
                  <>
                    <Check className="h-3.5 w-3.5 text-emerald-500" /> Copied
                  </>
                ) : (
                  <>
                    <Copy className="h-3.5 w-3.5" /> Copy JSON
                  </>
                )}
              </button>
            </div>
            <button
              type="button"
              disabled={publishing || (!report.prUrl && !canPublish)}
              onClick={report.prUrl ? handleOpenGithubPr : handleCreateDraftPr}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-ink px-3 py-2.5 text-xs font-medium text-surface shadow-[0_1px_0_rgba(255,255,255,0.05)_inset,0_8px_20px_-10px_rgba(0,0,0,0.5)] transition-all duration-150 hover:-translate-y-[1px] hover:shadow-[0_1px_0_rgba(255,255,255,0.05)_inset,0_12px_24px_-10px_rgba(0,0,0,0.55)] active:translate-y-0 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0 disabled:hover:shadow-none"
            >
              <GitPullRequest className="h-3.5 w-3.5" />
              {publishing
                ? "Creating draft PR…"
                : report.prUrl
                  ? report.publication?.draft || decision === "draft"
                    ? "Open draft GitHub PR"
                    : "Open GitHub PR"
                  : canPublish
                    ? publicationError || report.publication?.status === "failed"
                      ? "Retry draft PR"
                      : "Create draft PR"
                    : report.publication?.status === "dry_run"
                      ? "Dry run — no GitHub PR"
                      : "GitHub PR unavailable"}
            </button>
            {publicationError && (
              <p role="alert" className="text-xs text-ink-soft">
                {publicationError}
              </p>
            )}
            {!report.prUrl && report.publication?.reason && (
              <p className="text-xs text-ink-soft">{report.publication.reason}</p>
            )}
          </footer>
        )}
      </div>
    </aside>
  );
}

function LiveObserving({ agent }: { agent: LiveAgent }) {
  const visible = agent.lines.slice(0, agent.visibleLines);
  const stillStreaming = agent.visibleLines < agent.lines.length;
  return (
    <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
      <div>
        <div className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Currently observing
        </div>
        <h3 className="mt-1.5 text-lg font-semibold tracking-tight text-ink">{agent.agent}</h3>
        <p className="mt-0.5 text-sm text-ink-soft">{agent.purpose}</p>
      </div>

      <div className="rounded-xl border border-border bg-surface-muted/60 p-4">
        <ul className="space-y-2">
          {visible.map((line, i) => (
            <li key={i} className="animate-line-in flex items-start gap-2.5 text-sm text-ink">
              <StatusIcon ok className="mt-0.5" />
              <span className="leading-relaxed">{line}</span>
            </li>
          ))}
          {stillStreaming && (
            <li className="flex items-start gap-2.5 text-sm text-ink-soft">
              <span className="mt-0.5 h-4 w-4 shrink-0 rounded-full border-2 border-status-running animate-soft-pulse" />
              <span className="leading-relaxed italic">
                {visible.length === 0 ? "Waiting for first signal…" : "working…"}
              </span>
            </li>
          )}
        </ul>
      </div>

      {agent.metrics && (
        <div>
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            Live Metrics
          </div>
          <div className="grid grid-cols-2 gap-2">
            {agent.metrics.map((m) => (
              <div key={m.label} className="rounded-lg border border-border bg-surface px-2.5 py-2">
                <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
                  {m.label}
                </div>
                <div className="mt-0.5 font-mono text-sm font-semibold text-ink">{m.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="text-xs italic text-ink-soft">
        Waiting for completion — the final report appears once all agents finish.
      </p>
    </div>
  );
}

/**
 * Every reason the run cannot be auto-merged.
 *
 * `decisionReason` above is A10's `review_note`, which carries only the *first*
 * reason routing hit — a run blocked because it could not reproduce the bug
 * *and* exhausted its retries showed one of the two, and the other was
 * unrecoverable from the UI. These come from `trust_gating.draft_reasons`, the
 * same computation that sets `force_draft_pr`, so what is on screen and what
 * routed the PR cannot disagree.
 *
 * Sentences are rendered verbatim. The backend owns the words (governing rule
 * 3); a UI that phrases its own explanation is a second source of truth for a
 * decision it did not make.
 */
function DraftReasons({ reasons }: { reasons?: { code: string; detail: string }[] }) {
  if (!reasons || reasons.length === 0) return null;

  return (
    <div className="mt-3 rounded-lg border border-status-draft/30 bg-status-draft-bg/30 px-3 py-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
        {reasons.length === 1 ? "Why this is a draft" : `Why this is a draft (${reasons.length})`}
      </div>
      <ul className="mt-1.5 space-y-1.5">
        {reasons.map((r) => (
          <li key={r.code} className="text-[12.5px] leading-relaxed text-ink">
            {r.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="py-6 first:pt-0 last:pb-0">
      <div className="mb-3.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
        {title}
      </div>
      {children}
    </div>
  );
}
