/**
 * A2 — Reachability Gate + Evidence.
 *
 * Everything on this panel comes from `GET /api/runs/{runId}/dependency-risk`
 * (`services/ui_projection.py::build_dependency_risk`, sourced from A2's own
 * `CVEReachabilityReport`). No advisory, severity, or reachability verdict is
 * computed client-side — `classification` (`Critical` / `Informational` /
 * `Unknown`) is A2's own verdict against A1's import graph, and `severity` is
 * rendered exactly as OSV reported it (a CVSS score or the literal word
 * "HIGH") rather than rebucketed into invented severity bands.
 *
 * A2's claim is not "how many vulnerabilities exist" — it is "which of them
 * can actually reach this repository's code". So the panel is built as a
 * proof interface, not a dashboard:
 *
 *     DECLARED → ADVISORIES → REACHABILITY GATE → FINDINGS → PROOF
 *
 * UNITS. The gate's first stage counts *packages* and the second counts
 * *advisories*; one package routinely carries many advisories, so stage 2 is
 * frequently wider than stage 1. That is real, not a rendering fault, and
 * every stage prints its own unit so a widening is never misread as a
 * narrowing. Only stage 2 → stage 3 is a true subset relationship.
 *
 * DERIVED DATA. A2 emits no application areas, entry points, symbols,
 * confidence, or CVSS bands, so none appear here. The only derived values are
 * ones that follow arithmetically from the payload: hop count is
 * `reachPath.length`, and the large-repository grouping keys on `package`, a
 * real field. Findings with no `reachPath` never render a path.
 *
 * ADAPTIVE. The render strategy keys on the actual finding volume, not on any
 * threshold tuned to one repository: individual proof rails while the result
 * is small enough to read in full, verdict groups once it is not, and a
 * per-package concentration terrain with drill-down once even the groups
 * would be a scroll wall. The table is a secondary inspection mode and pages
 * its rows rather than mounting thousands at once.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  Table2,
  Network,
  X,
  ShieldAlert,
  ShieldQuestion,
  ShieldCheck,
  ChevronRight,
  ChevronDown,
  ArrowRight,
} from "lucide-react";
import type {
  DependencyClassification,
  DependencyFinding,
  DependencyRiskReport,
} from "./dependencyRiskTypes";
import { getDependencyRisk } from "@/lib/runService";
import type { AgentStatus } from "./data";

type ViewMode = "riskmap" | "table";

const CLASS_LABEL: Record<DependencyClassification, string> = {
  Critical: "REACHABLE",
  Informational: "NOT REACHABLE",
  Unknown: "UNKNOWN",
};

const CLASS_COLOR: Record<DependencyClassification, string> = {
  Critical: "#dc2626",
  Informational: "#64748b",
  Unknown: "#d97706",
};

const CLASS_ICON: Record<DependencyClassification, typeof ShieldAlert> = {
  Critical: ShieldAlert,
  Informational: ShieldCheck,
  Unknown: ShieldQuestion,
};

const CLASS_ORDER: DependencyClassification[] = ["Critical", "Unknown", "Informational"];

/** Render strategy keys on real finding volume, never on a demo-sized
 * constant: full proof rails while every finding still fits on screen,
 * verdict groups once they do not, package-concentration terrain once even
 * the groups would be a scroll wall. */
const DETAIL_MAX_FINDINGS = 12;
const GROUPED_MAX_FINDINGS = 80;
/** Rows mounted per page in the secondary table view — thousands of DOM rows
 * are never mounted at once. */
const TABLE_PAGE_SIZE = 50;
/** Rails mounted before progressive disclosure inside a group. */
const RAIL_PAGE_SIZE = 8;

type EvidenceMode = "detail" | "grouped" | "terrain";

function evidenceModeFor(findingCount: number): EvidenceMode {
  if (findingCount <= DETAIL_MAX_FINDINGS) return "detail";
  if (findingCount <= GROUPED_MAX_FINDINGS) return "grouped";
  return "terrain";
}

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load dependency analysis</span>
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

/** Provenance-strip cell. Deliberately borderless: the verdict above is the
 * result, and this footer only confirms what was scanned. */
function StatChip({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/70">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-[11px] text-ink">{value}</div>
    </div>
  );
}

function ClassBadge({ classification }: { classification: DependencyClassification }) {
  const Icon = CLASS_ICON[classification];
  return (
    <span
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold"
      style={{
        color: CLASS_COLOR[classification],
        background: `color-mix(in srgb, ${CLASS_COLOR[classification]} 14%, transparent)`,
      }}
    >
      <Icon className="h-3 w-3" />
      {CLASS_LABEL[classification]}
    </span>
  );
}

/** A package + version capsule. The version is a separate node so the
 * package name stays a single, addressable token. */
function PackageCapsule({ finding, accent }: { finding: DependencyFinding; accent: boolean }) {
  return (
    <span
      className={`inline-flex items-baseline gap-1.5 rounded border px-1.5 py-0.5 ${
        accent ? "border-status-failed/40 bg-status-failed/[0.08]" : "border-border bg-surface"
      }`}
    >
      <span className="font-mono text-[11px] font-medium text-ink">{finding.package}</span>
      {finding.installedVersion && (
        <span className="font-mono text-[10px] text-ink-soft">{finding.installedVersion}</span>
      )}
    </span>
  );
}

// ----------------------------------------------------------- reachability gate

/**
 * The elimination sequence, drawn as three proportional stages.
 *
 * Widths share one denominator so the stages are comparable, and each stage
 * prints its own unit — stage 1 counts packages, stages 2 and 3 count
 * advisories — because a repository with few packages and many advisories
 * genuinely widens between the first two stages. Only the reachable stage
 * takes the accent, and only when it is non-zero: a zero there is a real
 * security result, not an absence to be coloured like a risk.
 *
 * Bars transition from zero width on mount, so the collapse the user sees is
 * the real proportion arriving, never decorative motion.
 */
function ReachabilityGate({
  report,
  activeClass,
  onFocusClass,
  onHoverClass,
}: {
  report: DependencyRiskReport;
  activeClass: DependencyClassification | null;
  onFocusClass: (cls: DependencyClassification | null) => void;
  onHoverClass: (cls: DependencyClassification | null) => void;
}) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const denom = Math.max(report.totalDependencies, report.advisoryCount, 1);
  const hasReachable = report.reachableCount > 0;

  const stages = [
    {
      key: "declared",
      label: "Declared",
      value: report.totalDependencies,
      unit: report.totalDependencies === 1 ? "package" : "packages",
      accent: false,
    },
    {
      key: "advisories",
      label: "Advisories",
      value: report.advisoryCount,
      unit: report.advisoryCount === 1 ? "advisory" : "advisories",
      accent: false,
    },
    {
      key: "reachable",
      label: "Reachable",
      value: report.reachableCount,
      unit: report.reachableCount === 1 ? "advisory" : "advisories",
      accent: hasReachable,
    },
  ];

  return (
    <div
      className={`rounded-xl border bg-surface-muted/25 p-4 sm:p-5 ${
        hasReachable ? "border-status-failed/35" : "border-border/70"
      }`}
    >
      <div className="grid gap-4 sm:grid-cols-3 sm:gap-6">
        {stages.map((stage, i) => (
          <div key={stage.key} className="relative">
            <div className="flex items-baseline gap-1.5">
              <span
                className={`text-[9px] font-medium uppercase tracking-wider ${
                  stage.accent ? "text-status-failed" : "text-ink-soft"
                }`}
              >
                {stage.label}
              </span>
              {i > 0 && (
                <ArrowRight
                  aria-hidden
                  className="absolute -left-4 top-1 hidden h-3 w-3 text-ink-soft/30 sm:block"
                />
              )}
            </div>
            <div className="mt-0.5 flex items-baseline gap-1.5">
              <span
                className={`font-mono text-[28px] font-semibold leading-none tabular-nums ${
                  stage.accent ? "text-status-failed" : "text-ink"
                }`}
              >
                {stage.value}
              </span>
              <span className="text-[10px] text-ink-soft">{stage.unit}</span>
            </div>
            {/* Track and fill need real separation: at 420 declared vs 14
                reachable the accent stage is a 3% sliver, and a low-contrast
                fill made every stage read as the same flat line. */}
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-ink-soft/15">
              <div
                className={`h-full rounded-full transition-[width] duration-700 ease-out motion-reduce:transition-none ${
                  stage.accent ? "bg-status-failed" : "bg-ink-soft/75"
                }`}
                style={{
                  width: mounted && stage.value > 0 ? `${(stage.value / denom) * 100}%` : "0%",
                  minWidth: stage.value > 0 ? 3 : undefined,
                }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* The eliminated population, as filter controls. Unknown is never
          folded into unreachable — they are different facts. */}
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border/50 pt-3">
        {report.reachableCount > 0 && (
          <GateFilter
            cls="Critical"
            count={report.reachableCount}
            label="reachable"
            active={activeClass === "Critical"}
            onFocus={onFocusClass}
            onHover={onHoverClass}
          />
        )}
        {report.informationalCount > 0 && (
          <GateFilter
            cls="Informational"
            count={report.informationalCount}
            label="eliminated by reachability analysis"
            active={activeClass === "Informational"}
            onFocus={onFocusClass}
            onHover={onHoverClass}
          />
        )}
        {report.unknownCount > 0 && (
          <GateFilter
            cls="Unknown"
            count={report.unknownCount}
            label="undetermined — reachability could not be established"
            active={activeClass === "Unknown"}
            onFocus={onFocusClass}
            onHover={onHoverClass}
          />
        )}
      </div>
    </div>
  );
}

function GateFilter({
  cls,
  count,
  label,
  active,
  onFocus,
  onHover,
}: {
  cls: DependencyClassification;
  count: number;
  label: string;
  active: boolean;
  onFocus: (cls: DependencyClassification | null) => void;
  onHover: (cls: DependencyClassification | null) => void;
}) {
  const isUnknown = cls === "Unknown";
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`Show ${count} ${label}`}
      onClick={() => onFocus(active ? null : cls)}
      onMouseEnter={() => onHover(cls)}
      onMouseLeave={() => onHover(null)}
      className={`flex items-center gap-1.5 rounded-md px-2 py-1 text-left transition-colors ${
        active ? "bg-surface-muted" : "hover:bg-surface-muted/60"
      } ${isUnknown ? "border border-dashed border-status-retry/50" : "border border-transparent"}`}
    >
      <span
        aria-hidden
        className="h-1.5 w-1.5 shrink-0 rounded-full"
        style={{
          background: isUnknown ? "transparent" : CLASS_COLOR[cls],
          boxShadow: isUnknown ? `inset 0 0 0 1px ${CLASS_COLOR[cls]}` : undefined,
          opacity: cls === "Informational" ? 0.6 : 1,
        }}
      />
      <span
        className="font-mono text-[11px]"
        style={{ color: cls === "Critical" ? CLASS_COLOR[cls] : undefined }}
      >
        {count}
      </span>
      <span className={`text-[10px] ${isUnknown ? "text-status-retry" : "text-ink-soft"}`}>
        {label}
      </span>
    </button>
  );
}

/** One generated verdict, strictly from real counts. */
function verdictFor(report: DependencyRiskReport): string {
  if (report.reachableCount === 0) {
    return "No detected advisories are reachable from repository code.";
  }
  return `${report.reachableCount} of ${report.advisoryCount} advisor${
    report.advisoryCount === 1 ? "y is" : "ies are"
  } reachable from repository code.`;
}

// -------------------------------------------------------------- evidence rows

/**
 * A reachable finding and its proof. `reachPath` is rendered verbatim as an
 * ordered chain terminating at the package; the hop count is
 * `reachPath.length` and nothing else is derived. Selecting the rail opens
 * the full detail and highlights every other advisory on the same package.
 */
function ProofRail({
  finding,
  selected,
  sharesSelectedPackage,
  dimmed,
  onSelect,
}: {
  finding: DependencyFinding;
  selected: boolean;
  sharesSelectedPackage: boolean;
  dimmed: boolean;
  onSelect: (cveId: string) => void;
}) {
  const chain = finding.reachPath ?? [];
  const endsWithPackage = chain.length > 0 && chain[chain.length - 1] === finding.package;
  const fileHops = endsWithPackage ? chain.slice(0, -1) : chain;

  return (
    <button
      type="button"
      onClick={() => onSelect(finding.cveId)}
      className={`block w-full rounded-lg border px-3 py-2.5 text-left transition-all ${
        selected
          ? "border-status-failed/50 bg-status-failed/[0.06]"
          : sharesSelectedPackage
            ? "border-status-failed/30 bg-surface"
            : "border-border/70 bg-surface hover:border-status-failed/30"
      } ${dimmed ? "opacity-35" : "opacity-100"}`}
    >
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <span className="font-mono text-[11px] font-semibold text-ink">{finding.cveId}</span>
        {finding.severity && (
          <span
            className="rounded bg-surface-muted px-1 py-px font-mono text-[9px] text-ink-soft"
            title="Severity string as reported by OSV"
          >
            {finding.severity}
          </span>
        )}
        <span className="ml-auto">
          <ClassBadge classification={finding.classification} />
        </span>
      </div>

      <div className="mt-2 flex items-baseline gap-2">
        <span className="shrink-0 text-[8px] font-medium uppercase tracking-wider text-ink-soft/60">
          Reachability proof
        </span>
        <span className="font-mono text-[9px] text-ink-soft/60">
          {chain.length} hop{chain.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1">
        {fileHops.map((hop) => (
          <span key={hop} className="flex items-center gap-1.5">
            <span
              className="rounded border border-border/70 bg-surface-muted/40 px-1.5 py-0.5 font-mono text-[10px] text-ink-soft"
              title={hop}
            >
              {hop}
            </span>
            <ArrowRight className="h-3 w-3 shrink-0 text-ink-soft/40" aria-hidden />
          </span>
        ))}
        <PackageCapsule finding={finding} accent />
      </div>
    </button>
  );
}

/**
 * An eliminated advisory. There is no path to draw — A2 found no production
 * import of this package — so the row shows the severed link rather than a
 * fabricated chain.
 */
function EliminatedRow({
  finding,
  selected,
  sharesSelectedPackage,
  dimmed,
  onSelect,
}: {
  finding: DependencyFinding;
  selected: boolean;
  sharesSelectedPackage: boolean;
  dimmed: boolean;
  onSelect: (cveId: string) => void;
}) {
  const isUnknown = finding.classification === "Unknown";
  return (
    <button
      type="button"
      onClick={() => onSelect(finding.cveId)}
      className={`flex w-full flex-wrap items-center gap-x-2.5 gap-y-1 rounded-md border px-2.5 py-1.5 text-left transition-all ${
        selected
          ? "border-primary/40 bg-primary/[0.06]"
          : sharesSelectedPackage
            ? "border-ink-soft/30 bg-surface"
            : isUnknown
              ? "border-dashed border-status-retry/40 bg-transparent hover:bg-surface-muted/40"
              : "border-transparent hover:bg-surface-muted/50"
      } ${dimmed ? "opacity-35" : "opacity-100"}`}
    >
      <PackageCapsule finding={finding} accent={false} />
      <span aria-hidden className="font-mono text-[10px] text-ink-soft/40">
        ──╳
      </span>
      <span
        className={`text-[9px] font-medium uppercase tracking-wider ${
          isUnknown ? "text-status-retry" : "text-ink-soft/70"
        }`}
      >
        {isUnknown ? "Reachability undetermined" : "No reachable import path"}
      </span>
      <span className="font-mono text-[10px] text-ink-soft/60">{finding.cveId}</span>
      {finding.severity && (
        <span className="rounded bg-surface-muted px-1 py-px font-mono text-[9px] text-ink-soft/70">
          {finding.severity}
        </span>
      )}
    </button>
  );
}

// ------------------------------------------------------------ evidence groups

function GroupSection({
  classification,
  findings,
  defaultOpen,
  selectedCve,
  selectedPackage,
  dimmedClass,
  onSelect,
}: {
  classification: DependencyClassification;
  findings: DependencyFinding[];
  defaultOpen: boolean;
  selectedCve: string | null;
  selectedPackage: string | null;
  dimmedClass: DependencyClassification | null;
  onSelect: (cveId: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [shown, setShown] = useState(RAIL_PAGE_SIZE);
  if (findings.length === 0) return null;

  const isUnknown = classification === "Unknown";
  const isReachable = classification === "Critical";
  const dimmed = dimmedClass !== null && dimmedClass !== classification;
  const visible = findings.slice(0, shown);

  return (
    <div
      className={`rounded-lg border transition-opacity ${
        isUnknown
          ? "border-dashed border-status-retry/40"
          : isReachable
            ? "border-status-failed/25"
            : "border-border/60"
      } ${dimmed ? "opacity-40" : "opacity-100"}`}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left transition-colors hover:bg-surface-muted/40"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-ink-soft" aria-hidden />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-ink-soft" aria-hidden />
        )}
        <span
          className="font-mono text-[11px]"
          style={{ color: isReachable ? CLASS_COLOR.Critical : undefined }}
        >
          {findings.length}
        </span>
        <span
          className={`text-[10px] font-medium uppercase tracking-wider ${
            isUnknown ? "text-status-retry" : isReachable ? "text-status-failed" : "text-ink-soft"
          }`}
        >
          {isReachable ? "reachable" : isUnknown ? "undetermined" : "not reachable"}
        </span>
      </button>

      {open && (
        <div className="space-y-1.5 border-t border-border/50 p-2">
          {visible.map((f) =>
            f.classification === "Critical" ? (
              <ProofRail
                key={f.cveId}
                finding={f}
                selected={f.cveId === selectedCve}
                sharesSelectedPackage={selectedPackage === f.package && f.cveId !== selectedCve}
                dimmed={false}
                onSelect={onSelect}
              />
            ) : (
              <EliminatedRow
                key={f.cveId}
                finding={f}
                selected={f.cveId === selectedCve}
                sharesSelectedPackage={selectedPackage === f.package && f.cveId !== selectedCve}
                dimmed={false}
                onSelect={onSelect}
              />
            ),
          )}
          {findings.length > visible.length && (
            <button
              type="button"
              onClick={() => setShown((n) => n + RAIL_PAGE_SIZE)}
              className="rounded-md border border-border px-2 py-1 text-[10px] font-medium text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
            >
              Show more ({findings.length - visible.length} remaining)
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Risk concentration for results too large to list. Groups on `package` — a
 * real field on every finding — because A2 emits no application areas and
 * inventing them would be fabricating structure. Selecting a package drills
 * into only that subset.
 */
function ConcentrationTerrain({
  findings,
  onDrill,
}: {
  findings: DependencyFinding[];
  onDrill: (pkg: string) => void;
}) {
  const groups = useMemo(() => {
    const map = new Map<
      string,
      { pkg: string; reachable: number; unreachable: number; unknown: number; total: number }
    >();
    for (const f of findings) {
      let g = map.get(f.package);
      if (!g) {
        g = { pkg: f.package, reachable: 0, unreachable: 0, unknown: 0, total: 0 };
        map.set(f.package, g);
      }
      g.total += 1;
      if (f.classification === "Critical") g.reachable += 1;
      else if (f.classification === "Unknown") g.unknown += 1;
      else g.unreachable += 1;
    }
    // Packages with reachable advisories first — concentration of real risk
    // is the only ordering that matters here.
    return [...map.values()].sort(
      (a, b) => b.reachable - a.reachable || b.total - a.total || a.pkg.localeCompare(b.pkg),
    );
  }, [findings]);

  const maxTotal = Math.max(1, ...groups.map((g) => g.total));

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Risk concentration by package
        </span>
        <span className="font-mono text-[10px] text-ink-soft/60">{groups.length} packages</span>
      </div>
      <div className="max-h-[420px] space-y-1 overflow-y-auto pr-1">
        {groups.map((g) => (
          <button
            key={g.pkg}
            type="button"
            onClick={() => onDrill(g.pkg)}
            className="flex w-full items-center gap-2 rounded-md border border-border/60 bg-surface px-2 py-1.5 text-left transition-colors hover:border-status-failed/30 hover:bg-surface-muted/40"
          >
            <span
              className="w-[140px] shrink-0 truncate font-mono text-[11px] text-ink"
              title={g.pkg}
            >
              {g.pkg}
            </span>
            <span className="flex h-1.5 flex-1 overflow-hidden rounded-full bg-surface-muted">
              {g.reachable > 0 && (
                <span
                  style={{
                    width: `${(g.reachable / maxTotal) * 100}%`,
                    background: CLASS_COLOR.Critical,
                  }}
                />
              )}
              {g.unknown > 0 && (
                <span
                  style={{
                    width: `${(g.unknown / maxTotal) * 100}%`,
                    background: CLASS_COLOR.Unknown,
                  }}
                />
              )}
              {g.unreachable > 0 && (
                <span
                  style={{
                    width: `${(g.unreachable / maxTotal) * 100}%`,
                    background: CLASS_COLOR.Informational,
                    opacity: 0.5,
                  }}
                />
              )}
            </span>
            <span className="w-24 shrink-0 text-right font-mono text-[10px]">
              {g.reachable > 0 && (
                <span style={{ color: CLASS_COLOR.Critical }}>{g.reachable}R </span>
              )}
              {g.unknown > 0 && <span className="text-status-retry">{g.unknown}U </span>}
              <span className="text-ink-soft/60">{g.unreachable}N</span>
            </span>
          </button>
        ))}
      </div>
      <p className="mt-1.5 text-[9px] text-ink-soft/60">
        R = reachable · U = undetermined · N = not reachable. Grouped by package; A2 emits no
        application areas, so none are invented here.
      </p>
    </div>
  );
}

// -------------------------------------------------------------- checklist

function DependencyExplanation({ report }: { report: DependencyRiskReport }) {
  const lines: { mark: string; text: string; className: string }[] = [];
  lines.push({
    mark: "✓",
    text: `${report.totalDependencies} dependenc${report.totalDependencies === 1 ? "y" : "ies"} analyzed`,
    className: "text-ink-soft/60",
  });
  lines.push({
    mark: report.advisoryCount > 0 ? "✓" : "—",
    text: `${report.advisoryCount} advisor${report.advisoryCount === 1 ? "y" : "ies"} detected`,
    className: "text-ink-soft/60",
  });
  if (report.reachableCount > 0) {
    lines.push({
      mark: "⚠",
      text: `${report.reachableCount} reachable vulnerabilit${report.reachableCount === 1 ? "y" : "ies"}`,
      className: "text-status-failed",
    });
  }
  if (report.unknownCount > 0) {
    lines.push({
      mark: "—",
      text: `${report.unknownCount} dependenc${report.unknownCount === 1 ? "y" : "ies"} with undetermined reachability`,
      className: "text-status-retry",
    });
  }
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1">
      {lines.map((l) => (
        <li key={l.text} className="flex items-center gap-1.5 text-[10px] text-ink-soft">
          <span className={l.className}>{l.mark}</span>
          {l.text}
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------- inspector

function VulnerabilityDetail({
  finding,
  onClose,
}: {
  finding: DependencyFinding;
  onClose: () => void;
}) {
  return (
    <aside
      aria-label="Vulnerability detail"
      className="w-full shrink-0 rounded-2xl border border-border bg-surface p-4 lg:w-[320px]"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div
            className="truncate font-mono text-[13px] font-semibold text-ink"
            title={finding.package}
          >
            {finding.package}
          </div>
          <div className="mt-0.5 truncate text-[10px] text-ink-soft" title={finding.cveId}>
            {finding.cveId}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close vulnerability detail"
          className="shrink-0 rounded-md p-1 text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3">
        <ClassBadge classification={finding.classification} />
      </div>

      <div className="mt-3 divide-y divide-border/60 border-t border-border/60">
        <div className="flex items-baseline justify-between gap-3 py-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-soft">Version</span>
          <span className="font-mono text-[11px] text-ink">
            {finding.installedVersion ?? "Not measured"}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-3 py-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-soft">Severity</span>
          <span className="font-mono text-[11px] text-ink">
            {finding.severity || "Not measured"}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-3 py-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-soft">Reachability</span>
          <span className="font-mono text-[11px] text-ink">
            {finding.reachable === null
              ? "Not measured"
              : finding.reachable
                ? "Reachable"
                : "Not reachable"}
          </span>
        </div>
        <div className="flex items-baseline justify-between gap-3 py-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-soft">Affected range</span>
          <span className="font-mono text-[11px] text-ink">Not measured</span>
        </div>
        <div className="flex items-baseline justify-between gap-3 py-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-soft">Fixed version</span>
          <span className="font-mono text-[11px] text-ink">Not measured</span>
        </div>
        <div className="flex items-baseline justify-between gap-3 py-1">
          <span className="text-[10px] uppercase tracking-wider text-ink-soft">Declared in</span>
          <span className="font-mono text-[11px] text-ink">requirements.txt (direct)</span>
        </div>
      </div>

      <div className="mt-3">
        <div className="text-[10px] uppercase tracking-wider text-ink-soft">Code impact</div>
        {finding.reachPath && finding.reachPath.length > 0 ? (
          <div className="mt-1.5 space-y-1 font-mono text-[10px] text-ink-soft">
            <div className="text-ink">{finding.package}</div>
            {finding.reachPath.map((path, i) => (
              <div key={path} className="pl-2">
                {i === finding.reachPath!.length - 1 ? "└── " : "├── "}
                {path}
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-1 text-[10px] text-ink-soft">
            {finding.reachable === false
              ? "No production file in the indexed source imports this package."
              : "Not measured — the repository index was unavailable when this advisory was matched."}
          </p>
        )}
      </div>

      <p className="mt-3 text-[10px] text-ink-soft">
        Evidence:{" "}
        {finding.reachable === null
          ? "reachability undetermined — A1's semantic graph was not available."
          : finding.reachable
            ? `${finding.reachPath?.length ?? 0} production file(s) import this package.`
            : "no production import of this package was found in A1's semantic graph."}
      </p>
    </aside>
  );
}

// ------------------------------------------------------------------ table

/** Secondary inspection mode. Rows are paged rather than mounted all at
 * once, so a repository with thousands of advisories never builds thousands
 * of DOM rows. */
function FindingsTable({
  findings,
  selectedCve,
  onSelect,
}: {
  findings: DependencyFinding[];
  selectedCve: string | null;
  onSelect: (cveId: string) => void;
}) {
  const [shown, setShown] = useState(TABLE_PAGE_SIZE);
  const rows = findings.slice(0, shown);

  return (
    <div className="rounded-lg border border-border">
      <div className="max-h-[420px] overflow-auto">
        <table className="w-full border-collapse text-left text-[11px]">
          <thead className="sticky top-0 bg-surface">
            <tr className="border-b border-border text-[9px] uppercase tracking-wider text-ink-soft">
              <th className="px-2 py-1.5 font-medium">Package</th>
              <th className="px-2 py-1.5 font-medium">Version</th>
              <th className="px-2 py-1.5 font-medium">Advisory</th>
              <th className="px-2 py-1.5 font-medium">Severity</th>
              <th className="px-2 py-1.5 font-medium">Reachability</th>
              <th className="px-2 py-1.5 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr
                key={f.cveId}
                onClick={() => onSelect(f.cveId)}
                className={`cursor-pointer border-b border-border/60 transition-colors hover:bg-surface-muted ${
                  f.cveId === selectedCve ? "bg-primary/5" : ""
                }`}
              >
                <td className="px-2 py-1 font-mono text-ink">{f.package}</td>
                <td className="px-2 py-1 font-mono text-ink-soft">
                  {f.installedVersion ?? "Not measured"}
                </td>
                <td className="px-2 py-1 font-mono text-ink-soft">{f.cveId}</td>
                <td className="px-2 py-1 font-mono text-ink-soft">
                  {f.severity || "Not measured"}
                </td>
                <td className="px-2 py-1 font-mono text-ink-soft">
                  {f.reachable === null
                    ? "Not measured"
                    : f.reachable
                      ? "Reachable"
                      : "Not reachable"}
                </td>
                <td className="px-2 py-1">
                  <ClassBadge classification={f.classification} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {findings.length > rows.length && (
        <div className="border-t border-border px-2 py-1.5">
          <button
            type="button"
            onClick={() => setShown((n) => n + TABLE_PAGE_SIZE)}
            className="rounded-md border border-border px-2 py-1 text-[10px] font-medium text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
          >
            Load more ({findings.length - rows.length} remaining)
          </button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- panel

export function DependencyRiskPanel({
  runId,
  status,
}: {
  runId: string;
  /** The agent's live status — refetches once A2 transitions to a settled state. */
  status?: AgentStatus;
}) {
  const [report, setReport] = useState<DependencyRiskReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const [viewMode, setViewMode] = useState<ViewMode>("riskmap");
  const [selectedCve, setSelectedCve] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [classFilter, setClassFilter] = useState<Set<DependencyClassification> | null>(null);
  const [gateFocus, setGateFocus] = useState<DependencyClassification | null>(null);
  const [gateHover, setGateHover] = useState<DependencyClassification | null>(null);
  const [drillPackage, setDrillPackage] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getDependencyRisk(runId)
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

  const presentClasses = useMemo(() => {
    if (!report) return [];
    return CLASS_ORDER.filter((c) => report.findings.some((f) => f.classification === c));
  }, [report]);
  const activeClasses = useMemo(
    () => classFilter ?? new Set(presentClasses),
    [classFilter, presentClasses],
  );

  const search_ = search.trim().toLowerCase();
  const filteredFindings = useMemo(() => {
    if (!report) return [];
    return report.findings.filter((f) => {
      if (!activeClasses.has(f.classification)) return false;
      if (gateFocus && f.classification !== gateFocus) return false;
      if (drillPackage && f.package !== drillPackage) return false;
      if (!search_) return true;
      return f.package.toLowerCase().includes(search_) || f.cveId.toLowerCase().includes(search_);
    });
  }, [report, activeClasses, gateFocus, drillPackage, search_]);

  const byClass = useMemo(() => {
    const map = new Map<DependencyClassification, DependencyFinding[]>();
    for (const c of CLASS_ORDER) map.set(c, []);
    for (const f of filteredFindings) map.get(f.classification)?.push(f);
    return map;
  }, [filteredFindings]);

  const selectedFinding = report?.findings.find((f) => f.cveId === selectedCve) ?? null;
  const selectedPackage = selectedFinding?.package ?? null;

  // Render strategy follows the real volume of the *unfiltered* result, so
  // narrowing a large repository with a filter does not silently change the
  // visualization out from under the user.
  const evidenceMode = evidenceModeFor(report?.findings.length ?? 0);
  const effectiveMode: EvidenceMode =
    evidenceMode === "terrain" && drillPackage ? "grouped" : evidenceMode;

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Dependency Risk Map
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Analyzing dependency graph…</p>
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
          Dependency Risk Map
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A2 is querying advisories now; this panel renders once it publishes."
            : "Pending — A2 has not completed dependency analysis for this run yet."}
        </p>
      </section>
    );
  }

  if (!report.manifest) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Dependency Risk Map
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          Dependency analysis unavailable — no manifest (requirements.txt) was found in this
          repository.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-4 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Dependency Risk Map
        </h3>
        <p className="mt-0.5 text-[11px] text-ink-soft">
          Every advisory OSV reported for {report.manifest}, narrowed by whether A1&apos;s import
          graph shows this repository actually reaching the package.
        </p>
      </div>

      {/* 1 — verdict. */}
      <p className="text-[13px] font-medium text-ink">{verdictFor(report)}</p>

      {/* 2 — the elimination gate. */}
      <ReachabilityGate
        report={report}
        activeClass={gateFocus}
        onFocusClass={(cls) => {
          setGateFocus(cls);
          setViewMode("riskmap");
        }}
        onHoverClass={setGateHover}
      />

      {report.advisoryCount === 0 ? (
        <div className="rounded-lg border border-status-completed/30 bg-status-completed-bg/40 p-3 text-[11px] text-ink">
          <div className="font-semibold text-status-completed">SAFE DEPENDENCY SET</div>
          <p className="mt-1 text-ink-soft">
            OSV reported no advisories for any of the {report.totalDependencies} dependencies A2
            analyzed from {report.manifest}.
          </p>
        </div>
      ) : (
        <>
          {/* 5 — search and the secondary table toggle. */}
          <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
            <div className="flex rounded-md border border-border p-0.5">
              {(
                [
                  ["riskmap", Network, "Evidence"],
                  ["table", Table2, "Table"],
                ] as const
              ).map(([mode, Icon, label]) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setViewMode(mode)}
                  className={`flex items-center gap-1 rounded px-2 py-1 text-[11px] font-medium transition-colors ${
                    viewMode === mode
                      ? "bg-primary/10 text-primary"
                      : "text-ink-soft hover:bg-surface-muted"
                  }`}
                >
                  <Icon className="h-3 w-3" />
                  {label}
                </button>
              ))}
            </div>

            <div className="relative min-w-[160px] flex-1">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-ink-soft" />
              <input
                ref={searchRef}
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search package or advisory…"
                aria-label="Search dependencies"
                className="w-full rounded-md border border-border bg-surface py-1 pl-7 pr-2 text-[11px] text-ink outline-none focus:border-primary/50"
              />
            </div>
          </div>

          {presentClasses.length > 1 && (
            <div
              className="flex flex-wrap items-center gap-1.5"
              role="group"
              aria-label="Filter by reachability"
            >
              {presentClasses.map((cls) => {
                const on = activeClasses.has(cls);
                return (
                  <button
                    key={cls}
                    type="button"
                    onClick={() =>
                      setClassFilter((prev) => {
                        const base = prev ?? new Set(presentClasses);
                        const next = new Set(base);
                        if (next.has(cls)) next.delete(cls);
                        else next.add(cls);
                        return next;
                      })
                    }
                    className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                      on ? "border-border text-ink" : "border-border/50 text-ink-soft opacity-50"
                    }`}
                  >
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ background: CLASS_COLOR[cls] }}
                    />
                    {CLASS_LABEL[cls]}
                  </button>
                );
              })}
              {(gateFocus || drillPackage) && (
                <button
                  type="button"
                  onClick={() => {
                    setGateFocus(null);
                    setDrillPackage(null);
                  }}
                  className="rounded-full border border-primary/40 bg-primary/5 px-2 py-0.5 text-[10px] font-medium text-primary"
                >
                  Clear {drillPackage ?? "focus"} ✕
                </button>
              )}
            </div>
          )}

          {/* 3 & 4 — evidence, adapted to the real result volume. */}
          <div className="flex flex-col gap-3 lg:flex-row">
            <div className="min-w-0 flex-1 space-y-2">
              {viewMode === "riskmap" &&
                (effectiveMode === "terrain" ? (
                  <ConcentrationTerrain findings={filteredFindings} onDrill={setDrillPackage} />
                ) : effectiveMode === "detail" ? (
                  <>
                    {CLASS_ORDER.map((cls) => {
                      const items = byClass.get(cls) ?? [];
                      if (items.length === 0) return null;
                      const dimmed = gateHover !== null && gateHover !== cls;
                      return (
                        <div
                          key={cls}
                          className={`space-y-1.5 transition-opacity ${
                            dimmed ? "opacity-40" : "opacity-100"
                          }`}
                        >
                          {items.map((f) =>
                            cls === "Critical" ? (
                              <ProofRail
                                key={f.cveId}
                                finding={f}
                                selected={f.cveId === selectedCve}
                                sharesSelectedPackage={
                                  selectedPackage === f.package && f.cveId !== selectedCve
                                }
                                dimmed={false}
                                onSelect={setSelectedCve}
                              />
                            ) : (
                              <EliminatedRow
                                key={f.cveId}
                                finding={f}
                                selected={f.cveId === selectedCve}
                                sharesSelectedPackage={
                                  selectedPackage === f.package && f.cveId !== selectedCve
                                }
                                dimmed={false}
                                onSelect={setSelectedCve}
                              />
                            ),
                          )}
                        </div>
                      );
                    })}
                  </>
                ) : (
                  CLASS_ORDER.map((cls) => (
                    <GroupSection
                      key={cls}
                      classification={cls}
                      findings={byClass.get(cls) ?? []}
                      defaultOpen={cls === "Critical"}
                      selectedCve={selectedCve}
                      selectedPackage={selectedPackage}
                      dimmedClass={gateHover}
                      onSelect={setSelectedCve}
                    />
                  ))
                ))}
              {viewMode === "table" && (
                <FindingsTable
                  findings={filteredFindings}
                  selectedCve={selectedCve}
                  onSelect={setSelectedCve}
                />
              )}
            </div>

            {selectedFinding && (
              <VulnerabilityDetail finding={selectedFinding} onClose={() => setSelectedCve(null)} />
            )}
          </div>
        </>
      )}

      {/* 6 — what was scanned. Quiet, and last. */}
      <div className="space-y-2 border-t border-border/60 pt-3">
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <StatChip label="Dependencies analyzed" value={report.totalDependencies} />
          <StatChip
            label="Manifest"
            value={`${report.manifest}${report.ecosystem ? ` · ${report.ecosystem}` : ""}`}
          />
          <DependencyExplanation report={report} />
        </div>
        <p className="text-[10px] leading-snug text-ink-soft/80">
          Reachability is A2&apos;s own verdict over OSV advisories, resolved against A1&apos;s
          import graph — not a CVSS band. Source:{" "}
          <code className="font-mono">GET /api/runs/{"{run_id}"}/dependency-risk</code>
        </p>
      </div>
    </section>
  );
}
