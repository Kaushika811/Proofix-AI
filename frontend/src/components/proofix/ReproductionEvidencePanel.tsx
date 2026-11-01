/**
 * A3.5 — Runtime Observation Record.
 *
 * Everything on this panel comes from `GET /api/runs/{runId}/reproduction`
 * (`services/ui_projection.py::build_reproduction_evidence`, sourced from
 * A3.5's own `ReproductionResult`). No verdict, confidence, or stage status
 * is computed client-side.
 *
 * A3.5 is the pipeline's epistemic pivot: A0.5, A1, A2 and A3 all infer from
 * source. A3.5 is the only agent that actually executes the repository and
 * observes whether the suspected failure occurs. This panel answers one
 * question — did the reported failure actually happen when we ran the
 * repository — and is built to make that answer visually undeniable, not to
 * read as a CI run summary:
 *
 *     OBSERVATION → SIGNAL → PROVENANCE → MEASUREMENT → HANDOFF
 *
 * The four backend statuses are four different facts, not one template with
 * a colour swap: CONFIRMED is a positive result, UNCONFIRMED is a *real*
 * negative result (the suite ran clean), NO_TESTS is a coverage gap (nothing
 * to observe with), INFRA_ERROR is an absence of information (the
 * instrument itself failed). Each gets its own hero composition.
 *
 * A3.5 does not target a specific A3 finding — confirmed by reading
 * `orchestrator/nodes.py::reproduction_gate`, which never passes A3's
 * `static_report` into this stage. It is an independent full-suite pytest
 * gate. This panel never implies a finding-to-reproduction link that does
 * not exist.
 *
 * `stages[]` is a deterministic, backend-computed decomposition of the
 * final recorded result — not live progress, and deliberately not rendered
 * as its own stepper (that reads as fake real-time execution). Its detail
 * strings are instead folded into the hero and measurement sections where
 * they explain *why* a non-CONFIRMED result looks the way it does.
 *
 * `reexecutionCommand` is a *different*, targeted command built for A8's
 * later validation pass — it is never executed here. It is always shown
 * beneath a "prepared, not executed by A3.5" label, visually distinct from
 * the command A3.5 actually ran, so the two are never mistaken for the same
 * event.
 */
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, ArrowRight, AlertTriangle } from "lucide-react";
import type { EvidenceSource, ReproductionEvidence } from "./reproductionTypes";
import { getReproductionEvidence } from "@/lib/runService";
import type { AgentStatus } from "./data";

/** The small secondary badge word for each status — kept in A3.5's own
 * enum vocabulary, but never the hero: the observation itself carries the
 * weight. */
const RAW_STATUS_LABEL: Record<ReproductionEvidence["status"], string> = {
  CONFIRMED: "CONFIRMED",
  UNCONFIRMED: "UNCONFIRMED",
  NO_TESTS: "NO TESTS",
  INFRA_ERROR: "INFRASTRUCTURE ERROR",
};

/** The hero headline for the three non-confirmed states — what happened,
 * in plain words, before any raw enum or number. */
const HERO_HEADLINE: Record<Exclude<ReproductionEvidence["status"], "CONFIRMED">, string> = {
  UNCONFIRMED: "No failure observed",
  NO_TESTS: "No test surface",
  INFRA_ERROR: "Observation blocked",
};

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load reproduction evidence</span>
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

function StatChip({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-border bg-surface-muted/60 px-2.5 py-1.5">
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">{label}</div>
      <div className="font-mono text-sm font-semibold text-ink">{value}</div>
      {hint && <div className="mt-0.5 text-[9px] text-ink-soft">{hint}</div>}
    </div>
  );
}

/** So a bare exit code never has to be mentally decoded — every exit-code
 * chip carries its own real-world meaning alongside the number. */
function exitCodeHint(exitCode: number | null): string | undefined {
  if (exitCode === null) return undefined;
  if (exitCode === 0) return "clean exit";
  if (exitCode < 0) return "execution error";
  return "test failure";
}

/** Only shown when A3.5 actually measured a duration for the timed-out run
 * — never a hardcoded timeout constant the backend didn't send. */
function timeoutHint(evidence: ReproductionEvidence): string | undefined {
  if (!evidence.timedOut) return undefined;
  return evidence.durationSeconds !== null
    ? `${evidence.durationSeconds.toFixed(2)}s exceeded`
    : undefined;
}

// -------------------------------------------------------------- observed hero

/** CONFIRMED — the error signature is the hero. The status pill is small and
 * top-right; the observation itself, not the badge, is what's large. Below
 * it, a compact evidence-identity row (file / line / test) — not prose. */
function ConfirmedHero({ evidence }: { evidence: ReproductionEvidence }) {
  return (
    <div className="rounded-xl border border-status-completed/30 bg-status-completed-bg/10 p-4">
      <div className="flex items-start justify-between gap-3">
        <span className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          Observed failure
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold text-status-completed">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-status-completed" />
          {RAW_STATUS_LABEL.CONFIRMED}
        </span>
      </div>
      <div className="mt-2 break-words font-mono text-[20px] font-semibold leading-snug text-ink">
        {evidence.errorSignature ?? "Not measured"}
      </div>
      {(evidence.failingFile || evidence.failingTest) && (
        <dl className="mt-3 grid grid-cols-[auto_auto_1fr] items-baseline gap-x-4 gap-y-0.5 border-t border-status-completed/20 pt-2 text-[11px]">
          <dt className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/70">File</dt>
          <dt className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/70">Line</dt>
          <dt className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/70">Test</dt>
          <dd className="font-mono text-ink">{evidence.failingFile ?? "—"}</dd>
          <dd className="font-mono text-ink">{evidence.failingLine ?? "—"}</dd>
          <dd
            className="truncate font-mono text-ink-soft"
            title={evidence.failingTest ?? undefined}
          >
            {evidence.failingTest ?? "—"}
          </dd>
        </dl>
      )}
    </div>
  );
}

/** UNCONFIRMED — a real negative result, rendered as a clean experimental
 * outcome, never as an error. */
function UnconfirmedHero({ evidence }: { evidence: ReproductionEvidence }) {
  return (
    <div className="rounded-xl border border-border bg-surface-muted/20 p-4">
      <div className="flex items-center gap-1.5">
        <span aria-hidden className="h-2 w-2 rounded-full border border-ink-soft/60" />
        <span className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/60">
          {RAW_STATUS_LABEL.UNCONFIRMED}
        </span>
      </div>
      <div className="mt-1 text-[19px] font-semibold text-ink">{HERO_HEADLINE.UNCONFIRMED}</div>
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[12px] text-ink-soft">
        <span>
          {evidence.testsCollected === null ? "not reported" : evidence.testsCollected} executed
        </span>
        <span aria-hidden className="text-ink-soft/40">
          ·
        </span>
        <span>{evidence.testsPassed === null ? "not reported" : evidence.testsPassed} passed</span>
        <span aria-hidden className="text-ink-soft/40">
          ·
        </span>
        <span>0 reproduced</span>
      </div>
    </div>
  );
}

/** NO_TESTS — a coverage gap, not a result. There was nothing runnable to
 * observe the suspected failure with. */
function NoTestsHero({ evidence }: { evidence: ReproductionEvidence }) {
  return (
    <div className="rounded-xl border border-status-retry/25 bg-status-retry-bg/10 p-4">
      <div className="flex items-center gap-1.5">
        <span aria-hidden className="text-status-retry">
          —
        </span>
        <span className="text-[9px] font-medium uppercase tracking-wider text-status-retry/80">
          {RAW_STATUS_LABEL.NO_TESTS}
        </span>
      </div>
      <div className="mt-1 text-[19px] font-semibold text-ink">{HERO_HEADLINE.NO_TESTS}</div>
      <p className="mt-1.5 text-[12px] text-ink-soft">
        No executable tests were available to observe the reported failure.
      </p>
      {evidence.infraDetail && (
        <p className="mt-1 font-mono text-[11px] text-ink-soft">{evidence.infraDetail}</p>
      )}
    </div>
  );
}

/** INFRA_ERROR — the instrument itself failed. Never inferred from a bare
 * exit code: `timedOut` is stated explicitly, and duration is only shown
 * when A3.5 actually measured one. */
function InfraErrorHero({ evidence }: { evidence: ReproductionEvidence }) {
  const suiteStage = evidence.stages.find((s) => s.id === "suite_executed");
  return (
    <div className="rounded-xl border border-status-failed/30 bg-status-failed-bg/10 p-4">
      <div className="flex items-center gap-1.5">
        <span aria-hidden className="text-status-failed">
          ×
        </span>
        <span className="text-[9px] font-medium uppercase tracking-wider text-status-failed/80">
          {RAW_STATUS_LABEL.INFRA_ERROR}
        </span>
      </div>
      <div className="mt-1 text-[19px] font-semibold text-ink">{HERO_HEADLINE.INFRA_ERROR}</div>
      <p className="mt-1.5 text-[12px] text-ink-soft">
        The test environment prevented a reliable reproduction — this is not the same as the failure
        not reproducing.
      </p>
      {evidence.timedOut && (
        <p className="mt-1.5 flex items-start gap-1.5 text-[11px] font-medium text-status-failed">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          {evidence.durationSeconds !== null
            ? `Timed out after ${evidence.durationSeconds.toFixed(2)}s`
            : `Timed out — ${suiteStage?.detail ?? "execution exceeded its time limit."}`}
        </p>
      )}
      {!evidence.timedOut && evidence.infraDetail && (
        <p className="mt-1.5 font-mono text-[11px] text-ink-soft">{evidence.infraDetail}</p>
      )}
      <div className="mt-3 grid grid-cols-3 gap-1.5 border-t border-status-failed/20 pt-3">
        <StatChip
          label="Exit code"
          value={evidence.exitCode ?? "not reported"}
          hint={exitCodeHint(evidence.exitCode)}
        />
        <StatChip
          label="Duration"
          value={
            evidence.durationSeconds !== null
              ? `${evidence.durationSeconds.toFixed(2)}s`
              : "not reported"
          }
        />
        <StatChip
          label="Timeout"
          value={evidence.timedOut ? "Yes" : "No"}
          hint={timeoutHint(evidence)}
        />
      </div>
    </div>
  );
}

function ObservationHero({ evidence }: { evidence: ReproductionEvidence }) {
  switch (evidence.status) {
    case "CONFIRMED":
      return <ConfirmedHero evidence={evidence} />;
    case "UNCONFIRMED":
      return <UnconfirmedHero evidence={evidence} />;
    case "NO_TESTS":
      return <NoTestsHero evidence={evidence} />;
    case "INFRA_ERROR":
      return <InfraErrorHero evidence={evidence} />;
  }
}

// ---------------------------------------------------------- runtime signal

/** Above this many collected tests, individual per-test cells compress into
 * a fixed-size dense grid — proportionally sized buckets, not one cell per
 * test — so a suite of 2,000 renders exactly as fast as a suite of 12. */
const CELL_MODE_MAX = 60;

/** The fixed cell budget for a compressed grid — large enough to read as a
 * dense matrix, small enough that the target cell stays visually distinct
 * rather than lost among hundreds of neighbours. */
const COMPRESSED_CELL_BUDGET = 50;

interface SignalGroups {
  neutral: number;
  baseline: number;
  target: number;
  other: number;
  total: number;
}

/**
 * Every count here is a real backend field or a direct, undisguised
 * derivation of one (`target` is 1 exactly when the status is CONFIRMED and
 * `failingTest` is set — never inferred from position). `other` absorbs any
 * leftover so the groups always sum to `testsCollected` without silently
 * dropping or double-counting a test.
 */
function computeSignalGroups(evidence: ReproductionEvidence): SignalGroups | null {
  if (evidence.testsCollected === null) return null;
  const total = evidence.testsCollected;
  const target = evidence.status === "CONFIRMED" && evidence.failingTest ? 1 : 0;
  const baseline = evidence.baselineFailures.length;
  const neutral = Math.max(0, evidence.testsPassed ?? 0);
  const other = Math.max(0, total - neutral - baseline - target);
  return { neutral, baseline, target, other, total };
}

interface DisplayCells {
  neutral: number;
  baseline: number;
  target: number;
}

/**
 * Below `CELL_MODE_MAX`, one cell per real test. Above it, the same three
 * groups are redistributed into a fixed budget of cells — baseline and
 * target each keep at least one cell (they are never zeroed out just
 * because they're a small share of a large suite), the rest go to "pass".
 * This is a display compression only: the counts printed beneath the grid
 * always come straight from the real backend fields, never from the
 * compressed cell counts.
 */
function compressForDisplay(groups: SignalGroups): DisplayCells {
  if (groups.total <= CELL_MODE_MAX) {
    return {
      neutral: groups.neutral + groups.other,
      baseline: groups.baseline,
      target: groups.target,
    };
  }
  const target = groups.target > 0 ? 1 : 0;
  const baseline =
    groups.baseline > 0
      ? Math.max(1, Math.round((groups.baseline / groups.total) * COMPRESSED_CELL_BUDGET))
      : 0;
  const neutral = Math.max(0, COMPRESSED_CELL_BUDGET - target - baseline);
  return { neutral, baseline, target };
}

/** Rectangular runtime-signal cells — a dense matrix, not a pie chart and
 * not a stacked progress bar. PASS cells read as quiet texture; the
 * reproduced TARGET cell separates itself by colour alone, never by size. */
function RuntimeSignal({ evidence }: { evidence: ReproductionEvidence }) {
  const groups = computeSignalGroups(evidence);

  if (groups === null) {
    return (
      <div>
        <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Runtime signal
        </div>
        <p className="mt-1 text-[11px] text-ink-soft">
          Not available — the suite never reached a collection count.
        </p>
      </div>
    );
  }

  if (groups.total === 0) {
    return (
      <div>
        <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Runtime signal
        </div>
        <p className="mt-1 text-[11px] text-ink-soft">0 tests collected.</p>
      </div>
    );
  }

  const allClean = groups.baseline === 0 && groups.target === 0;
  const cells = compressForDisplay(groups);

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Runtime signal
        </span>
        {allClean ? (
          <span className="text-[10px] font-semibold text-status-completed">ALL PASSED</span>
        ) : (
          <span className="flex items-center gap-2.5 text-[9px] text-ink-soft/70">
            <span className="flex items-center gap-1">
              <span aria-hidden className="h-2 w-2 rounded-[2px] bg-ink-soft/35" />
              pass
            </span>
            <span className="flex items-center gap-1">
              <span aria-hidden className="h-2 w-2 rounded-[2px] bg-status-retry/60" />
              baseline
            </span>
            <span className="flex items-center gap-1">
              <span aria-hidden className="h-2 w-2 rounded-[2px] bg-status-failed" />
              target
            </span>
          </span>
        )}
        <span className="font-mono text-[10px] text-ink-soft">{groups.total} collected</span>
      </div>

      <div
        role="img"
        aria-label={`${groups.neutral} passed, ${groups.baseline} baseline failure${groups.baseline === 1 ? "" : "s"}, ${groups.target} reproduced target failure`}
        className="mt-2 flex flex-wrap gap-[3px]"
      >
        {Array.from({ length: cells.neutral }, (_, i) => (
          <span key={`n${i}`} aria-hidden className="h-3 w-2 rounded-[1px] bg-ink-soft/30" />
        ))}
        {Array.from({ length: cells.baseline }, (_, i) => (
          <span key={`b${i}`} aria-hidden className="h-3 w-2 rounded-[1px] bg-status-retry/60" />
        ))}
        {Array.from({ length: cells.target }, (_, i) => (
          <span
            key={`t${i}`}
            aria-hidden
            className="h-3 w-2 rounded-[1px] bg-status-failed ring-1 ring-status-failed/40"
          />
        ))}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-ink-soft">
        <span>
          <span className="font-mono text-ink">
            {evidence.testsPassed === null ? "not reported" : evidence.testsPassed}
          </span>{" "}
          passed
        </span>
        {groups.baseline > 0 && (
          <span>
            <span className="font-mono text-ink">{groups.baseline}</span> baseline
          </span>
        )}
        <span>
          <span className="font-mono text-ink">{groups.target}</span> reproduced
        </span>
      </div>
    </div>
  );
}

/**
 * Explicitly separates the reproduced target failure ("signal") from
 * pre-existing repository instability ("noise floor") — the point being
 * that a repository with baseline failures was already broken before A3.5
 * ran, and that is not the same fact as the reported bug reproducing.
 * Rendered only when there is something to distinguish.
 */
function SignalVsNoise({ evidence }: { evidence: ReproductionEvidence }) {
  const groups = computeSignalGroups(evidence);
  if (groups === null || (groups.target === 0 && groups.baseline === 0)) return null;
  return (
    <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
      <div className="rounded-md border border-status-failed/25 bg-status-failed-bg/5 px-2.5 py-1.5">
        <div className="text-[9px] font-medium uppercase tracking-wider text-status-failed/80">
          Signal
        </div>
        <div className="mt-0.5 flex items-baseline gap-1.5 text-[11px] text-ink">
          <span aria-hidden className="text-status-failed">
            ◆
          </span>
          {groups.target > 0
            ? "Target failure observed"
            : "No target failure — nothing newly reproduced"}
        </div>
        <div className="mt-0.5 font-mono text-[10px] text-ink-soft">
          {groups.target} reproduced failure{groups.target === 1 ? "" : "s"}
        </div>
      </div>
      <div className="rounded-md border border-status-retry/25 bg-status-retry-bg/5 px-2.5 py-1.5">
        <div className="text-[9px] font-medium uppercase tracking-wider text-status-retry/80">
          Noise floor
        </div>
        <div className="mt-0.5 flex items-baseline gap-1.5 text-[11px] text-ink">
          <span aria-hidden className="text-status-retry">
            ○
          </span>
          {groups.baseline} baseline failure{groups.baseline === 1 ? "" : "s"}
        </div>
        <div className="mt-0.5 text-[10px] text-ink-soft">
          {groups.baseline > 0
            ? "Already failing before reproduction"
            : "No pre-existing failures detected"}
        </div>
      </div>
    </div>
  );
}

const EVIDENCE_SOURCE_LABEL: Record<EvidenceSource, string> = {
  pytest_report: "Structured pytest evidence",
  output_text: "Fallback text evidence",
};

/**
 * A single-sentence synthesis of facts the panel has already stated
 * elsewhere — never a new, invented "evidence score". The header word
 * (OBSERVED / NOT OBSERVED / INCONCLUSIVE) mirrors the four real statuses
 * one-to-one; there is no fifth state.
 */
function EvidenceStrength({ evidence }: { evidence: ReproductionEvidence }) {
  const groups = computeSignalGroups(evidence);
  const passedText = evidence.testsPassed === null ? "not reported" : `${evidence.testsPassed}`;

  let header: string;
  let subtitle: string;
  let toneClass: string;
  let countsLine: string | null = null;
  let sourceLine: string | null = null;

  switch (evidence.status) {
    case "CONFIRMED":
      header = "OBSERVED";
      subtitle = "Runtime failure reproduced";
      toneClass = "border-status-completed/30";
      if (groups)
        countsLine = `${groups.target} target · ${passedText} passed · ${groups.baseline} baseline`;
      sourceLine = evidence.evidenceSource ? EVIDENCE_SOURCE_LABEL[evidence.evidenceSource] : null;
      break;
    case "UNCONFIRMED":
      header = "NOT OBSERVED";
      subtitle = "Full test run completed without the target failure";
      toneClass = "border-border";
      countsLine = `0 reproduced · ${passedText} passed`;
      break;
    case "NO_TESTS":
      header = "INCONCLUSIVE";
      subtitle = "No executable tests were available for observation";
      toneClass = "border-status-retry/30";
      break;
    case "INFRA_ERROR":
      header = "INCONCLUSIVE";
      subtitle = "Execution failed before reliable observation";
      toneClass = "border-status-failed/30";
      break;
  }

  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
        Evidence strength
      </div>
      <div className={`mt-1.5 rounded-lg border ${toneClass} p-3`}>
        <div className="text-[12px] font-semibold text-ink">{header}</div>
        <div className="mt-0.5 text-[11px] text-ink-soft">{subtitle}</div>
        {countsLine && (
          <div className="mt-1.5 font-mono text-[10px] text-ink-soft">{countsLine}</div>
        )}
        {sourceLine && <div className="mt-0.5 text-[10px] text-ink-soft">{sourceLine}</div>}
      </div>
    </div>
  );
}

// --------------------------------------------------------- evidence provenance

const PROVENANCE_CHAIN: Record<EvidenceSource, string[]> = {
  pytest_report: ["pytest", "JSON report", "failure classifier"],
  output_text: ["pytest", "stdout", "text / regex"],
};

/**
 * Why the confidence value is what it is, shown as a vertical instrument
 * chain terminating in a boxed observation — not a bare decimal, and not a
 * generic progress bar. `evidenceSource` is literally which parser branch
 * produced the evidence. Only rendered for a confirmed observation:
 * `evidenceSource` is null for every other status, so there is no chain to
 * draw.
 */
function EvidenceProvenance({ evidence }: { evidence: ReproductionEvidence }) {
  if (evidence.status !== "CONFIRMED" || !evidence.evidenceSource) return null;
  const steps = PROVENANCE_CHAIN[evidence.evidenceSource];
  const isStructured = evidence.evidenceSource === "pytest_report";
  return (
    <div className="rounded-lg border border-border/60 p-3">
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
        Evidence provenance
      </div>
      <ol className="mt-2 space-y-0.5">
        {steps.map((step) => (
          <li key={step}>
            <span className="font-mono text-[11px] uppercase tracking-wide text-ink-soft">
              {step}
            </span>
            <div aria-hidden className="pl-[2px] text-[10px] leading-none text-ink-soft/30">
              ▼
            </div>
          </li>
        ))}
      </ol>
      <div className="rounded-md border border-border bg-surface-muted/50 px-2.5 py-1.5">
        <div className="text-[9px] font-semibold uppercase tracking-wide text-ink">Observed</div>
        <div className="font-mono text-[11px] text-ink-soft">
          {evidence.exceptionType ?? evidence.errorSignature ?? "Observation"}
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between border-t border-border/40 pt-2 text-[10px] uppercase tracking-wide">
        <span className={isStructured ? "text-status-completed" : "text-status-retry"}>
          {isStructured ? "Structured evidence" : "Fallback evidence"}
        </span>
        <span className="text-ink-soft">
          confidence <span className="font-mono text-ink">{evidence.confidence.toFixed(2)}</span>
        </span>
      </div>
      {!isStructured && (
        <p className="mt-1 flex items-center gap-1.5 text-[10px] text-status-retry">
          <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden />
          Structured report unavailable
        </p>
      )}
    </div>
  );
}

/** Exit code / duration / timeout — real measurements of the one command
 * A3.5 actually ran. `timedOut` is stated as its own field, never left for
 * the reader to infer from a bare `exitCode === -1`. */
function ExecutionMeasurement({ evidence }: { evidence: ReproductionEvidence }) {
  return (
    <div className="rounded-lg border border-border/60 p-3">
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
        Execution measurement
      </div>
      <div className="mt-2 grid grid-cols-3 gap-1.5">
        <StatChip
          label="Exit code"
          value={evidence.exitCode ?? "not reported"}
          hint={exitCodeHint(evidence.exitCode)}
        />
        <StatChip
          label="Duration"
          value={
            evidence.durationSeconds !== null
              ? `${evidence.durationSeconds.toFixed(2)}s`
              : "not reported"
          }
        />
        <StatChip
          label="Timeout"
          value={evidence.timedOut ? "Yes" : "No"}
          hint={timeoutHint(evidence)}
        />
      </div>
    </div>
  );
}

/** The command A3.5 actually executed, styled as an instrument reading —
 * shown regardless of status, since A3.5 always runs it even when the
 * result is UNCONFIRMED or an infra error. */
function InstrumentCommand({ evidence }: { evidence: ReproductionEvidence }) {
  if (!evidence.command) return null;
  return (
    <div>
      <div className="text-[9px] font-medium uppercase tracking-wider text-status-completed/80">
        Actually executed
      </div>
      <code className="mt-1 block break-all rounded-md border border-border bg-surface-muted/50 px-2.5 py-2 font-mono text-[11px] text-ink">
        $ {evidence.command}
      </code>
    </div>
  );
}

// ------------------------------------------------------------------ handoff

/** A8's targeted re-execution — a *different* command, never run by A3.5.
 * Styled and labelled distinctly from `InstrumentCommand` so the two are
 * never mistaken for the same event. */
function ReexecutionHandoff({ evidence }: { evidence: ReproductionEvidence }) {
  if (!evidence.reexecutionCommand) return null;
  return (
    <div className="rounded-lg border border-primary/25 bg-primary/5 p-3">
      <div className="flex items-center gap-1.5 text-[9px] font-medium uppercase tracking-wider text-primary">
        <ArrowRight className="h-3 w-3" aria-hidden />
        A8 targeted re-execution
      </div>
      <p className="mt-1 text-[11px] text-ink-soft">prepared — not executed by A3.5</p>
      <code className="mt-1.5 block break-all rounded-md border border-primary/20 bg-surface px-2.5 py-2 font-mono text-[11px] text-ink">
        $ {evidence.reexecutionCommand}
      </code>
    </div>
  );
}

// -------------------------------------------------------------- traceback

/** Collapsed by default — the error signature and location in the hero are
 * already the point; the full frame-by-frame traceback is reference
 * material, not the lead. */
function CollapsedTraceback({ traceback }: { traceback: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 text-[10px] font-medium text-ink-soft transition-colors hover:text-ink"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Full execution traceback
      </button>
      {open && (
        <pre className="mt-1.5 max-h-[260px] overflow-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-surface-muted/40 p-2.5 text-[10px] leading-relaxed text-ink">
          {traceback}
        </pre>
      )}
    </div>
  );
}

// -------------------------------------------------------------------- panel

export function ReproductionEvidencePanel({
  runId,
  status,
}: {
  runId: string;
  status?: AgentStatus;
}) {
  const [evidence, setEvidence] = useState<ReproductionEvidence | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getReproductionEvidence(runId)
      .then((e) => {
        if (!cancelled) setEvidence(e);
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

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Failure Reproduction
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Loading reproduction evidence…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!evidence) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Failure Reproduction
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A3.5 is executing the test suite now; this panel renders once it publishes."
            : "Pending — A3.5 has not completed for this run yet."}
        </p>
      </section>
    );
  }

  const confirmed = evidence.status === "CONFIRMED";
  const limitedConfidenceReason =
    evidence.status === "UNCONFIRMED"
      ? "No runtime evidence to hand off — downstream agents proceed without confirmed reproduction."
      : evidence.status === "NO_TESTS"
        ? "No test suite was available — downstream evidence confidence is limited."
        : "Observation inconclusive — the environment failed, not the reproduction itself.";

  return (
    <section className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Failure Reproduction
        </h3>
        <p className="mt-0.5 text-[11px] text-ink-soft">Runtime Observation Record</p>
      </div>

      {/* 1. DID IT FAIL? WHAT EXACTLY FAILED? */}
      <ObservationHero evidence={evidence} />

      {/* 2 & 3. HOW MANY TESTS SUPPORT THAT? WAS THE REPO ALREADY BROKEN? */}
      <RuntimeSignal evidence={evidence} />
      <SignalVsNoise evidence={evidence} />
      <EvidenceStrength evidence={evidence} />
      {evidence.baselineFailures.length > 0 && (
        <div>
          <div className="mb-1 text-[9px] font-medium uppercase tracking-wider text-ink-soft/70">
            Baseline failures — pre-existing, excluded from the target (
            {evidence.baselineFailures.length})
          </div>
          <ul className="space-y-0.5">
            {evidence.baselineFailures.map((t) => (
              <li key={t} className="truncate font-mono text-[10px] text-ink-soft" title={t}>
                {t}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 5. HOW WAS THE RESULT MEASURED? CAN A4 TRUST THIS EVIDENCE? */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <EvidenceProvenance evidence={evidence} />
        <ExecutionMeasurement evidence={evidence} />
      </div>

      <InstrumentCommand evidence={evidence} />

      {confirmed && evidence.traceback && <CollapsedTraceback traceback={evidence.traceback} />}

      {/* 6. WHAT HAPPENS NEXT? */}
      <ReexecutionHandoff evidence={evidence} />

      <div className="border-t border-border/60 pt-3">
        <div className="flex items-center gap-1.5 text-[9px] font-medium uppercase tracking-wider text-ink-soft/70">
          <span>Observation result</span>
          <ArrowRight className="h-3 w-3 shrink-0 text-primary" aria-hidden />
          <span>A4 — Evidence Investigation</span>
        </div>
        {confirmed ? (
          <p className="mt-1.5 font-mono text-[11px] text-ink-soft">
            Handed off: error signature, {evidence.failingFile}
            {evidence.failingLine !== null ? `:${evidence.failingLine}` : ""}, failing test, runtime
            evidence
          </p>
        ) : (
          <p className="mt-1.5 text-[11px] text-status-retry">{limitedConfidenceReason}</p>
        )}
      </div>

      <p className="text-[10px] text-ink-soft">
        Explain: reproduction is A3.5&apos;s own full-suite pytest gate — it does not target a
        specific A3 finding, distinct from A3&apos;s static findings above. Source:{" "}
        <code className="font-mono">GET /api/runs/{"{run_id}"}/reproduction</code>
      </p>
    </section>
  );
}
