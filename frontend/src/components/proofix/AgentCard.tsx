import {
  GitBranch,
  FileCode,
  Brain,
  Network,
  ShieldCheck,
  FlaskConical,
  Search,
  GitBranchPlus,
  Route,
  Wrench,
  Dna,
  GitPullRequest,
  ChevronDown,
  ChevronRight,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

const AGENT_ICONS: Record<string, LucideIcon> = {
  "Repository Intelligence": Brain,
  "Dependency Analyzer": Network,
  "Static Analysis": ShieldCheck,
  "Failure Reproduction": FlaskConical,
  "Root Cause Analysis": Search,
  "Blast Radius": GitBranchPlus,
  "Repair Planner": Route,
  "Patch Generator": Wrench,
  "Mutation Validation": Dna,
  "Mergeability Router": GitPullRequest,
};
import type { LiveAgent } from "./useExecutionRun";
import { StatusBadge } from "./StatusBadge";
import { StatusIcon } from "./StatusIcon";
import { AgentVisualization } from "./AgentVisualization";
import { AnimatedNumber } from "./AnimatedNumber";

interface Props {
  entry: LiveAgent;
  agentIndex: number;
  expanded: boolean;
  active: boolean;
  onSelect: () => void;
  disabled?: boolean;
  outputLabel?: string;
  /**
   * Replaces `<AgentVisualization>` in the "Live view" box. A handful of
   * agents (A0.7, A0.5) publish a richer, persistent board rather than a
   * timed animation — this is how the workspace swaps it in without
   * `AgentVisualization` needing to know those boards exist.
   */
  liveView?: React.ReactNode;
  /**
   * Collapses the generic Activity feed / Supporting metrics sections below
   * the Live view box to a closed disclosure, instead of showing them open by
   * default. `Workspace` passes both as `true` for every agent, so every
   * card lands closed on first render — the values are real backend data,
   * not invented, so they stay one click away rather than being hidden
   * outright. `false`/unset falls back to the old always-open rendering with
   * no toggle chrome, which no current caller uses but which components
   * embedding `AgentCard` outside `Workspace` (e.g. a future standalone
   * agent view) may still want.
   */
  collapseActivity?: boolean;
  collapseMetrics?: boolean;
}

export function AgentCard({
  entry,
  agentIndex,
  expanded,
  active,
  onSelect,
  disabled,
  outputLabel,
  liveView,
  collapseActivity,
  collapseMetrics,
}: Props) {
  const isRunning = entry.liveStatus === "running";
  const isFailed = entry.liveStatus === "failed";
  const isCompleted = entry.liveStatus === "completed" || entry.liveStatus === "draft";

  // Closed by default only when the caller opted the card into collapsing;
  // every other agent gets `true` here and the toggle button never renders.
  const [activityOpen, setActivityOpen] = useState(!collapseActivity);
  const [metricsOpen, setMetricsOpen] = useState(!collapseMetrics);

  // timeline dot color
  const dotClass = isRunning
    ? "bg-status-running ring-4 ring-status-running/20 animate-soft-pulse"
    : isFailed
      ? "bg-status-failed"
      : isCompleted
        ? "bg-status-completed"
        : "bg-border";

  // Short highlights shown in the collapsed card body — first 2-3 execution lines.
  const context = entry.visualization?.kind === "context" ? entry.visualization.data : null;
  const contextHighlights =
    context && !context.skipped && isCompleted
      ? [
          entry.lines[0] || "Prepared repair context",
          `${context.contextFunctions} functions from ${context.contextFiles} files included`,
          context.tokenReduction !== null
            ? `${Math.round(context.tokenReduction * 100)}% estimated token reduction`
            : "Token reduction not measured",
        ]
      : null;
  const highlights =
    !expanded && (isCompleted || isFailed) ? (contextHighlights ?? entry.lines.slice(0, 3)) : [];

  return (
    <div className="relative pl-8">
      {/* timeline rail */}
      <span className="absolute left-[11px] top-0 h-full w-px bg-border" aria-hidden />
      <span
        className={`absolute left-[6px] top-5 h-3 w-3 rounded-full transition-all ${dotClass}`}
        aria-hidden
      />
      <article
        data-execution-agent-index={agentIndex}
        data-execution-agent-active={active ? "true" : undefined}
        onClick={disabled ? undefined : onSelect}
        aria-disabled={disabled || undefined}
        style={{ scrollMarginTop: 96 }}
        className={`animate-card-in rounded-2xl border bg-surface transition-[transform,border-color,box-shadow,background-color] duration-150 ease-out ${
          disabled
            ? "cursor-not-allowed border-border opacity-50"
            : "cursor-pointer " +
              (active
                ? "border-primary/40 shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_-12px_hsl(var(--primary)/0.25)]"
                : "border-border hover:-translate-y-[1px] hover:scale-[1.005] hover:border-primary/30 hover:shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_20px_-14px_hsl(var(--primary)/0.22)]")
        }`}
      >
        <header
          data-execution-agent-header="true"
          className={`flex items-start gap-4 ${expanded ? "p-5" : "px-5 pt-3.5 pb-1"}`}
        >
          <div
            className={`flex shrink-0 items-center justify-center rounded-lg bg-surface-muted font-mono text-xs font-medium text-ink-soft ${expanded ? "h-9 w-9" : "h-7 w-7 text-[11px]"}`}
          >
            {String(entry.index).padStart(2, "0")}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-3">
              {(() => {
                const Icon = AGENT_ICONS[entry.agent];
                return Icon ? <Icon className="h-4 w-4 text-ink-soft" aria-hidden /> : null;
              })()}
              <h3 className="text-base font-semibold text-ink">{entry.agent}</h3>
              <StatusBadge status={entry.liveStatus} pulse={isRunning} />
              {!expanded && outputLabel && (isCompleted || isFailed) && (
                <span className="inline-flex items-center rounded-md bg-surface-muted px-1.5 py-0.5 font-mono text-[10px] text-ink-soft">
                  {outputLabel}
                </span>
              )}
              <span className="ml-auto font-mono text-xs text-ink-soft">
                {isRunning ? "…" : entry.duration}
              </span>
            </div>
            {expanded && <p className="mt-1 text-sm text-ink-soft">{entry.purpose}</p>}
          </div>
        </header>

        {highlights.length > 0 && (
          <ul className="px-5 pb-3 pl-[52px] space-y-0.5">
            {highlights.map((line, i) => (
              <li key={i} className="truncate text-xs text-ink-soft/80 leading-relaxed">
                <span className="mr-1.5 text-ink-soft/40">·</span>
                {line}
              </li>
            ))}
          </ul>
        )}

        <div
          // Every interactive element a per-agent panel renders (toggles,
          // expandable rows, "why" disclosures) lives inside this box. The
          // card's own expand/collapse is wired to `onClick` on the whole
          // `<article>` above, so without this stop, clicking any of those
          // controls bubbled up and re-toggled — and often closed — the
          // entire card the user was trying to look inside.
          onClick={(e) => e.stopPropagation()}
          className={`grid transition-all duration-[250ms] ease-out ${
            expanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
          }`}
        >
          <div className="overflow-hidden">
            <div className="px-5 pb-6 pt-1">
              {/* Hero visualization — the specialist's workstation */}
              <section
                aria-label="Agent visualization"
                className="overflow-hidden rounded-xl border border-border/70 bg-surface-muted/40"
              >
                <div className="flex items-center justify-between border-b border-border/60 px-3 py-1.5">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
                    Live view
                  </span>
                  {isRunning && (
                    <span className="inline-flex items-center gap-1.5 font-mono text-[10px] text-ink-soft">
                      <span
                        className="h-1.5 w-1.5 rounded-full bg-status-running animate-soft-pulse"
                        aria-hidden
                      />
                      streaming
                    </span>
                  )}
                </div>
                <div className="p-3">{liveView ?? <AgentVisualization entry={entry} />}</div>
              </section>

              {/* Live activity feed */}
              <div className="mt-5">
                {collapseActivity ? (
                  <button
                    type="button"
                    onClick={() => setActivityOpen((o) => !o)}
                    aria-expanded={activityOpen}
                    className="mb-2 flex w-full items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-ink-soft transition-colors hover:text-ink"
                  >
                    {activityOpen ? (
                      <ChevronDown className="h-3 w-3 flex-none" aria-hidden />
                    ) : (
                      <ChevronRight className="h-3 w-3 flex-none" aria-hidden />
                    )}
                    Activity
                    <span className="ml-auto font-mono text-[10px] font-normal normal-case tracking-normal text-ink-soft/50">
                      {entry.lines.length} {entry.lines.length === 1 ? "line" : "lines"}
                    </span>
                  </button>
                ) : (
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-ink-soft">
                    Activity
                  </div>
                )}
                {activityOpen && (
                  <ul className="space-y-2">
                    {entry.lines.slice(0, entry.visibleLines).map((line, i) => {
                      const isLastVisible = i === entry.visibleLines - 1;
                      const isLastLine = i === entry.lines.length - 1;
                      if (isRunning) {
                        if (isLastVisible) {
                          return (
                            <li
                              key={i}
                              className="animate-line-in flex items-start gap-2.5 text-sm text-ink"
                            >
                              <span
                                className="mt-0.5 h-4 w-4 shrink-0 rounded-full border-2 border-status-running animate-soft-pulse"
                                aria-hidden
                              />
                              <span className="leading-relaxed">
                                {line}
                                <span
                                  className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] bg-status-running align-middle animate-caret-blink"
                                  aria-hidden
                                />
                              </span>
                            </li>
                          );
                        }
                        return (
                          <li
                            key={i}
                            className="animate-line-in flex items-start gap-2.5 text-sm text-ink-soft"
                          >
                            <span
                              className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-ink-soft/50"
                              aria-hidden
                            />
                            <span className="leading-relaxed">{line}</span>
                          </li>
                        );
                      }
                      const failMark = isFailed && isLastLine;
                      return (
                        <li
                          key={i}
                          className="animate-line-in flex items-start gap-2.5 text-sm text-ink"
                        >
                          <StatusIcon ok={!failMark} className="mt-0.5" />
                          <span className="leading-relaxed">{line}</span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              {entry.modifiedFiles && (
                <div className="mt-5">
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-ink-soft">
                    <GitBranch className="h-3.5 w-3.5" /> Modified files
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {entry.modifiedFiles.map((f) => (
                      <span
                        key={f}
                        className="inline-flex items-center gap-1.5 rounded-md bg-accent px-2 py-1 font-mono text-xs text-accent-foreground"
                      >
                        <FileCode className="h-3 w-3" />
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {entry.pills && !entry.modifiedFiles && (
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {entry.pills.map((p) => (
                    <span
                      key={p}
                      className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-xs text-ink-soft"
                    >
                      <FileCode className="h-3 w-3 text-ink-soft" />
                      {p}
                    </span>
                  ))}
                </div>
              )}

              {/* Supporting metrics — reduced dominance, at the bottom */}
              {entry.metrics && (
                <div className="mt-5 border-t border-border/60 pt-3">
                  {collapseMetrics ? (
                    <button
                      type="button"
                      onClick={() => setMetricsOpen((o) => !o)}
                      aria-expanded={metricsOpen}
                      className="mb-2 flex w-full items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft/80 transition-colors hover:text-ink"
                    >
                      {metricsOpen ? (
                        <ChevronDown className="h-3 w-3 flex-none" aria-hidden />
                      ) : (
                        <ChevronRight className="h-3 w-3 flex-none" aria-hidden />
                      )}
                      Supporting metrics
                      <span className="ml-auto font-mono text-[10px] font-normal normal-case tracking-normal text-ink-soft/50">
                        {entry.metrics.length} {entry.metrics.length === 1 ? "metric" : "metrics"}
                      </span>
                    </button>
                  ) : (
                    <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-ink-soft/80">
                      Supporting metrics
                    </div>
                  )}
                  {metricsOpen && (
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
                      {entry.metrics.map((m) => (
                        <div key={m.label} className="flex items-baseline justify-between gap-3">
                          <dt className="text-[10px] uppercase tracking-wider text-ink-soft/70">
                            {m.label}
                          </dt>
                          <dd className="font-mono text-xs font-medium text-ink/90 tabular-nums">
                            <AnimatedNumber value={m.value} />
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </article>
    </div>
  );
}
