/**
 * A3 — Priority Lens.
 *
 * Everything on this panel comes from `GET /api/runs/{runId}/static-findings`
 * (`services/ui_projection.py::build_static_findings`, sourced from A3's own
 * `StaticAnalysisReport`). No severity band, cluster count, consensus verdict
 * or ranking is computed client-side: `severity` renders exactly as A3
 * recorded it, `severityMeasured` gates whether it is shown as a number at
 * all (bandit alone derives severity from a real per-finding measurement —
 * semgrep and ruff assign a scanner-wide constant), `tools[]` is the only
 * source for how many scanners agree, and `findings` is already the
 * backend's own `blast_radius_score` ordering, never re-sorted here.
 *
 * A0.5, A1 and A2 are grouped views over the repository. A3's own work is a
 * *pipeline* (scan → cluster → prioritize) whose central question is "why
 * does this finding outrank the others" — so this panel is built around
 * A3's real scoring shape,
 *
 *     blast_radius_score = severity × criticality × log(tools+1) × (1+churn)
 *
 * as a visual convergence of four independently real factors into one rank,
 * never as a literal recomputable formula (the frontend does not have, and
 * must not derive, the constants A3's own scoring uses). `blastRadiusScore`
 * has no fixed upper bound — unlike severity/criticality/churn, which the
 * backend guarantees to [0, 1] — so its bar is drawn relative to the
 * highest-scoring finding *in this run's own top 8*, not against an
 * absolute scale, and is labelled as such.
 *
 * There is no synthetic "scanning" animation, no fake progress, and no
 * fabricated "clustered" count: A3's durable contract has `rawCount` and the
 * final `prioritizedCount` but not the pre-truncation cluster total, so the
 * clustering step is described as a transformation, never given an invented
 * number. `findings` is already capped at 8 by the backend, so this panel
 * never paginates, virtualizes, or truncates further — the same layout
 * applies whether `rawCount` is 12 or 15,000.
 */
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, XCircle, HelpCircle } from "lucide-react";
import type { ScannerOutcome, StaticFinding, StaticFindingsReport } from "./staticFindingsTypes";
import { getStaticFindings } from "@/lib/runService";
import type { AgentStatus } from "./data";

const STATUS_LABEL: Record<ScannerOutcome, string> = {
  ok: "ready",
  ok_no_findings: "no findings",
  unavailable: "unavailable",
  stubbed: "stubbed",
};

/** `ok`/`ok_no_findings` both mean the scanner actually ran — the only two
 * outcomes that can contribute real cross-tool agreement. */
function scannerRan(outcome: ScannerOutcome): boolean {
  return outcome === "ok" || outcome === "ok_no_findings";
}

const STATUS_ICON: Record<ScannerOutcome, typeof CheckCircle2> = {
  ok: CheckCircle2,
  ok_no_findings: CheckCircle2,
  unavailable: XCircle,
  stubbed: HelpCircle,
};

const STATUS_CLASS: Record<ScannerOutcome, string> = {
  ok: "text-status-completed",
  ok_no_findings: "text-ink-soft",
  unavailable: "text-status-failed",
  stubbed: "text-status-retry",
};

/** Both from the same "high" convention used across the app: criticality and
 * churn are backend-guaranteed to [0, 1] (criticality from A1's
 * `compute_criticality`, churn from `get_churn_weights`'s own-max division),
 * so a fixed 0.7 cutoff is a stable, meaningful threshold — not a per-run
 * invention — for the one-line qualitative label under each bar. Below the
 * threshold, no label is shown rather than inventing a "low" descriptor the
 * backend has no corresponding tier for. */
const HIGH_THRESHOLD = 0.7;

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load static analysis</span>
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

// -------------------------------------------------------------- triage funnel

/**
 * Raw findings narrowing to the prioritized set, drawn as a literal wedge
 * rather than a KPI row — the shape itself is the reduction. No cluster
 * count is shown: A3's durable contract has no pre-truncation cluster
 * total, so inventing one here would be exactly the fabrication the backend
 * comment on `StaticAnalysisReport` warns against.
 */
function TriageFunnel({
  rawCount,
  prioritizedCount,
}: {
  rawCount: number;
  prioritizedCount: number;
}) {
  return (
    <div className="flex items-center gap-4">
      <div>
        <div className="font-mono text-[32px] font-semibold leading-none text-ink">{rawCount}</div>
        <div className="mt-1 text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          raw finding{rawCount === 1 ? "" : "s"}
        </div>
      </div>
      <div aria-hidden className="h-9 w-14 shrink-0">
        <div
          className="h-full w-full bg-ink-soft/20"
          style={{ clipPath: "polygon(0 8%, 100% 32%, 100% 68%, 0 92%)" }}
        />
      </div>
      <div>
        <div className="font-mono text-[32px] font-semibold leading-none text-primary">
          {prioritizedCount}
        </div>
        <div className="mt-1 text-[9px] font-medium uppercase tracking-wider text-primary/80">
          prioritized
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------- scanner status strip

/**
 * Every scanner A3 actually invoked, from `scannerStatus` directly — never
 * inferred from which tool names happen to appear on a finding (a scanner
 * that found nothing, or that never ran, would otherwise vanish silently).
 * Unavailable and stubbed scanners are shown, not hidden: they cap how much
 * cross-tool agreement is even possible.
 */
function ScannerStatusStrip({ scannerStatus }: { scannerStatus: Record<string, ScannerOutcome> }) {
  const entries = Object.entries(scannerStatus);
  if (entries.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <span className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/60">
        Scanners
      </span>
      {entries.map(([name, outcome]) => {
        const Icon = STATUS_ICON[outcome];
        return (
          <span key={name} className="flex items-center gap-1.5 text-[10px]">
            <Icon className={`h-3 w-3 shrink-0 ${STATUS_CLASS[outcome]}`} aria-hidden />
            <span className="capitalize text-ink-soft">{name}</span>
            <span className={STATUS_CLASS[outcome]}>{STATUS_LABEL[outcome]}</span>
          </span>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------- factor indicators

type FactorSize = "full" | "compact";

/** Severity. Hatched and unlabelled when unmeasured — the one rule this
 * whole panel exists to protect: an estimated/hardcoded severity must never
 * read as a measurement. */
function SeverityFactor({ finding, size }: { finding: StaticFinding; size: FactorSize }) {
  const measured = finding.severityMeasured;
  return (
    <div>
      <div
        className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70"
        title={
          measured
            ? "Measured severity supplied by the scanner."
            : "Severity was not directly measured for this finding."
        }
      >
        Severity
      </div>
      <div
        className={`mt-1 overflow-hidden rounded-full bg-ink-soft/15 ${size === "full" ? "h-1.5" : "h-1"}`}
      >
        {measured ? (
          <div
            className="h-full rounded-full bg-ink-soft/80"
            style={{ width: `${Math.round(finding.severity * 100)}%` }}
          />
        ) : (
          <div
            className="h-full w-full opacity-60"
            style={{
              backgroundImage:
                "repeating-linear-gradient(135deg, var(--ink-soft) 0 3px, transparent 3px 6px)",
            }}
            aria-hidden
          />
        )}
      </div>
      <div className="mt-1 flex items-baseline gap-1 font-mono text-[10px]">
        {measured ? (
          <>
            <span className="text-ink">{finding.severity.toFixed(2)}</span>
            <span className="font-sans text-[8px] uppercase tracking-wider text-ink-soft/60">
              measured
            </span>
          </>
        ) : (
          <span className="font-sans text-[9px] uppercase tracking-wider text-status-retry">
            Not measured
          </span>
        )}
      </div>
    </div>
  );
}

/** Agreement. One dot per entry in `tools[]` — never a count derived from
 * `consensus` alone, and never more dots than scanners actually reported. */
function AgreementFactor({ finding, size }: { finding: StaticFinding; size: FactorSize }) {
  const n = finding.tools.length;
  return (
    <div>
      <div
        className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70"
        title="Number of independent scanners reporting this finding."
      >
        Agreement
      </div>
      <div className={`mt-1.5 flex items-center gap-1 ${size === "full" ? "" : "gap-0.5"}`}>
        {n === 0 ? (
          <span className="h-1.5 w-1.5 rounded-full border border-dashed border-ink-soft/40" />
        ) : (
          Array.from({ length: n }, (_, i) => (
            <span
              key={i}
              className={`rounded-full ${finding.consensus ? "bg-primary" : "bg-ink-soft/60"} ${
                size === "full" ? "h-1.5 w-1.5" : "h-1 w-1"
              }`}
              aria-hidden
            />
          ))
        )}
      </div>
      <div className="mt-1 font-mono text-[10px] text-ink">
        {n === 0 ? (
          <span className="font-sans text-ink-soft">no scanner attribution</span>
        ) : (
          <>
            {n} scanner{n === 1 ? "" : "s"}
            {size === "full" && (
              <span className="ml-1 font-sans text-[9px] text-ink-soft">
                ({finding.tools.join(", ")})
              </span>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Criticality — A1's structural-importance signal, used here purely as a
 * ranking input. This is not A1's Risk Terrain; A3 only ever shows this one
 * file's own value. */
function CriticalityFactor({ finding, size }: { finding: StaticFinding; size: FactorSize }) {
  const high = finding.criticality >= HIGH_THRESHOLD;
  return (
    <div>
      <div
        className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70"
        title="Semantic criticality derived from A1's repository risk model."
      >
        Criticality
      </div>
      <div
        className={`mt-1 overflow-hidden rounded-full bg-ink-soft/15 ${size === "full" ? "h-1.5" : "h-1"}`}
      >
        <div
          className="h-full rounded-full bg-ink-soft/80"
          style={{ width: `${Math.round(finding.criticality * 100)}%` }}
        />
      </div>
      <div className="mt-1 font-mono text-[10px] text-ink">
        {finding.criticality.toFixed(2)}
        {size === "full" && high && (
          <span className="ml-1.5 font-sans text-[9px] uppercase tracking-wider text-ink-soft/60">
            load-bearing file
          </span>
        )}
      </div>
    </div>
  );
}

/** Churn — git activity on the file, backend-supplied. */
function ChurnFactor({ finding, size }: { finding: StaticFinding; size: FactorSize }) {
  const high = finding.churnWeight >= HIGH_THRESHOLD;
  return (
    <div>
      <div
        className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70"
        title="Repository change activity weight."
      >
        Churn
      </div>
      <div
        className={`mt-1 overflow-hidden rounded-full bg-ink-soft/15 ${size === "full" ? "h-1.5" : "h-1"}`}
      >
        <div
          className="h-full rounded-full bg-ink-soft/80"
          style={{ width: `${Math.round(finding.churnWeight * 100)}%` }}
        />
      </div>
      <div className="mt-1 font-mono text-[10px] text-ink">
        {finding.churnWeight.toFixed(2)}
        {size === "full" && high && (
          <span className="ml-1.5 font-sans text-[9px] uppercase tracking-wider text-ink-soft/60">
            frequently changed
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * The four factors, side by side, for one finding. Desktop keeps all four
 * visible in a row; narrow widths wrap to two-and-two rather than
 * introducing horizontal scroll.
 */
function FactorRow({ finding, size }: { finding: StaticFinding; size: FactorSize }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
      <SeverityFactor finding={finding} size={size} />
      <AgreementFactor finding={finding} size={size} />
      <CriticalityFactor finding={finding} size={size} />
      <ChurnFactor finding={finding} size={size} />
    </div>
  );
}

/** A single muted glyph between factors — typographic rhythm only, never
 * paired with numbers, so it reads as "these combine" rather than as an
 * arithmetic expression a reader might try to verify. */
function EquationGlyph({ symbol }: { symbol: string }) {
  return (
    <span aria-hidden className="hidden self-center px-0.5 text-[12px] text-ink-soft/30 sm:block">
      {symbol}
    </span>
  );
}

/**
 * The full equation, left to right: the four evidence factors, then a
 * connector, then the converging priority result — the read order the
 * backend's own ranking actually follows. On narrow screens the factors
 * stack two-by-two and a single "↓" leads into priority instead.
 */
function EquationRow({ finding, maxScore }: { finding: StaticFinding; maxScore: number }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-1">
      <div className="grid flex-1 grid-cols-2 gap-x-4 gap-y-3 sm:flex sm:items-center sm:gap-1">
        <div className="sm:min-w-0 sm:flex-1">
          <SeverityFactor finding={finding} size="full" />
        </div>
        <EquationGlyph symbol="×" />
        <div className="sm:min-w-0 sm:flex-1">
          <AgreementFactor finding={finding} size="full" />
        </div>
        <EquationGlyph symbol="×" />
        <div className="sm:min-w-0 sm:flex-1">
          <CriticalityFactor finding={finding} size="full" />
        </div>
        <EquationGlyph symbol="×" />
        <div className="sm:min-w-0 sm:flex-1">
          <ChurnFactor finding={finding} size="full" />
        </div>
      </div>
      <span aria-hidden className="flex justify-center text-[13px] text-primary/50 sm:hidden">
        ↓
      </span>
      <span aria-hidden className="hidden self-center px-1 text-[14px] text-primary/50 sm:block">
        →
      </span>
      <div className="sm:w-40">
        <PriorityResult finding={finding} maxScore={maxScore} size="full" />
      </div>
    </div>
  );
}

/**
 * The converging result. `blastRadiusScore` is rendered, never recomputed —
 * the bar width is relative to the highest score among the findings on
 * screen (`maxScore`), because the score itself has no fixed ceiling the
 * way the four factors above it do.
 */
function PriorityResult({
  finding,
  maxScore,
  size,
}: {
  finding: StaticFinding;
  maxScore: number;
  size: FactorSize;
}) {
  const pct = maxScore > 0 ? Math.max(4, (finding.blastRadiusScore / maxScore) * 100) : 0;
  return (
    <div className={size === "full" ? "w-full sm:w-40" : "w-24"}>
      <div className="flex items-baseline justify-between">
        <span
          className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70"
          title="Relative blast-radius score calculated by A3."
        >
          Priority{size === "full" && <span className="normal-case"> · relative</span>}
        </span>
        <span className="font-mono text-[10px] font-semibold text-ink">#{finding.rank}</span>
      </div>
      <div
        className={`mt-1 overflow-hidden rounded-full bg-ink-soft/15 ${size === "full" ? "h-2" : "h-1.5"}`}
      >
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 font-mono text-[11px] font-semibold text-ink">
        {finding.blastRadiusScore.toFixed(2)}
      </div>
    </div>
  );
}

// --------------------------------------------------------------- why-ranked

/** Rule-based, generated only from fields actually present on this finding —
 * never a fabricated narrative. Falls back to an honest statement when no
 * single factor crosses the "high" threshold: the rank still came from the
 * product of four real factors, it just was not driven by any one of them. */
function whyThisRanksHigh(finding: StaticFinding): string[] {
  const reasons: string[] = [];
  if (finding.severityMeasured && finding.severity >= HIGH_THRESHOLD) {
    reasons.push("High measured severity");
  } else if (!finding.severityMeasured) {
    reasons.push(
      "Severity not independently measured — ranked on agreement, criticality and churn",
    );
  }
  if (finding.tools.length >= 2 || finding.consensus) {
    reasons.push(
      `Independent scanner agreement (${finding.tools.length} scanner${finding.tools.length === 1 ? "" : "s"})`,
    );
  }
  if (finding.criticality >= HIGH_THRESHOLD) {
    reasons.push("High structural criticality");
  }
  if (finding.churnWeight >= HIGH_THRESHOLD) {
    reasons.push("Active code churn");
  }
  if (reasons.length === 0) {
    reasons.push(
      "Ranked by the combination of severity, agreement, criticality and churn — no single factor dominates",
    );
  }
  return reasons;
}

/**
 * The inline expansion for a selected row — not a separate page, not a
 * navigation away from the lens. Repeats the four factors at full size,
 * then the exact FILE / LINE / FINDING fields, then the generated "why"
 * reasoning, so the row visibly grows in place rather than being replaced.
 */
function WhyThisRanked({ finding }: { finding: StaticFinding }) {
  const reasons = whyThisRanksHigh(finding);
  return (
    <div className="mt-3 border-t border-border/60 pt-3">
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
        Why this ranked #{finding.rank}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <SeverityFactor finding={finding} size="full" />
        <AgreementFactor finding={finding} size="full" />
        <CriticalityFactor finding={finding} size="full" />
        <ChurnFactor finding={finding} size="full" />
      </div>

      <div className="mt-3 flex items-center justify-between">
        <span className="font-mono text-[10px] text-ink-soft">Blast-radius score (A3)</span>
        <span className="font-mono text-[12px] font-semibold text-ink">
          {finding.blastRadiusScore.toFixed(4)}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3 border-t border-border/60 pt-3">
        <div>
          <div className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70">
            File
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-ink" title={finding.file}>
            {finding.file}
          </div>
        </div>
        <div>
          <div className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70">
            Line
          </div>
          <div className="mt-0.5 font-mono text-[11px] text-ink">{finding.line}</div>
        </div>
        <div>
          <div className="text-[8px] font-medium uppercase tracking-wider text-ink-soft/70">
            Finding
          </div>
          <div className="mt-0.5 truncate text-[11px] text-ink" title={finding.message}>
            {finding.message}
          </div>
        </div>
      </div>

      <ul className="mt-3 space-y-1 border-t border-border/60 pt-3">
        {reasons.map((r) => (
          <li key={r} className="flex items-start gap-1.5 text-[11px] text-ink">
            <span className="mt-0.5 text-ink-soft/50" aria-hidden>
              +
            </span>
            {r}
          </li>
        ))}
      </ul>

      {finding.rank === 1 && (
        <div className="mt-3 flex items-center gap-1.5 border-t border-border/60 pt-3 text-[10px] text-ink-soft">
          <ArrowRight className="h-3 w-3 text-primary" aria-hidden />
          Investigate with A4 — Evidence Investigation
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ finding row

function PriorityFindingRow({
  finding,
  maxScore,
  expanded,
  dimmed,
  onToggle,
}: {
  finding: StaticFinding;
  maxScore: number;
  expanded: boolean;
  dimmed: boolean;
  onToggle: () => void;
}) {
  // Only 1–2 findings dominate visually at once by default — the rest stay
  // compact until the user asks for their equation.
  const prominent = finding.rank <= 2;

  if (!prominent && !expanded) {
    return (
      <li className={`transition-opacity ${dimmed ? "opacity-40" : "opacity-100"}`}>
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="flex w-full items-center gap-2.5 rounded-lg border border-border/60 px-2.5 py-1.5 text-left transition-colors hover:border-primary/30 hover:bg-surface-muted/40"
        >
          <span className="shrink-0 rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[9px] font-semibold text-ink-soft">
            #{finding.rank}
          </span>
          <span
            className="min-w-0 flex-1 truncate font-mono text-[10px] text-ink"
            title={finding.file}
          >
            {finding.file}:{finding.line}
          </span>
          <span className="hidden shrink-0 items-center gap-1 sm:flex">
            {finding.tools.map((t) => (
              <span
                key={t}
                className={`h-1 w-1 rounded-full ${finding.consensus ? "bg-primary" : "bg-ink-soft/50"}`}
                aria-hidden
              />
            ))}
          </span>
          <span className="shrink-0 font-mono text-[10px] font-medium text-ink-soft">
            {finding.blastRadiusScore.toFixed(2)}
          </span>
        </button>
      </li>
    );
  }

  return (
    <li className={`transition-opacity ${dimmed ? "opacity-40" : "opacity-100"}`}>
      <div
        className={`rounded-xl border px-3.5 py-3 transition-colors ${
          finding.rank === 1
            ? "border-primary/35 bg-primary/[0.04]"
            : expanded
              ? "border-primary/25 bg-surface-muted/20"
              : "border-border"
        }`}
      >
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="block w-full text-left"
        >
          <div className="flex items-center gap-2">
            <span
              className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${
                finding.rank === 1 ? "bg-primary/15 text-primary" : "bg-surface-muted text-ink-soft"
              }`}
            >
              #{finding.rank}
            </span>
            <span className="truncate font-mono text-[11px] text-ink" title={finding.file}>
              {finding.file}:{finding.line}
            </span>
          </div>
          <div className="mt-1 truncate text-[12px] text-ink-soft">{finding.message}</div>

          {prominent ? (
            <EquationRow finding={finding} maxScore={maxScore} />
          ) : (
            // A manually expanded compact row goes straight to the full
            // `WhyThisRanked` breakdown below — showing this compact summary
            // too would repeat every factor a second time in the same row.
            !expanded && (
              <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center">
                <div className="flex-1">
                  <FactorRow finding={finding} size="compact" />
                </div>
                <div className="sm:w-32">
                  <PriorityResult finding={finding} maxScore={maxScore} size="compact" />
                </div>
              </div>
            )
          )}
        </button>

        {expanded && <WhyThisRanked finding={finding} />}
      </div>
    </li>
  );
}

// ------------------------------------------------------------- priority lens

function PriorityLens({
  findings,
  expandedId,
  onToggle,
}: {
  findings: StaticFinding[];
  expandedId: string | null;
  onToggle: (id: string) => void;
}) {
  const maxScore = useMemo(
    () => findings.reduce((m, f) => Math.max(m, f.blastRadiusScore), 0),
    [findings],
  );

  if (findings.length === 0) {
    return (
      <p className="rounded-lg border border-border bg-surface-muted/30 p-3 text-[11px] text-ink-soft">
        No prioritized findings.
      </p>
    );
  }

  return (
    <ol className="space-y-1.5">
      {findings.map((f) => (
        <PriorityFindingRow
          key={f.id}
          finding={f}
          maxScore={maxScore}
          expanded={f.id === expandedId}
          dimmed={expandedId !== null && expandedId !== f.id}
          onToggle={() => onToggle(f.id)}
        />
      ))}
    </ol>
  );
}

// ------------------------------------------------------------------- panel

export function StaticFindingsPanel({ runId, status }: { runId: string; status?: AgentStatus }) {
  const [report, setReport] = useState<StaticFindingsReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getStaticFindings(runId)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId, attempt, status]);

  const topFinding = report?.findings[0] ?? null;

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Static Analysis
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Static analysis loading…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!report) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Static Analysis
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A3 is scanning now; this panel renders once it publishes."
            : "Pending — A3 has not completed yet."}
        </p>
      </section>
    );
  }

  // rawCount === 0 is a real, completed scan that found nothing to
  // prioritize — never presented as "clean", since A3 does not itself make
  // a safety claim, only a triage one. The two zero-finding cases are
  // different facts and must read differently: no scanner ran at all is a
  // coverage gap, not a result.
  if (report.rawCount === 0 && report.prioritizedCount === 0) {
    const anyScannerRan = Object.values(report.scannerStatus).some(scannerRan);
    return (
      <section className="space-y-3 rounded-2xl border border-border bg-surface p-4">
        <div>
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            Static Analysis
          </h3>
          <p className="mt-0.5 text-[15px] font-semibold text-ink">Priority Lens</p>
        </div>
        <p className="text-[12px] text-ink">No findings require prioritization.</p>
        <div className="font-mono text-[32px] font-semibold leading-none text-ink-soft/60">0</div>
        <div className="-mt-2 text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          prioritized
        </div>
        <ScannerStatusStrip scannerStatus={report.scannerStatus} />
        <p className="text-[11px] text-ink-soft">
          {anyScannerRan
            ? "No findings reported by the available scanners."
            : "Priority ranking could not be established because no scanner produced findings."}
        </p>
      </section>
    );
  }

  const summary = `${report.rawCount} finding${report.rawCount === 1 ? "" : "s"} distilled into ${
    report.prioritizedCount
  } priorit${report.prioritizedCount === 1 ? "y" : "ies"} — ranked by blast radius.`;

  return (
    <section className="space-y-4 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Static Analysis
        </h3>
        <p className="mt-0.5 text-[15px] font-semibold text-ink">Priority Lens</p>
        <p className="text-[11px] text-ink-soft">{summary}</p>
      </div>

      <TriageFunnel rawCount={report.rawCount} prioritizedCount={report.prioritizedCount} />
      <ScannerStatusStrip scannerStatus={report.scannerStatus} />

      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/70">
        Why this finding ranks here
      </div>
      <PriorityLens
        findings={report.findings}
        expandedId={selectedId}
        onToggle={(id) => setSelectedId((cur) => (cur === id ? null : id))}
      />

      {topFinding && (
        <div className="flex items-center gap-1.5 border-t border-border/60 pt-3 text-[10px] text-ink-soft">
          <ArrowRight className="h-3 w-3 text-primary" aria-hidden />
          Next — Evidence Investigation examines{" "}
          <span className="font-mono text-ink-soft">
            {topFinding.file}:{topFinding.line}
          </span>
        </div>
      )}

      <p className="text-[10px] text-ink-soft">
        Explain: findings are A3&apos;s own scan-cluster-rank pipeline over this repository&apos;s
        source — distinct from A0.5&apos;s structure, A1&apos;s roles, and A2&apos;s dependency
        risk.{" "}
        {report.findings.length > 0 && (
          <>
            Priority bars are relative to the highest-scoring finding in this set, not an absolute
            scale.{" "}
          </>
        )}
        Source: <code className="font-mono">GET /api/runs/{"{run_id}"}/static-findings</code>
      </p>
    </section>
  );
}
