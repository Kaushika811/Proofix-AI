import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Sparkles } from "lucide-react";
import type { ExecutiveSummaryModel, RunReportModel, WorkspaceHeaderModel } from "@/mocks";

const tones = {
  passed: "text-status-completed",
  failed: "text-status-failed",
  incomplete: "text-amber-500",
  not_run: "text-ink-soft",
  unavailable: "text-ink-soft",
};

function ExpandableText({ text, label }: { text: string; label: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflow, setOverflow] = useState(false);
  const ref = useRef<HTMLParagraphElement>(null);
  const id = useId();
  useEffect(() => {
    const element = ref.current;
    if (!element || expanded) return;
    const measure = () => setOverflow(element.scrollHeight > element.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [text, expanded]);
  return (
    <div className="min-w-0">
      <p
        id={id}
        ref={ref}
        className={`whitespace-pre-wrap [overflow-wrap:anywhere] leading-relaxed ${expanded ? "" : "line-clamp-2"}`}
      >
        {text}
      </p>
      {(overflow || expanded) && (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={id}
          aria-label={`${expanded ? "Show less" : "Show more"}: ${label}`}
          onClick={() => setExpanded(!expanded)}
          className="mt-1 text-xs font-normal text-primary hover:underline focus-visible:outline focus-visible:outline-primary"
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      )}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-4 border-b border-border/50 py-3">
      <dt className="max-w-[40%] shrink-0 pt-1 text-[10px] font-medium uppercase tracking-[0.14em] text-ink-soft">
        {label}
      </dt>
      <dd className="min-w-0 text-right text-sm font-semibold tabular-nums text-ink">{children}</dd>
    </div>
  );
}

function GroupTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-4 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
      <span className="h-px flex-1 bg-border/60" />
      {children}
      <span className="h-px flex-[6] bg-border/60" />
    </h3>
  );
}

export function RepairSummary({
  summary,
  report,
  header,
  done,
  onAttempts,
}: {
  summary: ExecutiveSummaryModel;
  report?: RunReportModel | null;
  header: WorkspaceHeaderModel;
  done: boolean;
  onAttempts: () => void;
}) {
  const settled =
    done ||
    ["completed", "blocked", "failed"].includes(header.status ?? "") ||
    summary.decision === "blocked" ||
    summary.decision === "failed";
  const title = !settled
    ? "Repair in progress"
    : summary.decision === "blocked"
      ? "Repair stopped"
      : summary.decision === "failed"
        ? "Run failed"
        : summary.decision === "merge"
          ? "Patch eligible for review"
          : "Review required";
  const publication = report?.publication;
  const prUrl = publication?.status === "published" ? report?.prUrl || publication.pr_url : null;
  const publicationText = prUrl
    ? "PR published"
    : publication?.status === "failed"
      ? "PR publication failed"
      : publication?.status === "dry_run"
        ? "No PR created — dry run"
        : publication?.status === "disabled"
          ? "No PR created — publication disabled"
          : "Publication not confirmed";
  const failure = summary.latestFailure;
  const button =
    "rounded-md border border-border px-3 py-2 text-xs font-medium text-ink hover:bg-surface-muted focus-visible:outline focus-visible:outline-primary";
  return (
    <section
      aria-label="Repair summary"
      className="rounded-2xl border border-border bg-card p-6 sm:p-8"
    >
      <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.14em] text-primary">
        <Sparkles className="h-3.5 w-3.5" />
        Repair Summary
      </div>
      <div className="mt-7 grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(300px,380px)]">
        <div className="min-w-0 space-y-8">
          <div>
            <GroupTitle>Execution</GroupTitle>
            <dl className="grid grid-cols-1 gap-x-10 gap-y-2 sm:grid-cols-2">
              <Fact label="Repository">
                <ExpandableText text={summary.repository} label="Repository" />
              </Fact>
              <Fact label="Runtime">
                <span className="font-mono">{summary.runtime || "—"}</span>
              </Fact>
              <Fact label="Attempts">
                <span title={`${summary.recordedAttempts ?? summary.attempts} recorded attempts`}>
                  {summary.recordedAttempts ?? summary.attempts}
                </span>
              </Fact>
              {summary.executedValidations !== undefined && (
                <Fact label="Validations">
                  <span title={`${summary.executedValidations} executed validations`}>
                    {summary.executedValidations}
                  </span>
                </Fact>
              )}
            </dl>
          </div>
          <div>
            <GroupTitle>Analysis</GroupTitle>
            <dl className="grid grid-cols-1 gap-x-10 gap-y-2 sm:grid-cols-2">
              <Fact label="Bug">
                <ExpandableText
                  text={summary.bug || summary.issueTitle || "Not measured"}
                  label="Bug"
                />
              </Fact>
              <Fact label="Severity">
                <span
                  className={`inline-flex rounded-md border px-2 py-1 text-xs ${summary.severity === "HIGH" || summary.severity === "CRITICAL" ? "border-status-failed/30 bg-status-failed/10 text-status-failed" : "border-border text-ink-soft"}`}
                >
                  {summary.severity}
                </span>
              </Fact>
              <Fact label="Root cause">
                <ExpandableText text={summary.rootCause} label="Root cause" />
              </Fact>
              <Fact label="Investigator confidence">
                <span className="font-mono">{summary.confidence}</span>
              </Fact>
              <Fact label="Files affected">
                {summary.impactScopeCount ?? summary.filesAffected ?? "Unmeasured"}
              </Fact>
              <Fact label="Mutation score">
                <span className="font-mono">{summary.mutationScore}</span>
              </Fact>
            </dl>
          </div>
        </div>
        <div className="min-w-0 rounded-xl border border-violet-400/30 bg-violet-400/5 p-6 [&_button]:text-violet-400">
          <h3 className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
            Final Decision
          </h3>
          <h2 className="mt-3 flex items-center gap-2.5 text-[22px] font-bold tracking-tight text-violet-400">
            <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-full bg-current" />
            {title}
          </h2>
          <div className="mt-6 flex flex-wrap items-baseline justify-between gap-2 border-t border-border/50 pt-4">
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
              Trust score
            </span>
            <span className="font-mono text-2xl font-semibold text-ink">{summary.trustScore}</span>
          </div>
          <div className="mt-5 text-[13px] text-ink">
            <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-soft">
              Reason
            </h3>
            <ExpandableText
              text={
                settled
                  ? summary.decisionReason || header.decisionLabel
                  : "Collecting repair evidence."
              }
              label="Decision reason"
            />
          </div>
          <div aria-label="Verification" className="mt-6 flex flex-wrap gap-1.5">
            {(summary.verification ?? []).map((row) => (
              <div
                key={row.label}
                className={`max-w-full rounded-md border border-current/20 px-2.5 py-1 text-[11px] ${tones[row.status]}`}
              >
                <span className="font-medium">{row.label}: </span>
                <span data-evidence-state={row.status}>{row.detail}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <footer className="mt-6 space-y-3 border-t border-border pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-ink-soft">{publicationText}</p>
          <div className="flex flex-wrap gap-2">
            {!!(summary.recordedAttempts ?? report?.recoveryAttempts?.length) && (
              <button className={button} onClick={onAttempts}>
                Compare repair attempts
              </button>
            )}
            {prUrl && (
              <a className={button} href={prUrl} target="_blank" rel="noopener noreferrer">
                Open PR
              </a>
            )}
          </div>
        </div>
        {!!summary.changedFiles?.length && (
          <details className="text-xs text-ink-soft">
            <summary className="cursor-pointer">
              Changed files ({summary.changedFiles.length})
            </summary>
            <ul className="mt-2 space-y-1 font-mono">
              {summary.changedFiles.map((file) => (
                <li className="break-all" key={file}>
                  {file}
                </li>
              ))}
            </ul>
          </details>
        )}
        {publication?.reason && (
          <details className="text-xs text-ink-soft">
            <summary className="cursor-pointer">Publication details</summary>
            <p className="mt-2 break-words leading-relaxed">{publication.reason}</p>
          </details>
        )}
        {failure && (
          <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-xs font-medium text-ink">
              Latest failing test evidence
            </summary>
            <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-xs text-ink-soft">
              {failure}
            </pre>
          </details>
        )}
      </footer>
    </section>
  );
}
