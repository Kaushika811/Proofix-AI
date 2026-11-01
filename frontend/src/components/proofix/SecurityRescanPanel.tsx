/**
 * A9 — Security Re-scan, as a reconciliation ledger.
 *
 * A9 is not "a security scan". It is the reconciliation of two scans: A3
 * produced a pre-patch baseline, A9 re-scans the patched repository, and the
 * only question is which post-patch findings have no counterpart in that
 * baseline. It compares counts per `(tool, file, message)` key and ignores
 * line numbers on purpose, because a patch that inserts a line shifts every
 * finding below it and a shift is not an introduction.
 *
 * That algorithm is the panel. Two columns — BASELINE on the left, POST-PATCH
 * on the right — every occurrence a chip, and a rail drawn between the chips
 * that paired, annotated with the number of lines the patch moved it by. The
 * reader *sees* the shift being absorbed instead of being asked to trust it. A
 * chip no rail reaches is introduced: the absence of a rail is the signal, and
 * multiplicity (one baseline occurrence, two post-patch ones → one rail, one
 * dangling chip) is the case the layout renders most plainly, because it is
 * exactly the case set difference would get wrong.
 *
 * What the panel refuses to say:
 * - **No severity, anywhere.** `_run_bandit`/`_run_semgrep` stamp a constant
 *   0.7 on every finding, so chips are never colour-graded, size-graded or
 *   sorted by it. The only encoded dimensions are matched-vs-dangling and lane.
 * - **No empty lane for a scanner that did not run.** It renders hatched and
 *   reads NOT COMPARED. "0 new findings" for a scan that never happened is a
 *   false clean, and it is the defect class A9 itself exists to eliminate.
 * - **No ruff lane.** `A9._keyed` excludes ruff as style, not security.
 * - **No headline score.** A9's score is `100 - 25 × introduced`, so it
 *   carries nothing the introduced count does not already say. It is a
 *   secondary field, and `null` renders as an em-dash, never 0 or 100.
 *
 * The verdict bar counts are derived from the same arrays the ledger renders
 * (`ledgerTotals`), never tallied separately — so the bar and the ledger
 * cannot disagree, including when a filter hides part of the ledger. NOT
 * COMPARED counts *scanners*, not findings, and is labelled as such.
 *
 * Scale is a first-class case, not a footnote. `density()` reads the real
 * dataset: under ~15 pairs every chip is drawn and labelled; past that,
 * findings group into collapsible per-file sections; past ~60 the carried bulk
 * collapses into a count band by default. Introduced findings always render
 * individually and always expanded at every scale — the rare signal is never
 * paginated behind the bulk.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, CircleAlert, CircleCheck, TriangleAlert } from "lucide-react";
import type {
  ReconciliationKey,
  ReconciliationLane,
  SecurityFinding,
  SecurityRescanReport,
  SecurityRetryContext,
} from "./securityRescanTypes";
import { getSecurityRescan } from "@/lib/runService";
import type { AgentStatus } from "./data";

/** The scanners A9 compares. Mirrors `a9_security_rescan.KNOWN_SCANNERS`. */
const KNOWN_SCANNERS = ["bandit", "semgrep"] as const;

// Card geometry. Fixed pitch is what lets the rails be drawn from index
// arithmetic alone — no DOM measurement, so a 3-pair group and a 300-pair one
// lay out identically (the technique `BlastRadiusMap` uses for its columns).
// Cards are tall enough to carry tool + message + file + line — the four
// facts the brief asks each chip to state on its own, without a shared
// per-key header the reader has to scroll back up to.
const ROW_H = 60;
const ROW_GAP = 10;
const PITCH = ROW_H + ROW_GAP;

/** Under this many carried pairs, every chip is drawn and every section open. */
const EXPLANATORY_MAX_PAIRS = 15;
/** Past this, carried pairs collapse to a count band unless asked for. */
const DENSE_MIN_PAIRS = 60;
/** Per-section render cap, so a 400-finding file cannot blow up the DOM. */
const KEYS_PER_SECTION = 40;

// ------------------------------------------------------------------ derivation

interface LedgerTotals {
  /** `null` for a run whose pairing was never recorded — unknown, not zero. */
  carried: number | null;
  introduced: number;
  /** Scanners not compared — never findings. */
  scannersNotCompared: number;
  scannersCompared: number;
}

/**
 * The verdict-bar counts, read off the same arrays the ledger draws.
 *
 * Deliberately takes the *full* lane list rather than the filtered one: a
 * filter changes what is on screen, never what A9 found.
 */
function ledgerTotals(lanes: ReconciliationLane[]): LedgerTotals {
  const totals: LedgerTotals = {
    carried: 0,
    introduced: 0,
    scannersNotCompared: 0,
    scannersCompared: 0,
  };
  for (const lane of lanes) {
    if (!lane.compared) {
      totals.scannersNotCompared += 1;
      continue;
    }
    totals.scannersCompared += 1;
    for (const key of lane.keys) {
      totals.carried = (totals.carried ?? 0) + key.rails.length;
      totals.introduced += key.introducedIds.length;
    }
  }
  return totals;
}

type Density = "explanatory" | "grouped" | "dense";

function density(totals: LedgerTotals): Density {
  const carried = totals.carried ?? 0;
  if (carried > DENSE_MIN_PAIRS) return "dense";
  if (carried > EXPLANATORY_MAX_PAIRS) return "grouped";
  return "explanatory";
}

interface FileGroup {
  file: string;
  keys: ReconciliationKey[];
  carried: number;
  introduced: number;
}

function groupByFile(keys: ReconciliationKey[]): FileGroup[] {
  const groups = new Map<string, FileGroup>();
  for (const key of keys) {
    const existing = groups.get(key.file);
    const target = existing ?? { file: key.file, keys: [], carried: 0, introduced: 0 };
    if (!existing) groups.set(key.file, target);
    target.keys.push(key);
    target.carried += key.rails.length;
    target.introduced += key.introducedIds.length;
  }
  // Sections holding an introduced finding sort first: the rare signal leads,
  // at every scale, before any cap or collapse can push it down.
  return [...groups.values()].sort(
    (a, b) => b.introduced - a.introduced || a.file.localeCompare(b.file),
  );
}

function hasIntroduced(key: ReconciliationKey): boolean {
  return key.introducedIds.length > 0;
}

function formatDelta(delta: number): string {
  if (delta === 0) return "±0";
  return delta > 0 ? `+${delta}` : String(delta);
}

// ---------------------------------------------------------------------- chips

interface ChipProps {
  id: string;
  tool: string;
  file: string;
  message: string;
  line: number | null;
  kind: "carried" | "introduced" | "resolved";
  label: string;
  dim: boolean;
  lit: boolean;
  onHover: (id: string | null) => void;
  onSelect?: () => void;
  animation: string;
}

const CARD_STYLE: Record<ChipProps["kind"], string> = {
  carried: "border-border bg-surface-muted/50",
  // The one saturated colour on the board, spent entirely on the occurrence
  // no rail reaches. Nothing else competes with it.
  introduced: "border-status-failed/60 bg-status-failed-bg/30",
  resolved: "border-dashed border-border bg-transparent opacity-70",
};

/**
 * One occurrence, as an evidence card — not a line-number pill. Tool, message
 * and file are restated on every card on purpose: at the point a reader's eye
 * lands on a dangling card, it must be able to answer "which finding is this"
 * without tracing back up to a shared header.
 */
function OccurrenceCard({
  id,
  tool,
  file,
  message,
  line,
  kind,
  label,
  dim,
  lit,
  onHover,
  onSelect,
  animation,
}: ChipProps) {
  return (
    <button
      type="button"
      data-chip-id={id}
      data-chip-kind={kind}
      aria-label={label}
      onMouseEnter={() => onHover(id)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(id)}
      onBlur={() => onHover(null)}
      onClick={onSelect}
      style={{ height: ROW_H }}
      className={`flex w-full flex-col justify-center gap-0.5 rounded-md border-l-2 border px-2 py-1 text-left transition-opacity duration-150 ${CARD_STYLE[kind]} ${animation} ${
        lit ? "ring-1 ring-primary/60" : ""
      } ${dim ? "opacity-20" : "opacity-100"}`}
    >
      <div className="flex items-center justify-between gap-1.5">
        <span className="truncate font-mono text-[8px] uppercase tracking-[0.1em] text-ink-soft">
          {tool}
        </span>
        <span className="shrink-0 font-mono text-[9px] tabular-nums text-ink">
          {line === null ? "—" : `L${line}`}
        </span>
      </div>
      <p className="truncate text-[10px] leading-tight text-ink" title={message}>
        {message}
      </p>
      <p className="truncate font-mono text-[9px] leading-tight text-ink-soft" title={file}>
        {file}
      </p>
      {kind === "introduced" && (
        <span className="text-[8px] font-bold uppercase tracking-[0.14em] text-status-failed">
          ↑ introduced — no baseline match
        </span>
      )}
      {kind === "resolved" && (
        <span className="text-[8px] uppercase tracking-[0.12em] text-ink-soft">
          resolved — no post-patch match
        </span>
      )}
    </button>
  );
}

// --------------------------------------------------------------- rail drawing

/**
 * One finding identity, both scans side by side, rails between what paired.
 *
 * Card rows are absolutely positioned at a fixed pitch, so the SVG path
 * endpoints are pure index arithmetic. Below `sm` the drawn rail is hidden and
 * the delta label carries the same fact. Tool, message and file are restated
 * on every card rather than hoisted into a shared header — a dangling
 * introduced card has to read as a complete finding on its own, without the
 * reader tracing back up the column to find out what it is.
 */
function KeyLedger({
  entry,
  hoveredId,
  onHover,
  selectedId,
  onSelect,
  retryContext,
  animate,
}: {
  entry: ReconciliationKey;
  hoveredId: string | null;
  onHover: (id: string | null) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  retryContext: SecurityRetryContext | null;
  animate: boolean;
}) {
  const rows = Math.max(entry.baseline.length, entry.post.length, 1);
  const height = rows * ROW_H + (rows - 1) * ROW_GAP;

  const baselineRow = new Map(entry.baseline.map((occ, i) => [occ.id, i]));
  const postRow = new Map(entry.post.map((occ, i) => [occ.id, i]));
  const introduced = new Set(entry.introducedIds);
  const resolved = new Set(entry.resolvedIds);
  const railByEndpoint = new Map<string, (typeof entry.rails)[number]>();
  for (const rail of entry.rails) {
    railByEndpoint.set(rail.baselineId, rail);
    railByEndpoint.set(rail.postId, rail);
  }

  const ownsHover = hoveredId !== null && (baselineRow.has(hoveredId) || postRow.has(hoveredId));
  const hoveredRail = hoveredId ? railByEndpoint.get(hoveredId) : undefined;
  const litIds = new Set<string>();
  if (ownsHover && hoveredId) {
    litIds.add(hoveredId);
    if (hoveredRail) {
      litIds.add(hoveredRail.baselineId);
      litIds.add(hoveredRail.postId);
    }
  }
  const dimming = hoveredId !== null;
  const isDim = (id: string) => dimming && !litIds.has(id);

  const yOf = (row: number) => row * PITCH + ROW_H / 2;
  const selectedIntroduced = selectedId && introduced.has(selectedId) ? selectedId : null;
  const hoveredMatchedRail =
    ownsHover && hoveredId && !introduced.has(hoveredId)
      ? railByEndpoint.get(hoveredId)
      : undefined;

  return (
    <div
      className={`rounded-lg border border-border bg-surface p-2.5 ${
        dimming && !ownsHover ? "opacity-40 transition-opacity duration-150" : ""
      }`}
    >
      <div
        className="grid items-start gap-x-2"
        style={{ gridTemplateColumns: "minmax(0,1fr) 64px minmax(0,1fr)" }}
      >
        <div className="relative" style={{ height }}>
          {entry.baseline.map((occ, i) => (
            <div key={occ.id} className="absolute inset-x-0" style={{ top: i * PITCH }}>
              <OccurrenceCard
                id={occ.id}
                tool={entry.tool}
                file={entry.file}
                message={entry.message}
                line={occ.line}
                kind={resolved.has(occ.id) ? "resolved" : "carried"}
                label={
                  resolved.has(occ.id)
                    ? `Baseline ${entry.tool} finding in ${entry.file} at line ${occ.line}, no post-patch counterpart`
                    : `Baseline ${entry.tool} finding in ${entry.file} at line ${occ.line}, carried into the post-patch scan`
                }
                dim={isDim(occ.id)}
                lit={litIds.has(occ.id)}
                onHover={onHover}
                animation={animate ? "animate-ledger-baseline-in" : ""}
              />
            </div>
          ))}
        </div>

        <div className="relative" style={{ height }} aria-hidden>
          <svg
            className="absolute inset-0 hidden h-full w-full sm:block"
            viewBox={`0 0 100 ${height}`}
            preserveAspectRatio="none"
          >
            {entry.rails.map((rail) => {
              const y1 = yOf(baselineRow.get(rail.baselineId) ?? 0);
              const y2 = yOf(postRow.get(rail.postId) ?? 0);
              const litRail = litIds.has(rail.postId);
              return (
                <path
                  key={rail.postId}
                  d={`M0,${y1} C38,${y1} 62,${y2} 100,${y2}`}
                  fill="none"
                  vectorEffect="non-scaling-stroke"
                  strokeWidth={litRail ? 1.75 : 1}
                  stroke={
                    litRail
                      ? "var(--color-primary)"
                      : "color-mix(in oklab, var(--color-ink-soft) 45%, transparent)"
                  }
                  opacity={dimming && !litRail ? 0.15 : 1}
                  className={animate ? "animate-ledger-rail-draw" : ""}
                />
              );
            })}
          </svg>
          {entry.rails.map((rail) => {
            const row = postRow.get(rail.postId) ?? 0;
            const litRail = litIds.has(rail.postId);
            return (
              <span
                key={rail.postId}
                style={{ top: row * PITCH, height: ROW_H }}
                className={`absolute inset-x-0 flex items-center justify-center font-mono text-[9px] tabular-nums transition-opacity duration-150 ${
                  litRail ? "font-semibold text-primary" : "text-ink-soft"
                } ${dimming && !litRail ? "opacity-15" : "opacity-100"}`}
              >
                {formatDelta(rail.lineDelta)}
              </span>
            );
          })}
        </div>

        <div className="relative" style={{ height }}>
          {entry.post.map((occ, i) => {
            const isNew = introduced.has(occ.id);
            const rail = railByEndpoint.get(occ.id);
            return (
              <div key={occ.id} className="absolute inset-x-0" style={{ top: i * PITCH }}>
                <OccurrenceCard
                  id={occ.id}
                  tool={entry.tool}
                  file={entry.file}
                  message={entry.message}
                  line={occ.line}
                  kind={isNew ? "introduced" : "carried"}
                  label={
                    isNew
                      ? `Introduced ${entry.tool} finding in ${entry.file} at line ${occ.line} — no baseline counterpart`
                      : `Post-patch ${entry.tool} finding in ${entry.file} at line ${occ.line}, carried from baseline line ${
                          entry.baseline.find((b) => b.id === rail?.baselineId)?.line ?? "unknown"
                        }, moved ${formatDelta(rail?.lineDelta ?? 0)} lines`
                  }
                  dim={isDim(occ.id)}
                  lit={litIds.has(occ.id)}
                  onHover={onHover}
                  onSelect={
                    isNew ? () => onSelect(selectedId === occ.id ? null : occ.id) : undefined
                  }
                  animation={
                    animate ? (isNew ? "animate-ledger-settle" : "animate-ledger-post-in") : ""
                  }
                />
              </div>
            );
          })}
        </div>
      </div>

      {selectedIntroduced || (ownsHover && hoveredId && introduced.has(hoveredId)) ? (
        <IntroducedExplanation entry={entry} retryContext={retryContext} />
      ) : hoveredMatchedRail ? (
        <MatchedExplanation entry={entry} rail={hoveredMatchedRail} />
      ) : null}
    </div>
  );
}

/** Same finding, both scans — the fact a rail asserts, stated in words. */
function MatchedExplanation({
  entry,
  rail,
}: {
  entry: ReconciliationKey;
  rail: ReconciliationKey["rails"][number];
}) {
  const baselineLine = entry.baseline.find((occ) => occ.id === rail.baselineId)?.line ?? null;
  const postLine = entry.post.find((occ) => occ.id === rail.postId)?.line ?? null;
  return (
    <div className="mt-2.5 rounded-md border border-border bg-surface-muted/40 p-2.5 text-[11px] text-ink">
      <p className="font-medium text-status-completed">Same finding</p>
      <dl className="mt-1.5 space-y-1">
        <div className="flex flex-wrap gap-x-1.5">
          <dt className="text-ink-soft">Key:</dt>
          <dd className="font-mono text-[10px]">
            {entry.tool} + {entry.file} + &ldquo;{entry.message}&rdquo;
          </dd>
        </div>
        <div className="flex flex-wrap gap-x-1.5">
          <dt className="text-ink-soft">Line moved:</dt>
          <dd className="font-mono text-[10px] tabular-nums">
            {baselineLine ?? "—"} → {postLine ?? "—"}
            {rail.lineDelta !== 0 ? ` (${formatDelta(rail.lineDelta)})` : " (unchanged)"}
          </dd>
        </div>
      </dl>
    </div>
  );
}

/** Why this card has no rail, and what A9 does about it next. */
function IntroducedExplanation({
  entry,
  retryContext,
}: {
  entry: ReconciliationKey;
  retryContext: SecurityRetryContext | null;
}) {
  const occurrence = entry.post.find((occ) => entry.introducedIds.includes(occ.id));
  return (
    <div
      role="note"
      className="mt-2.5 rounded-md border border-status-failed/25 bg-status-failed-bg/25 p-2.5 text-[11px] text-ink"
    >
      <p>
        No matching baseline occurrence exists for this (tool, file, message):{" "}
        <span className="font-mono text-[10px]">
          ({entry.tool}, {entry.file})
        </span>
        .
      </p>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-status-failed/20 pt-2 text-[10px]">
        <div>
          <dt className="uppercase tracking-wider text-ink-soft">Tool</dt>
          <dd className="mt-0.5 font-mono">{entry.tool}</dd>
        </div>
        <div>
          <dt className="uppercase tracking-wider text-ink-soft">Line</dt>
          <dd className="mt-0.5 font-mono">{occurrence?.line ?? "—"}</dd>
        </div>
        <div className="col-span-2">
          <dt className="uppercase tracking-wider text-ink-soft">File</dt>
          <dd className="mt-0.5 truncate font-mono" title={entry.file}>
            {entry.file}
          </dd>
        </div>
        <div className="col-span-2">
          <dt className="uppercase tracking-wider text-ink-soft">Message</dt>
          <dd className="mt-0.5">{entry.message}</dd>
        </div>
      </dl>
      {retryContext?.assertionMessage || retryContext?.securityConstraint ? (
        <dl className="mt-2 space-y-1.5 border-t border-status-failed/20 pt-2">
          {retryContext.assertionMessage && (
            <div>
              <dt className="text-[9px] uppercase tracking-wider text-ink-soft">Assertion</dt>
              <dd className="mt-0.5">{retryContext.assertionMessage}</dd>
            </div>
          )}
          {retryContext.securityConstraint && (
            <div>
              <dt className="text-[9px] uppercase tracking-wider text-ink-soft">
                Constraint carried into the next repair attempt
              </dt>
              <dd className="mt-0.5">{retryContext.securityConstraint}</dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="mt-1.5 text-ink-soft">A9 recorded no retry constraint for this run.</p>
      )}
      <p className="mt-2 border-t border-status-failed/20 pt-2 text-[10px] font-bold uppercase tracking-[0.12em] text-status-failed">
        Patch requires revision
      </p>
    </div>
  );
}

// ------------------------------------------------------------- file sections

function FileSection({
  group,
  mode,
  hoveredId,
  onHover,
  selectedId,
  onSelect,
  retryContext,
  animate,
}: {
  group: FileGroup;
  mode: Density;
  hoveredId: string | null;
  onHover: (id: string | null) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  retryContext: SecurityRetryContext | null;
  animate: boolean;
}) {
  // A section holding an introduced finding is always open, at every scale.
  const forcedOpen = group.introduced > 0;
  const [open, setOpen] = useState(forcedOpen || mode === "explanatory");
  // In dense mode the carried bulk is behind one more click; the introduced
  // findings inside it are not.
  const [showCarried, setShowCarried] = useState(mode !== "dense");
  const [limit, setLimit] = useState(KEYS_PER_SECTION);

  const expanded = forcedOpen || open;
  const introducedKeys = group.keys.filter(hasIntroduced);
  const carriedOnlyKeys = group.keys.filter((k) => !hasIntroduced(k));
  const rendered = showCarried ? [...introducedKeys, ...carriedOnlyKeys] : introducedKeys;
  const visible = rendered.slice(0, limit);
  const hiddenCount = rendered.length - visible.length;

  return (
    <div className="rounded-lg border border-border bg-surface-muted/20">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left"
      >
        <ChevronRight
          className={`h-3 w-3 shrink-0 text-ink-soft transition-transform ${expanded ? "rotate-90" : ""}`}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-ink">{group.file}</span>
        <span className="shrink-0 text-[10px] text-ink-soft">
          {group.carried} carried
          {group.introduced > 0 && (
            <>
              {", "}
              <span className="font-semibold text-status-failed">
                {group.introduced} introduced
              </span>
            </>
          )}
        </span>
      </button>

      {expanded && (
        <div className="space-y-2 border-t border-border px-2.5 py-2.5">
          {!showCarried && carriedOnlyKeys.length > 0 && (
            <button
              type="button"
              onClick={() => setShowCarried(true)}
              className="ledger-hatch flex w-full items-center justify-between rounded-md border border-border px-2.5 py-2 text-left text-[10px] text-ink-soft"
            >
              <span>
                {group.carried} carried, unchanged — every occurrence paired with a baseline one
              </span>
              <span className="shrink-0 font-medium text-ink">Show pairs</span>
            </button>
          )}
          {visible.map((entry) => (
            <KeyLedger
              key={entry.id}
              entry={entry}
              hoveredId={hoveredId}
              onHover={onHover}
              selectedId={selectedId}
              onSelect={onSelect}
              retryContext={retryContext}
              animate={animate}
            />
          ))}
          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setLimit((n) => n + KEYS_PER_SECTION)}
              className="w-full rounded-md border border-border px-2.5 py-1.5 text-[10px] text-ink-soft hover:bg-surface-muted"
            >
              Show {Math.min(hiddenCount, KEYS_PER_SECTION)} more
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- lanes

function NotComparedLane({ tool }: { tool: string }) {
  return (
    <div
      data-lane={tool}
      data-compared="false"
      className="ledger-hatch rounded-lg border border-dashed border-border px-3 py-3"
    >
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink">{tool}</span>
        <span className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-status-retry">
          <span aria-hidden>◌</span> Not compared — scanner did not run
        </span>
      </div>
      <p className="mt-1 text-[10px] text-ink-soft">
        Post-patch result unavailable for this scanner. Its baseline is excluded from the comparison
        on both sides — this is not a pass.
      </p>
    </div>
  );
}

function Lane({
  lane,
  mode,
  hoveredId,
  onHover,
  selectedId,
  onSelect,
  retryContext,
  animate,
}: {
  lane: ReconciliationLane;
  mode: Density;
  hoveredId: string | null;
  onHover: (id: string | null) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  retryContext: SecurityRetryContext | null;
  animate: boolean;
}) {
  if (!lane.compared) return <NotComparedLane tool={lane.tool} />;

  const groups = groupByFile(lane.keys);
  return (
    <div data-lane={lane.tool} data-compared="true" className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-[0.14em] text-ink">
          {lane.tool}
        </span>
        <span className="h-px flex-1 bg-border" aria-hidden />
      </div>
      {groups.length === 0 ? (
        <p className="rounded-lg border border-border bg-surface-muted/20 px-2.5 py-2 text-[10px] text-ink-soft">
          Ran and reported nothing, in either scan.
        </p>
      ) : (
        <div className="space-y-2">
          {groups.map((group) => (
            <FileSection
              key={group.file}
              group={group}
              mode={mode}
              hoveredId={hoveredId}
              onHover={onHover}
              selectedId={selectedId}
              onSelect={onSelect}
              retryContext={retryContext}
              animate={animate}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------- verdict bar

/**
 * Three states, never a fourth. An introduced finding is a hard fact A9
 * measured directly, so it outranks completeness — a patch that introduced a
 * finding is rejected whether or not the carried side was ever persisted.
 * Absent that, an incomplete comparison is never allowed to read as a clean
 * one: no scanner run, or no stored pairing, both say so explicitly instead
 * of falling through to "no new findings".
 */
type Verdict = "rejected" | "incomplete" | "clean";

function deriveVerdict(totals: LedgerTotals, reconciliationAvailable: boolean): Verdict {
  if (totals.introduced > 0) return "rejected";
  if (!reconciliationAvailable || totals.scannersCompared === 0) return "incomplete";
  return "clean";
}

function VerdictBar({
  totals,
  reconciliationAvailable,
}: {
  totals: LedgerTotals;
  reconciliationAvailable: boolean;
}) {
  const verdict = deriveVerdict(totals, reconciliationAvailable);
  const incompleteReason = !reconciliationAvailable
    ? "not recorded"
    : totals.scannersCompared === 0
      ? "no scanner compared"
      : null;

  const HEADLINE: Record<Verdict, { icon: typeof CircleCheck; text: string; color: string }> = {
    rejected: { icon: CircleAlert, text: "Patch rejected", color: "text-status-failed" },
    incomplete: {
      icon: TriangleAlert,
      text: "Reconciliation incomplete",
      color: "text-status-retry",
    },
    clean: { icon: CircleCheck, text: "No new findings", color: "text-status-completed" },
  };
  const { icon: Icon, text: headline, color } = HEADLINE[verdict];

  const sentence =
    verdict === "rejected"
      ? `Patch introduces ${totals.introduced} finding${totals.introduced === 1 ? "" : "s"} not present in baseline → PATCH REJECTED`
      : verdict === "incomplete"
        ? !reconciliationAvailable
          ? "No introduced findings were recorded. Carried findings cannot be displayed because baseline/post-patch pairing was not recorded."
          : "Nothing was compared — no scanner ran, so this is not a clean result."
        : "No new findings — all comparable scanner findings were already present.";

  return (
    <div
      role="status"
      className={`rounded-xl border px-3 py-2.5 ${
        verdict === "rejected"
          ? "border-status-failed/30 bg-status-failed-bg/40"
          : verdict === "incomplete"
            ? "border-status-retry/30 bg-status-retry-bg/20"
            : "border-border bg-surface-muted/40"
      }`}
    >
      <p className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
        Security verdict
      </p>
      <div className={`mt-1 flex items-center gap-1.5 text-sm font-semibold ${color}`}>
        <Icon className="h-4 w-4 shrink-0" aria-hidden />
        {headline}
      </div>
      {verdict === "incomplete" ? (
        <dl className="mt-1.5 space-y-0.5 text-[11px] text-ink-soft">
          <div className="flex gap-1.5">
            <dt>Introduced findings:</dt>
            <dd className="font-mono tabular-nums text-ink">{totals.introduced}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt>Baseline pairing:</dt>
            <dd className="font-mono text-ink">{incompleteReason}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-1 text-[11px] text-ink-soft">
          {totals.introduced} introduced finding{totals.introduced === 1 ? "" : "s"}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 border-t border-border/60 pt-2 font-mono text-[11px] tabular-nums">
        <span className="text-ink">
          {totals.carried === null ? "— CARRIED (not recorded)" : `${totals.carried} CARRIED`}
        </span>
        <span className="text-ink-soft" aria-hidden>
          ·
        </span>
        <span className={totals.introduced > 0 ? "font-semibold text-status-failed" : "text-ink"}>
          {totals.introduced} INTRODUCED
        </span>
        <span className="text-ink-soft" aria-hidden>
          ·
        </span>
        <span className="text-ink">
          {totals.scannersNotCompared} SCANNER{totals.scannersNotCompared === 1 ? "" : "S"} NOT
          COMPARED
        </span>
      </div>
      <p
        className={`mt-1 text-[11px] ${verdict === "rejected" ? "font-medium text-status-failed" : "text-ink-soft"}`}
      >
        {sentence}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------- filters

type Scope = "all" | "introduced";

function LedgerFilters({
  scope,
  onScope,
  scanner,
  onScanner,
  lanes,
}: {
  scope: Scope;
  onScope: (s: Scope) => void;
  scanner: string;
  onScanner: (s: string) => void;
  lanes: ReconciliationLane[];
}) {
  const select = "rounded-md border border-border bg-surface px-2 py-1 text-[10px] text-ink";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-ink-soft">
        Show
        <select
          className={select}
          value={scope}
          onChange={(e) => onScope(e.target.value as Scope)}
          aria-label="Filter findings"
        >
          <option value="all">All findings</option>
          <option value="introduced">Introduced only</option>
        </select>
      </label>
      <label className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-ink-soft">
        Scanner
        <select
          className={select}
          value={scanner}
          onChange={(e) => onScanner(e.target.value)}
          aria-label="Filter by scanner"
        >
          <option value="all">All scanners</option>
          {lanes.map((lane) => (
            <option key={lane.tool} value={lane.tool}>
              {lane.tool}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

// ------------------------------------------------------------------ fallbacks

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load security re-scan</span>
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

/**
 * A run that completed before A9 recorded the reconciliation. The pairing was
 * never stored, so drawing an empty ledger would assert "nothing carried" — a
 * claim about a comparison nobody kept. It says so instead, and lists the one
 * side that was persisted.
 */
function LegacyLedger({ findings }: { findings: SecurityFinding[] }) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-1.5 rounded-md border border-dashed border-status-retry/40 bg-status-retry-bg/15 px-2.5 py-1.5">
        <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-status-retry">
          ◌ Reconciliation unavailable
        </span>
        <span className="text-[10px] text-ink-soft">
          Baseline ↔ post-patch pairing was not recorded for this run. Only introduced findings were
          persisted.
        </span>
      </div>
      {findings.length === 0 ? (
        <p className="rounded-lg border border-border bg-surface-muted/20 px-3 py-2 text-[11px] text-ink-soft">
          No introduced findings were recorded.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {findings.map((f) => (
            <li
              key={f.id}
              className="rounded-lg border-l-2 border border-status-failed/50 bg-status-failed-bg/25 px-3 py-2"
            >
              <div className="flex items-center justify-between gap-1.5">
                <span className="font-mono text-[8px] uppercase tracking-[0.1em] text-ink-soft">
                  {f.tools.length > 0 ? f.tools.join(", ") : "unknown tool"}
                </span>
                <span className="font-mono text-[10px] tabular-nums text-ink">L{f.line}</span>
              </div>
              <p className="mt-1 text-[11px] text-ink">{f.message}</p>
              <div className="mt-1 truncate font-mono text-[10px] text-ink-soft">{f.file}</div>
              <span className="mt-1 block text-[8px] font-bold uppercase tracking-[0.14em] text-status-failed">
                ↑ introduced — no baseline match
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------- secondary + detail

/**
 * Kept strictly secondary — one line, no border-weight to compete with the
 * verdict above it. The score says nothing the reconciliation map does not
 * already show; it exists for A10's handoff, not for the reader's eye.
 */
function SecondaryScore({ report }: { report: SecurityRescanReport }) {
  return (
    <p className="flex items-baseline gap-1.5 px-0.5 text-[10px] text-ink-soft">
      <span className="uppercase tracking-wider">Security score</span>
      <span className="font-mono tabular-nums text-ink">
        {report.securityScore === null ? "— not measured" : report.securityScore.toFixed(0)}
      </span>
      <span>secondary to the map above</span>
    </p>
  );
}

function Details({ report }: { report: SecurityRescanReport }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 text-[11px] font-medium text-ink"
      >
        Re-execution &amp; retry detail
        <ChevronDown
          className={`h-3.5 w-3.5 text-ink-soft transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-3 py-2 text-[11px] text-ink-soft">
          <div>
            <span>Re-execution command</span>
            <pre className="mt-1 overflow-x-auto rounded-md bg-surface-muted/50 p-2 font-mono text-[10px] text-ink">
              {report.reexecutionCommand || "—"}
            </pre>
          </div>
          <div>
            Timeout:{" "}
            <span className="font-mono text-ink">{report.reexecutionTimeoutSeconds ?? "—"}s</span>
          </div>
          <div>
            A9 supplies inputs to A10&apos;s mergeability decision — security axis{" "}
            <span className="font-mono text-ink">
              {report.securityScore === null ? "not measured" : report.securityScore.toFixed(0)}
            </span>
            , hard gate{" "}
            <span className="font-mono text-ink">
              {report.rejected ? "blocks auto-merge" : "does not block"}
            </span>
            . The decision itself is on the merge card.
          </div>
          {report.retryContext?.assertionMessage && (
            <div className="border-t border-border/60 pt-2">
              <span className="text-[10px] uppercase tracking-wider">Assertion</span>
              <p className="mt-0.5 text-ink">{report.retryContext.assertionMessage}</p>
            </div>
          )}
          {report.retryContext?.securityConstraint && (
            <div>
              <span className="text-[10px] uppercase tracking-wider">
                Security constraint for retry
              </span>
              <p className="mt-0.5 text-ink">{report.retryContext.securityConstraint}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- panel

function Shell({ children }: { children: ReactNode }) {
  return (
    <section className="space-y-4 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Security Re-scan
        </h3>
        {children}
      </div>
    </section>
  );
}

export function SecurityRescanPanel({ runId, status }: { runId: string; status?: AgentStatus }) {
  const [report, setReport] = useState<SecurityRescanReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>("all");
  const [scanner, setScanner] = useState("all");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSecurityRescan(runId)
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

  const lanes = useMemo<ReconciliationLane[]>(() => {
    const supplied = report?.reconciliation ?? [];
    // Both scanners always get a lane, even if the payload omitted one: a
    // missing lane and a lane that ran clean must not look alike.
    return KNOWN_SCANNERS.map(
      (tool) => supplied.find((lane) => lane.tool === tool) ?? { tool, compared: false, keys: [] },
    );
  }, [report]);

  // Derived from the full lane list, never from what a filter leaves on screen.
  // For a run whose pairing was never recorded, the carried side is unknown
  // rather than zero, and the introduced count comes from the one array that
  // was persisted — so the bar still cannot disagree with what is rendered.
  const totals = useMemo(() => {
    const computed = ledgerTotals(lanes);
    if (report && !report.reconciliationAvailable) {
      return { ...computed, carried: null, introduced: report.newFindings.length };
    }
    return computed;
  }, [lanes, report]);
  const mode = density(totals);

  const visibleLanes = useMemo(
    () =>
      lanes
        .filter((lane) => scanner === "all" || lane.tool === scanner)
        .map((lane) =>
          scope === "introduced" ? { ...lane, keys: lane.keys.filter(hasIntroduced) } : lane,
        ),
    [lanes, scanner, scope],
  );

  if (loading) {
    return (
      <Shell>
        <p className="mt-1.5 text-xs text-ink-soft">Security re-scan loading…</p>
      </Shell>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!report) {
    return (
      <Shell>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A9 is re-scanning the patched repository now; this panel renders once it publishes."
            : "Security rescan pending — A9 has not completed yet."}
        </p>
      </Shell>
    );
  }

  return (
    <section className="space-y-4 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 className="text-[10px] font-medium uppercase tracking-[0.14em] text-ink-soft">
          Security reconciliation
        </h3>
        <p className="mt-0.5 text-sm font-medium text-ink">
          Did the patch introduce anything that wasn&apos;t present before?
        </p>
      </div>

      {report.reconciliationAvailable ? (
        <>
          <LedgerFilters
            scope={scope}
            onScope={setScope}
            scanner={scanner}
            onScanner={setScanner}
            lanes={lanes}
          />

          <div className="grid grid-cols-3 gap-x-2 border-b border-border px-1 pb-1.5 text-[9px] font-medium uppercase tracking-wider text-ink-soft">
            <span>
              A3 baseline{" "}
              <span className="normal-case tracking-normal text-ink-soft/70">— before patch</span>
            </span>
            <span className="text-center">rails</span>
            <span>
              A9 rescan{" "}
              <span className="normal-case tracking-normal text-ink-soft/70">— after patch</span>
            </span>
          </div>

          <div className="space-y-4">
            {visibleLanes.map((lane) => (
              <Lane
                key={lane.tool}
                lane={lane}
                mode={mode}
                hoveredId={hoveredId}
                onHover={setHoveredId}
                selectedId={selectedId}
                onSelect={setSelectedId}
                retryContext={report.retryContext}
                animate={mode === "explanatory"}
              />
            ))}
          </div>
        </>
      ) : (
        <LegacyLedger findings={report.newFindings} />
      )}

      <VerdictBar totals={totals} reconciliationAvailable={report.reconciliationAvailable} />
      <SecondaryScore report={report} />
      <Details report={report} />
    </section>
  );
}
