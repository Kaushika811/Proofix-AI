/**
 * A4 — Evidence Investigation Board, as a convergence evidence map.
 *
 * One instrument, not a dashboard: independent evidence sources are drawn as
 * beams travelling toward a single location, so the eye reads
 * EVIDENCE → CONVERGENCE → ROOT CAUSE → CONFIDENCE in the two or three
 * seconds it takes to look at the panel. Everything else — the full category
 * breakdown, the raw evidence log — is real but secondary, folded under a
 * disclosure so it never competes with the hero for attention.
 *
 * Everything comes from `GET /api/runs/{runId}/investigation`
 * (`services/ui_projection.py::build_investigation`, sourced from A4's own
 * `InvestigationReport`). No verdict, stance, strength or confidence is
 * computed here — those are A4's judgements over real upstream artifacts, and
 * recomputing any of them in the browser would let the screen disagree with
 * the run. The one number this file derives is the **earned confidence**
 * shown under VERIFIED ONLY, and it is a plain sum of published terms — see
 * `evidenceBeams.ts` for the provenance model and why the sum is printed
 * rather than a subtraction from the published figure.
 *
 * Three distinctions the layout is careful to preserve, because collapsing
 * any of them would make the map lie:
 *
 * 1. A source that **ran and found nothing** is not a source that **could not
 *    run**. The first is a result; the second is "Not measured", folded into
 *    the evidence log rather than drawn as a beam (there is no weight to draw).
 * 2. Neither of those is evidence *against* the finding. Only an unanchored
 *    citation carries that — the one real contradicting signal today. It
 *    appears twice on purpose: in place in the citation ladder (the trail A4
 *    actually followed, in order) and pulled into its own contradicting
 *    branch (so it is never legible only by scanning a mixed list).
 * 3. `null` confidence means nothing was scored — never 0%.
 *
 * There is no "View source" control, and the convergence node shows a cited
 * *location* rather than cited *lines*: the repository clone is removed when
 * a run ends, so the backend genuinely cannot serve the source, and rendering
 * a code window would mean inventing its contents. That absence is stated
 * outright (`SourceSnapshot`) rather than left looking like a missing feature.
 *
 * Scale. The beam field is bounded by the number of confidence terms — at
 * most five real evidence beams plus the diversity bonus, by construction in
 * `root_cause_builder.compute_confidence_breakdown` — so a 40-file repository
 * and a 4,000-file one draw the same beams. Repo size lands entirely in the
 * citation ladder and the evidence log, which is why those cap their rows and
 * offer "Show all".
 *
 * Not built: a per-attempt reinvestigation timeline. `InvestigationReport`
 * carries no per-attempt evidence snapshot today — only `RootCauseBrief`
 * (a different, non-public model) counts attempts — so a before/after view
 * would have nothing real to draw. Wiring that through is backend work.
 */
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  FlaskConical,
  HelpCircle,
  MinusCircle,
  Package,
  ScanLine,
  FileCode2,
} from "lucide-react";
import type {
  EvidenceCategory,
  EvidenceItem,
  EvidenceStatus,
  InvestigationReport,
} from "./investigationTypes";
import { DIVERSITY_TERM, classifyTerms, subtotal, survives, type Beam } from "./evidenceBeams";
import { getInvestigation } from "@/lib/runService";
import { prefersReducedMotion } from "@/hooks/useCountUp";
import type { AgentStatus } from "./data";

const CATEGORY_ORDER: EvidenceCategory[] = ["scanner", "reproduction", "source", "dependency"];

const CATEGORY_LABEL: Record<EvidenceCategory, string> = {
  scanner: "Scanners",
  reproduction: "Reproduction",
  source: "Source",
  dependency: "Dependencies",
};

const CATEGORY_ICON: Record<EvidenceCategory, typeof ScanLine> = {
  scanner: ScanLine,
  reproduction: FlaskConical,
  source: FileCode2,
  dependency: Package,
};

const CATEGORY_EMPTY: Record<EvidenceCategory, string> = {
  scanner: "Scanner evidence — Not measured",
  reproduction: "Reproduction — Unavailable",
  source: "Source evidence — Not measured",
  dependency: "Dependency evidence — Not measured",
};

const STATUS_ICON: Record<EvidenceStatus, typeof CheckCircle2> = {
  present: CheckCircle2,
  absent: MinusCircle,
  unavailable: HelpCircle,
  error: AlertTriangle,
};

/** Verified / uncertain / contradicting / navigation — the app's own status palette. */
const INK_VERIFIED = "var(--color-status-completed)";
const INK_UNCERTAIN = "var(--color-status-retry)";
const INK_CONTRADICT = "var(--color-status-failed)";

/**
 * Evidence semantics, and nowhere else: green only means verified, amber only
 * means asserted-but-unproven, red only means contradicting, gray only means
 * the source never ran. `unavailable` is gray rather than amber for exactly
 * that reason — a scanner that never ran made no claim to be unproven.
 */
const STATUS_COLOR: Record<EvidenceStatus, string> = {
  present: INK_VERIFIED,
  absent: "var(--color-ink-soft)",
  unavailable: "var(--color-ink-soft)",
  error: INK_CONTRADICT,
};

const STATUS_LABEL: Record<EvidenceStatus, string> = {
  present: "reported",
  absent: "nothing found",
  unavailable: "not measured",
  error: "execution error",
};

const REPRODUCTION_LABEL = {
  reproduced: "REPRODUCED",
  not_reproduced: "NOT REPRODUCED",
  unavailable: "UNAVAILABLE",
  error: "EXECUTION ERROR",
} as const;

const REPRODUCTION_COLOR = {
  reproduced: INK_VERIFIED,
  not_reproduced: "var(--color-ink-soft)",
  unavailable: "var(--color-ink-soft)",
  error: INK_CONTRADICT,
} as const;

const INVESTIGATION_LABEL = {
  complete: "COMPLETE",
  partial: "PARTIAL",
  no_finding: "NO FINDING",
  error: "DEGRADED",
} as const;

const INVESTIGATION_COLOR = {
  complete: INK_VERIFIED,
  partial: INK_UNCERTAIN,
  no_finding: "var(--color-ink-soft)",
  error: INK_CONTRADICT,
} as const;

/** Beam provenance, in the map's own vocabulary. */
const PROVENANCE_LABEL: Record<Beam["provenance"], string> = {
  verified: "VERIFIED",
  asserted: "UNVERIFIED",
  unclassified: "UNCLASSIFIED",
};

const PROVENANCE_COLOR: Record<Beam["provenance"], string> = {
  verified: INK_VERIFIED,
  asserted: INK_UNCERTAIN,
  unclassified: "var(--color-ink-soft)",
};

// ------------------------------------------------------------------ motion

/**
 * Ease a number from where it is to where it is going.
 *
 * `useCountUp` always starts from zero, which is right on mount and wrong on a
 * toggle — 0.85 → 0.70 must read as a *retraction*, not as a fresh count from
 * nothing. Honours `prefers-reduced-motion` by snapping.
 */
function useTween(target: number, duration = 420): number {
  const [value, setValue] = useState(target);

  useEffect(() => {
    if (!Number.isFinite(target)) return;
    if (prefersReducedMotion()) {
      setValue(target);
      return;
    }
    let raf: number;
    const origin = value;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const next = origin + (target - origin) * eased;
      setValue(next);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, duration]);

  return value;
}

// ------------------------------------------------------------------ format

/** A3's 0..1 severity as a band. Only ever called when a tool measured it. */
function severityBand(severity: number): string {
  if (severity >= 0.8) return "HIGH";
  if (severity >= 0.5) return "MEDIUM";
  return "LOW";
}

function points(value: number): string {
  return value.toFixed(2);
}

/**
 * A repository path that stays readable when it is long.
 *
 * The basename and line are what identify the location, so they never
 * truncate; the directory gives way first. Both spans carry `min-w-0` — a
 * flex child's default min-width is its content width, so `truncate` alone
 * does nothing inside a flex row and the text spills past the edge instead of
 * clipping. A 4,000-file monorepo has paths that would otherwise push the
 * line number off the row, or off the panel entirely.
 */
function Path({
  file,
  line,
  className,
}: {
  file: string;
  line?: number | null;
  className?: string;
}) {
  const cut = file.lastIndexOf("/");
  const dir = cut === -1 ? "" : file.slice(0, cut + 1);
  const base = cut === -1 ? file : file.slice(cut + 1);
  return (
    <span
      className={`flex min-w-0 max-w-full items-baseline font-mono ${className ?? ""}`}
      title={file}
    >
      {dir && <span className="min-w-0 truncate text-ink-soft">{dir}</span>}
      <span className="shrink-0 whitespace-nowrap text-ink">
        {base}
        {line !== null && line !== undefined ? `:${line}` : ""}
      </span>
    </span>
  );
}

// ------------------------------------------------------------------ chrome

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load the investigation</span>
      <span className="min-w-0 break-words font-mono text-ink-soft">{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="ml-auto shrink-0 rounded-md border border-border px-2 py-0.5 font-medium text-ink transition-colors hover:bg-surface-muted"
      >
        Retry
      </button>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
      {children}
    </div>
  );
}

/**
 * The claim points down into the beams; the beams point down into the
 * ladder. A single centred arrow between sections, not a full spine — the
 * beams themselves are the connective tissue in between.
 */
function FlowArrow() {
  return (
    <div className="-my-1 flex justify-center" aria-hidden>
      <svg width="14" height="10" viewBox="0 0 14 10" className="text-ink-soft/50">
        <path
          d="M1 1 L7 8 L13 1"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

// -------------------------------------------------------------- claim card

/**
 * §1 — the claim, compact, at the top: what A4 concluded, and the reproduced
 * failure (or ranked finding) it concluded it from. Root cause and finding
 * identity used to be two separate cards read four sections apart; here
 * they're one sentence a reader can take in before anything else loads.
 */
function ClaimCard({ report }: { report: InvestigationReport }) {
  if (report.subjectKind === null) {
    return (
      <div
        role="group"
        aria-label="Root cause claim"
        className="rounded-xl border border-border bg-surface-muted/30 p-3 text-[11px] text-ink-soft"
      >
        No finding — neither a reproduced failure nor a ranked static finding was available to
        investigate. A4 ran; there was nothing for it to take as its subject.
      </div>
    );
  }

  const repro = report.reproductionStatus;
  return (
    <div
      role="group"
      aria-label="Root cause claim"
      className="rounded-xl border border-border bg-surface-muted/30 p-3"
    >
      <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
        Root cause
      </div>
      {report.diagnosisStatus && (
        <div className="mt-1 text-[10px] font-semibold uppercase text-ink-soft">
          Diagnosis evidence: {report.diagnosisStatus}
        </div>
      )}
      <p className="mt-1 min-w-0 break-words text-[13px] font-medium leading-relaxed text-ink">
        {report.rootCause ?? "Not measured — A4 had no root cause to report."}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/60 pt-2 text-[10px]">
        {repro && (
          <span className="font-semibold" style={{ color: REPRODUCTION_COLOR[repro] }}>
            {REPRODUCTION_LABEL[repro]}
          </span>
        )}
        {report.file && (
          <Path file={report.file} line={report.line} className="min-w-0 flex-1 text-ink-soft" />
        )}
        <span className="shrink-0 rounded border border-border px-1.5 py-0.5 font-mono text-ink-soft">
          {report.findingId ?? "unidentified"}
        </span>
      </div>

      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-ink-soft">
        <span className="min-w-0 break-words">{report.title}</span>
        {report.severity !== null && report.severityMeasured && (
          <span>
            Severity{" "}
            <span className="font-semibold text-ink">
              {severityBand(report.severity)} ({report.severity.toFixed(2)})
            </span>
          </span>
        )}
        {report.rootCauseSource && (
          <span>
            {report.rootCauseSource === "llm"
              ? "LLM hypothesis, source locations checked"
              : "Deterministic analysis, no LLM"}
          </span>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ toggle

function ProvenanceToggle({
  verifiedOnly,
  onChange,
}: {
  verifiedOnly: boolean;
  onChange: (next: boolean) => void;
}) {
  const option = (label: string, active: boolean, next: boolean) => (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onChange(next)}
      className={`shrink-0 rounded-md px-2 py-1 text-[10px] font-medium uppercase tracking-wider transition-colors ${
        active ? "bg-surface text-primary shadow-sm" : "text-ink-soft hover:text-ink"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div
      role="group"
      aria-label="Evidence provenance"
      className="flex shrink-0 items-center gap-0.5 rounded-lg border border-border bg-surface-muted/60 p-0.5"
    >
      {option("All evidence", !verifiedOnly, false)}
      {option("Verified only", verifiedOnly, true)}
    </div>
  );
}

// -------------------------------------------------------------- citations

/** A citation item carries its anchoring result in `detail.verified`. */
function citationsOf(evidence: EvidenceItem[]) {
  return evidence
    .filter((e) => e.category === "source" && typeof e.detail.file === "string")
    .map((e) => ({
      id: e.id,
      file: String(e.detail.file),
      line: typeof e.detail.line === "number" ? e.detail.line : null,
      verified: e.detail.verified === true,
      claim: e.description,
    }));
}

// -------------------------------------------------------------- hero: map

/**
 * §2–4, §9 — the hero. Evidence beams (left) travel toward a convergence
 * node (center); the same terms compress into the confidence readout
 * (right). This is the one place the panel spends its visual weight — every
 * other section is quieter by design.
 *
 * Beam geometry is index-based rather than measured: `y` is `(i + 0.5) / n`
 * of the row band, computed from array position alone. That keeps the SVG
 * correct with zero layout reads (no ResizeObserver, nothing that only works
 * once mounted in a real browser), which is what lets a 40-file run and a
 * 4,000-file run draw an identical, instantly-correct map.
 */
function ConvergenceMap({
  report,
  beams,
  verifiedOnly,
}: {
  report: InvestigationReport;
  beams: Beam[];
  verifiedOnly: boolean;
}) {
  const sourceBeams = beams.filter((b) => b.component !== DIVERSITY_TERM);
  const diversity = beams.find((b) => b.component === DIVERSITY_TERM);
  const maxPoints = Math.max(...sourceBeams.map((b) => b.points), 0.01);

  const confidence = report.confidence;
  const surviving = beams.filter(survives);
  const kept = subtotal(surviving);
  const shownValue = confidence === null ? 0 : verifiedOnly ? kept : confidence;
  const tweened = useTween(shownValue);
  const barBeams = verifiedOnly ? surviving : beams;
  const barTotal = barBeams.reduce((sum, b) => sum + b.points, 0) || 1;

  const rows = sourceBeams.length;
  const diversityWithdrawn = verifiedOnly && diversity && !survives(diversity);
  const diversityLive = Boolean(diversity) && !diversityWithdrawn;

  if (confidence === null && sourceBeams.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface-muted/20 p-4 text-center text-[11px] text-ink-soft">
        No confidence terms — A4 scored nothing, so there is no convergence to draw. The evidence it
        did gather is in the log below.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_minmax(0,11rem)] lg:gap-0">
      {/* ---- left: evidence sources ---- */}
      <ul aria-label="Evidence sources" className="flex flex-col justify-center gap-2 lg:pr-2">
        {sourceBeams.map((beam) => {
          const withdrawn = verifiedOnly && !survives(beam);
          const width = withdrawn ? 6 : Math.max(18, (beam.points / maxPoints) * 100);
          const color = PROVENANCE_COLOR[beam.provenance];
          return (
            <li key={beam.component} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2">
                <span
                  className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wider text-ink"
                  title={beam.component}
                >
                  {beam.component}
                </span>
                <span className="shrink-0 whitespace-nowrap font-mono text-[10px] tabular-nums text-ink-soft">
                  +{points(beam.points)}
                </span>
              </div>
              <div className="mt-1 flex items-center gap-1.5">
                <span
                  className="h-[3px] rounded-full transition-[width,opacity] duration-500 ease-out motion-reduce:transition-none"
                  style={{
                    width: `${width}%`,
                    minWidth: withdrawn ? "0.5rem" : "1.25rem",
                    opacity: withdrawn ? 0.25 : 1,
                    backgroundColor: color,
                    backgroundImage:
                      beam.provenance === "asserted"
                        ? "repeating-linear-gradient(90deg, currentColor 0 3px, transparent 3px 6px)"
                        : undefined,
                    color,
                  }}
                />
              </div>
              <span
                className="mt-0.5 block text-[9px] font-semibold uppercase tracking-wider"
                style={{ color: withdrawn ? "var(--color-ink-soft)" : color }}
              >
                {withdrawn ? "withdrawn" : PROVENANCE_LABEL[beam.provenance]}
              </span>
            </li>
          );
        })}
      </ul>

      {/* ---- middle: beams converging on the node ---- */}
      <div className="relative min-h-[9rem] lg:min-h-[11rem]">
        <svg
          className="absolute inset-0 h-full w-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden
        >
          {sourceBeams.map((beam, i) => {
            const withdrawn = verifiedOnly && !survives(beam);
            const y = ((i + 0.5) / rows) * 100;
            const strokeWidth = Math.max(0.5, (beam.points / maxPoints) * 2.4);
            const color = PROVENANCE_COLOR[beam.provenance];
            return (
              <path
                key={beam.component}
                d={`M0,${y} C 28,${y} 30,50 50,50`}
                fill="none"
                stroke={color}
                strokeWidth={strokeWidth}
                strokeLinecap="round"
                strokeDasharray={beam.provenance === "asserted" ? "2.5 2.5" : undefined}
                vectorEffect="non-scaling-stroke"
                className="transition-opacity duration-300 ease-out"
                style={{ opacity: withdrawn ? 0 : 0.85 }}
              />
            );
          })}
        </svg>

        <ConvergenceNode
          report={report}
          diversity={diversity}
          diversityLive={diversityLive}
          sourceCount={
            verifiedOnly
              ? surviving.filter((b) => b.component !== DIVERSITY_TERM).length
              : sourceBeams.length
          }
        />
      </div>

      {/* ---- right: earned confidence ---- */}
      <div className="flex flex-col justify-center gap-2 lg:pl-2 lg:text-right">
        <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
          Earned confidence
        </div>
        <div className="flex items-baseline gap-2 lg:justify-end">
          <div className="font-mono text-3xl font-semibold leading-none tracking-tight text-ink tabular-nums">
            {confidence === null ? "—" : tweened.toFixed(2)}
          </div>
          {confidence !== null && verifiedOnly && kept !== confidence && (
            <div
              className="font-mono text-sm tabular-nums text-ink-soft line-through decoration-1"
              title="Published confidence, before verified-only removed unverified terms"
            >
              {points(confidence)}
            </div>
          )}
        </div>
        {confidence === null ? (
          <p className="text-[10px] text-ink-soft">Not measured — no evidence to score.</p>
        ) : (
          <>
            <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-muted lg:ml-auto">
              <div
                className="flex h-full overflow-hidden rounded-full transition-[width] duration-500 ease-out motion-reduce:transition-none"
                style={{ width: `${Math.round(shownValue * 100)}%` }}
                role="progressbar"
                aria-label="Earned confidence"
                aria-valuenow={Math.round(shownValue * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                {barBeams.map((b) => (
                  <span
                    key={b.component}
                    style={{
                      width: `${(b.points / barTotal) * 100}%`,
                      backgroundColor: PROVENANCE_COLOR[b.provenance],
                      opacity: b.provenance === "asserted" ? 0.55 : 1,
                    }}
                  />
                ))}
              </div>
            </div>
            <ul className="flex flex-col gap-0.5 text-[9px] text-ink-soft lg:items-end">
              {barBeams.map((b) => (
                <li key={b.component} className="flex min-w-0 items-center gap-1">
                  <span
                    className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: PROVENANCE_COLOR[b.provenance] }}
                    aria-hidden
                  />
                  <span className="truncate">{b.component}</span>
                  <span className="shrink-0 font-mono tabular-nums">{points(b.points)}</span>
                </li>
              ))}
            </ul>
            {verifiedOnly && (
              <p className="text-[10px] text-ink-soft lg:text-right">
                {kept === confidence
                  ? "Every term behind this confidence was verified — nothing to withdraw."
                  : `${points(confidence)} published; this is the sum of terms nothing needed to assert.`}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * §2, §4 — the convergence node. The signature element: a reticle rather
 * than a card, so the panel reads as an instrument sighting on a location
 * rather than a tile in a dashboard. Pulses only while a diversity bonus is
 * actually being paid — the animation is load-bearing information, not
 * decoration, and stops the instant VERIFIED ONLY withdraws it.
 */
function ConvergenceNode({
  report,
  diversity,
  diversityLive,
  sourceCount,
}: {
  report: InvestigationReport;
  diversity: Beam | undefined;
  diversityLive: boolean;
  sourceCount: number;
}) {
  return (
    <div className="absolute left-1/2 top-1/2 w-[calc(100%-1rem)] max-w-[15rem] -translate-x-1/2 -translate-y-1/2 sm:w-auto">
      <div
        className={`relative rounded-lg border border-ink/25 bg-surface px-3 py-2.5 shadow-md ${
          diversityLive ? "animate-convergence-pulse" : ""
        }`}
      >
        <span className="pointer-events-none absolute -left-px -top-px h-2.5 w-2.5 border-l-2 border-t-2 border-ink/40" />
        <span className="pointer-events-none absolute -right-px -top-px h-2.5 w-2.5 border-r-2 border-t-2 border-ink/40" />
        <span className="pointer-events-none absolute -bottom-px -left-px h-2.5 w-2.5 border-b-2 border-l-2 border-ink/40" />
        <span className="pointer-events-none absolute -bottom-px -right-px h-2.5 w-2.5 border-b-2 border-r-2 border-ink/40" />

        <div className="text-center text-[9px] font-semibold uppercase tracking-[0.15em] text-ink-soft">
          Evidence converges
        </div>
        <div className="mt-1 min-w-0 text-center">
          {report.file ? (
            <Path
              file={report.file}
              line={report.line}
              className="mx-auto w-fit max-w-full justify-center text-[11px] font-semibold"
            />
          ) : (
            <p className="font-mono text-[11px] text-ink-soft">Location — Not measured</p>
          )}
        </div>
        <p className="mt-1 text-center text-[9px] uppercase tracking-wider text-ink-soft">
          {sourceCount} independent evidence source{sourceCount === 1 ? "" : "s"}
        </p>
        {diversity && (
          <div
            className="mt-1.5 border-t border-border/60 pt-1.5 text-center text-[10px] transition-opacity duration-300"
            style={{ opacity: diversityLive ? 1 : 0.3 }}
          >
            <span className="font-semibold" style={{ color: INK_VERIFIED }}>
              ✦ diversity bonus {diversityLive ? `+${points(diversity.points)}` : "withdrawn"}
            </span>
            <p className="mt-0.5 min-w-0 break-words text-ink-soft">{diversity.basis}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------ ladder & branch

const LADDER_PREVIEW = 4;

/**
 * §5 — the citation ladder, and §6 — the contradicting branch beside it.
 * The ladder is the full trail A4 followed, in the order it followed it,
 * each stop marked with the verifier's own anchoring result — a run that
 * argued through two unverified hops before landing on a verified one shows
 * exactly that shape. The contradicting branch pulls the unverified stops
 * back out as their own card, because §6 asks that contradiction never be
 * legible only by scanning a mixed list. §8 sits alongside as a plain
 * statement of what the ladder cannot show.
 */
function LadderAndBranch({ report }: { report: InvestigationReport }) {
  const [showAll, setShowAll] = useState(false);
  const citations = useMemo(() => citationsOf(report.evidence), [report.evidence]);
  const contradicting = citations.filter((c) => !c.verified);
  const shown = showAll ? citations : citations.slice(0, LADDER_PREVIEW);

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,14rem)]">
      <div role="group" aria-label="Citation ladder" className="min-w-0">
        <SectionLabel>Citation ladder</SectionLabel>
        <p className="mb-2 text-[10px] leading-snug text-ink-soft">
          Located source lines identify where to review a claim; they do not confirm its meaning.
        </p>
        {citations.length ? (
          <ol className="space-y-2 border-l border-border pl-3">
            {shown.map((c) => (
              <li key={c.id} className="min-w-0">
                <div className="flex min-w-0 items-baseline gap-1.5">
                  <span
                    aria-hidden
                    className="shrink-0 font-mono text-[11px]"
                    style={{ color: c.verified ? INK_VERIFIED : INK_CONTRADICT }}
                  >
                    {c.verified ? "✓" : "✕"}
                  </span>
                  <Path file={c.file} line={c.line} className="min-w-0 text-[11px]" />
                </div>
                <p className="mt-0.5 min-w-0 break-words pl-4 text-[10px] leading-snug text-ink-soft">
                  └─ {c.claim}
                </p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="text-[10px] text-ink-soft">
            No citations anchored to source — the location above carries the finding alone.
          </p>
        )}
        {citations.length > LADDER_PREVIEW && (
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            className="mt-1.5 text-[10px] font-medium text-ink-soft transition-colors hover:text-ink"
          >
            {showAll ? "Show fewer" : `Show all ${citations.length} cited locations`}
          </button>
        )}
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <div
          role="group"
          aria-label="Contradicting evidence"
          className="min-w-0 rounded-lg border p-2.5"
          style={{ borderColor: `color-mix(in oklab, ${INK_CONTRADICT} 30%, transparent)` }}
        >
          <div
            className="text-[9px] font-semibold uppercase tracking-wider"
            style={{ color: INK_CONTRADICT }}
          >
            Contradicting ({contradicting.length})
          </div>
          {contradicting.length ? (
            <ul className="mt-1 space-y-1">
              {contradicting.map((c) => (
                <li key={c.id} className="flex min-w-0 items-baseline gap-1.5 text-[10px]">
                  <span
                    aria-hidden
                    className="shrink-0 font-mono"
                    style={{ color: INK_CONTRADICT }}
                  >
                    ✕
                  </span>
                  <Path file={c.file} line={c.line} className="min-w-0 text-[10px]" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[10px] text-ink-soft">None — every citation anchored.</p>
          )}
          {contradicting.length > 0 && (
            <p className="mt-1.5 text-[10px] text-ink-soft">
              Evidence exists against the claim; supporting evidence currently outweighs it.
            </p>
          )}
        </div>

        <div
          role="group"
          aria-label="Source availability"
          className="min-w-0 rounded-lg border border-border p-2.5"
        >
          <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
            Source snapshot
          </div>
          <p className="mt-1 text-[10px] text-ink-soft">
            Unavailable after run — the repository clone is removed once A4 finishes, so cited lines
            cannot be fetched or shown.
          </p>
          <p className="mt-1 font-mono text-[10px] text-ink">
            {citations.length} location{citations.length === 1 ? "" : "s"} preserved
          </p>
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------- evidence log

function EvidenceRow({ item }: { item: EvidenceItem }) {
  const [open, setOpen] = useState(false);
  const Icon = STATUS_ICON[item.status];
  const color = STATUS_COLOR[item.status];
  const detailEntries = Object.entries(item.detail).filter(
    ([, value]) =>
      value !== null && value !== undefined && !(Array.isArray(value) && !value.length),
  );
  const expandable = detailEntries.length > 0 || item.strengthBasis !== null;

  return (
    <li className="border-t border-border/60 first:border-t-0">
      <button
        type="button"
        onClick={() => expandable && setOpen((o) => !o)}
        aria-expanded={expandable ? open : undefined}
        disabled={!expandable}
        className="flex w-full min-w-0 items-start gap-1.5 py-1.5 text-left disabled:cursor-default"
      >
        <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color }} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-1.5">
            <span className="min-w-0 truncate font-mono text-[11px] text-ink">{item.source}</span>
            <span
              className="shrink-0 whitespace-nowrap text-[9px] uppercase tracking-wider"
              style={{ color }}
            >
              {STATUS_LABEL[item.status]}
            </span>
            {item.stance !== "neutral" && (
              <span
                className="shrink-0 whitespace-nowrap text-[9px] uppercase tracking-wider"
                style={{ color: item.stance === "supporting" ? INK_VERIFIED : INK_CONTRADICT }}
              >
                {item.stance}
              </span>
            )}
            {item.strength !== null && (
              <span className="shrink-0 whitespace-nowrap font-mono text-[9px] text-ink-soft">
                strength {item.strength.toFixed(2)}
              </span>
            )}
          </span>
          <span className="mt-0.5 block min-w-0 break-words text-[10px] leading-snug text-ink-soft">
            {item.description}
          </span>
        </span>
        {expandable &&
          (open ? (
            <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-ink-soft" />
          ) : (
            <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-ink-soft" />
          ))}
      </button>
      {open && (
        <div className="min-w-0 pb-2 pl-5">
          {item.strengthBasis && (
            <p className="mb-1 min-w-0 break-words text-[10px] text-ink-soft">
              Strength: {item.strengthBasis}
            </p>
          )}
          <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[10px]">
            {detailEntries.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-ink-soft">{key}</dt>
                <dd className="min-w-0 break-all font-mono text-ink">
                  {Array.isArray(value) ? value.join(", ") : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      )}
    </li>
  );
}

const ROW_PREVIEW = 6;

function CategoryCard({
  category,
  items,
  status,
}: {
  category: EvidenceCategory;
  items: EvidenceItem[];
  status: EvidenceStatus | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const Icon = CATEGORY_ICON[category];
  const measured = status === "present" || status === "absent";
  const shown = showAll ? items : items.slice(0, ROW_PREVIEW);

  return (
    <div
      role="group"
      aria-label={`${CATEGORY_LABEL[category]} evidence`}
      className="min-w-0 rounded-lg border border-border bg-surface-muted/30"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-1.5 px-2.5 py-1.5 text-left"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-ink-soft" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-ink-soft" />
        )}
        <Icon className="h-3.5 w-3.5 shrink-0 text-ink-soft" aria-hidden />
        <span className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wider text-ink">
          {CATEGORY_LABEL[category]}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {!measured && <Circle className="h-2 w-2 text-status-pending" aria-hidden />}
          <span className="font-mono text-[10px] text-ink-soft">{items.length}</span>
        </span>
      </button>
      {open && (
        <div className="min-w-0 px-2.5 pb-1">
          {items.length ? (
            <>
              <ul>
                {shown.map((item) => (
                  <EvidenceRow key={item.id} item={item} />
                ))}
              </ul>
              {items.length > ROW_PREVIEW && (
                <button
                  type="button"
                  onClick={() => setShowAll((v) => !v)}
                  className="w-full py-1 text-left text-[10px] font-medium text-ink-soft transition-colors hover:text-ink"
                >
                  {showAll
                    ? "Show fewer"
                    : `Show all ${items.length} ${CATEGORY_LABEL[category].toLowerCase()}`}
                </button>
              )}
            </>
          ) : (
            <p className="py-1.5 text-[10px] text-ink-soft">{CATEGORY_EMPTY[category]}</p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * §10 — everything the map deliberately keeps off the hero: the four-category
 * correlation view and the unavailable-sources list. Real, complete, and one
 * click away rather than competing with the convergence map for attention.
 */
function EvidenceLog({ report }: { report: InvestigationReport }) {
  const [open, setOpen] = useState(false);
  const byCategory = useMemo(() => {
    const grouped = new Map<EvidenceCategory, EvidenceItem[]>();
    for (const category of CATEGORY_ORDER) grouped.set(category, []);
    for (const item of report.evidence) grouped.get(item.category)?.push(item);
    return grouped;
  }, [report.evidence]);
  const completeness = report.completeness;

  return (
    <div className="border-t border-border pt-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full min-w-0 items-center gap-1.5 text-left"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-ink-soft" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-ink-soft" />
        )}
        <span className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Full evidence log ({report.evidence.length} item{report.evidence.length === 1 ? "" : "s"})
        </span>
      </button>

      {open && (
        <div className="mt-2 space-y-3">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {CATEGORY_ORDER.map((category) => (
              <CategoryCard
                key={category}
                category={category}
                items={byCategory.get(category) ?? []}
                status={completeness.categoryStatus[category]}
              />
            ))}
          </div>
          <p className="min-w-0 break-words text-[10px] text-ink-soft">
            Coverage:{" "}
            {completeness.ratio === null
              ? "Not measured"
              : `${completeness.measuredCategories}/${completeness.totalCategories} evidence categories answered`}
            . A category counts only when its source actually ran — one that could not run is not a
            result, and is never read as evidence against the finding.
          </p>
          {report.unavailableSources.length > 0 && (
            <div>
              <SectionLabel>Sources unavailable ({report.unavailableSources.length})</SectionLabel>
              <ul className="space-y-0.5">
                {report.unavailableSources.map((u) => (
                  <li
                    key={`${u.source}:${u.reason}`}
                    className="min-w-0 break-words text-[10px] text-ink-soft"
                  >
                    <span className="font-mono text-ink">{u.source}</span> — {u.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- board

export function EvidenceInvestigationBoard({
  runId,
  status,
}: {
  runId: string;
  status?: AgentStatus;
}) {
  const [report, setReport] = useState<InvestigationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [verifiedOnly, setVerifiedOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getInvestigation(runId)
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

  const beams = useMemo(
    () => classifyTerms(report?.confidenceBreakdown ?? []),
    [report?.confidenceBreakdown],
  );

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Evidence Investigation
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Loading investigation…</p>
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
          Evidence Investigation
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A4 is correlating evidence now; this board renders once it publishes."
            : "Pending — A4 has not completed for this run yet."}
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-4 overflow-hidden rounded-2xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            Evidence Investigation
          </h3>
          <p className="mt-0.5 max-w-prose text-[11px] text-ink-soft">
            Source locations, runtime observations and claim support are shown separately.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <ProvenanceToggle verifiedOnly={verifiedOnly} onChange={setVerifiedOnly} />
          <div className="text-right">
            <div className="text-[9px] uppercase tracking-wider text-ink-soft">Evidence coverage</div>
            <div
              className="text-sm font-semibold"
              style={{ color: INVESTIGATION_COLOR[report.status] }}
            >
              {INVESTIGATION_LABEL[report.status]}
            </div>
          </div>
        </div>
      </div>

      {report.errors.length > 0 && (
        <div className="min-w-0 rounded-lg border border-status-failed/30 bg-status-failed-bg/30 px-2.5 py-1.5">
          <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-status-failed">
            <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden />
            Investigation degraded
          </div>
          <ul className="mt-0.5 space-y-0.5">
            {report.errors.map((message) => (
              <li key={message} className="min-w-0 break-words text-[10px] text-ink-soft">
                {message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ClaimCard report={report} />
      <FlowArrow />
      <ConvergenceMap report={report} beams={beams} verifiedOnly={verifiedOnly} />
      <FlowArrow />
      <LadderAndBranch report={report} />

      <EvidenceLog report={report} />
    </section>
  );
}
