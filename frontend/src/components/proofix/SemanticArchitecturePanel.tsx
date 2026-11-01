/**
 * A1 — Semantic Risk Terrain (adaptive).
 *
 * Everything on this panel comes from `GET /api/runs/{runId}/semantic-graph`
 * (`services/ui_projection.py::build_semantic_graph`, sourced from A1's own
 * `SemanticIntentGraph`). No role, count, edge or criticality figure is
 * computed client-side beyond deterministic aggregation of that response —
 * bucketing, ranking, and averaging over the real per-file fields. Nothing
 * is invented, and nothing is fetched that isn't already part of this
 * contract.
 *
 * This is deliberately NOT the A0.5 Repository Knowledge Graph
 * (`RepositoryIntelligencePanel`). A0.5 answers "what exists and how is it
 * wired" — a containment tree of files/classes/functions plus import edges.
 * A1 answers "what does this code appear to do, and where does that make it
 * dangerous to touch" — one of six architectural roles per file
 * (`SemanticRole`), weighed against how connected and how volatile that file
 * is.
 *
 * THE RISK SPINE. A deterministic, sorted layout — never force-directed,
 * never simulated — so the same repository renders identically twice and the
 * panel is comparable run over run. Six role lanes in harm order; criticality
 * on x (low left, high right); mark size = fan-in (`imported_by`); fill = a
 * single-hue sequential ramp over `churn_weight`. Role is a positional
 * channel (the lane), which is what frees color to carry churn instead of
 * duplicating the role a second time.
 *
 * The rule the whole layout serves: high criticality + wide fan-in + high
 * churn must read as dangerous without inspecting anything. That is the
 * upper-right of the harmful lanes, washed and captioned as the danger
 * corner.
 *
 * ADAPTIVE DENSITY. The encoding never changes with repository size; only
 * the mark does, so crossing a threshold never changes what an axis means:
 *   - atlas (<= 80 files):  one row per file. Collisions are impossible by
 *     construction and every file keeps its name. Empty roles collapse to a
 *     compact muted row, so a four-file repository occupies four small
 *     regions rather than four oversized boxes.
 *   - swarm (<= 800):       beeswarm packing inside the lane so no mark
 *     hides another; only the highest-stakes files keep labels.
 *   - ridge (> 800):        a per-lane density ribbon tinted by that bin's
 *     mean churn, with outliers lifted into a reserved band above it. The
 *     mass becomes shape; the danger corner stays individually drawn.
 *
 * LABELS. Every mark reserves its own footprint before any label is placed,
 * across all lanes. A label that cannot find clean space is dropped, never
 * overlapped — the mark is still there, still hoverable, still clickable.
 *
 * DEPENDENCIES. Import edges are never all drawn at once; at repository
 * scale that is an unreadable hairball. Selecting a file keeps the spine on
 * screen, rings the mark, fades everything that is not a one-hop neighbour,
 * and answers "what does this depend on / what depends on this" in
 * `FileDetail` — incoming and outgoing listed separately, capped, with a
 * "+N more" count.
 *
 * PERFORMANCE. Ridge mode bounds DOM cost by (6 lanes x bins), not by file
 * count, which is what keeps a 5,000-file repository renderable. Layout is
 * memoized on the graph and the measured plot width, and is not recomputed
 * on hover.
 *
 * BOUNDS. `criticality` and `churn_weight` are backend-guaranteed to [0, 1]
 * (`services/ast_import_graph.compute_criticality` clamps with `min(1.0, ...)`
 * over non-negative terms; `services/git_service.get_churn_weights` divides
 * by its own max). That guarantee is what licenses a fixed [0, 1] x-domain
 * here rather than a quantile scale, which would make position track file
 * *count* and break the "x = criticality" contract the legend promises.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  KeyRound,
  Database,
  Globe2,
  Settings2,
  FlaskConical,
  Wrench,
  ArrowRight,
  ArrowDown,
  TriangleAlert,
  Search,
  type LucideIcon,
} from "lucide-react";
import type { SemanticFile, SemanticGraphExport, SemanticRole } from "./semanticGraphTypes";
import { getSemanticGraph } from "@/lib/runService";
import type { AgentStatus } from "./data";

// ------------------------------------------------------------- role vocabulary

/** Top-to-bottom lane order = structural-risk order, per A1's own taxonomy —
 * boundaries the code trusts (auth, data) first, inert scaffolding (tests)
 * last. This is a display order only; it never changes which six roles exist. */
const LANE_ORDER: SemanticRole[] = [
  "auth-boundary",
  "public-api",
  "data-access",
  "config-surface",
  "internal-util",
  "test-only",
];

const ROLE_LABEL: Record<SemanticRole, string> = {
  "auth-boundary": "Auth Boundary",
  "data-access": "Data Access",
  "public-api": "Public API",
  "config-surface": "Config Surface",
  "test-only": "Test Only",
  "internal-util": "Internal Utility",
};

const ROLE_COLOR: Record<SemanticRole, string> = {
  "auth-boundary": "#dc2626",
  "data-access": "#0891b2",
  "public-api": "#3b82f6",
  "config-surface": "#d97706",
  "test-only": "#16a34a",
  "internal-util": "#64748b",
};

const ROLE_ICON: Record<SemanticRole, LucideIcon> = {
  "auth-boundary": KeyRound,
  "data-access": Database,
  "public-api": Globe2,
  "config-surface": Settings2,
  "test-only": FlaskConical,
  "internal-util": Wrench,
};

function fileName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

/** The directory portion of a real path — a deterministic string split, not
 * a guess. `"vulnapi/payments/gateway.py"` → `"vulnapi/payments"`; a
 * top-level file with no directory maps to `"(root)"` rather than an empty
 * string, so it groups distinctly instead of colliding with a real package
 * that happens to be named "". */
function moduleOf(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx === -1 ? "(root)" : path.slice(0, idx);
}

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, n));
}

// ------------------------------------------------------------------ helpers

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load the semantic graph</span>
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

/** Both from the existing "hot" definition: criticality and churn each
 * individually in the top band. Fixed, not per-repo, because both fields are
 * backend-guaranteed to [0, 1] — see the module docblock. */
const HOT_THRESHOLD = 0.7;

/**
 * "184 files across 5 architectural roles. 12 files have high criticality
 * and high churn." — or, when nothing crosses that combo, a role
 * concentration observation among individually-critical files. Never a
 * bare count with no supporting signal is upgraded into a claim.
 */
function buildGeneratedInsight(graph: SemanticGraphExport, hotCount: number): string {
  const roleCount = LANE_ORDER.filter((r) => (graph.roleCounts[r] ?? 0) > 0).length;
  const lead = `${graph.totalFiles} file${graph.totalFiles === 1 ? "" : "s"} across ${roleCount} architectural role${
    roleCount === 1 ? "" : "s"
  }.`;

  if (hotCount > 0) {
    return `${lead} ${hotCount} file${hotCount === 1 ? "" : "s"} ${
      hotCount === 1 ? "has" : "have"
    } high criticality and high churn.`;
  }

  const critical = graph.files.filter((f) => f.criticality >= HOT_THRESHOLD);
  if (critical.length > 0) {
    const counts = new Map<SemanticRole, number>();
    for (const f of critical) counts.set(f.role, (counts.get(f.role) ?? 0) + 1);
    let topRole: SemanticRole | null = null;
    let topCount = 0;
    let tied = false;
    for (const [role, count] of counts) {
      if (count > topCount) {
        topRole = role;
        topCount = count;
        tied = false;
      } else if (count === topCount) {
        tied = true;
      }
    }
    if (topRole && !tied) {
      return `${lead} ${ROLE_LABEL[topRole]} contains the highest concentration of critical files.`;
    }
  }

  return lead;
}

// ---------------------------------------------------------- highest-risk rank

/**
 * "3 files · criticality ≥ 0.83" — the threshold is 90% of *this run's own*
 * highest criticality, never a hardcoded absolute. A cluster of 3+ files at
 * or above that line is reported as a cluster; anything smaller collapses to
 * naming the single top file. Null only when every file scores zero
 * criticality — there is nothing honest to rank in that case.
 */
const CLUSTER_RELATIVE_THRESHOLD = 0.9;
const CLUSTER_MIN_SIZE = 3;

interface HighestRiskSummary {
  kind: "file" | "cluster";
  files: SemanticFile[];
  threshold?: number;
}

function buildHighestRiskSummary(files: SemanticFile[]): HighestRiskSummary | null {
  if (files.length === 0) return null;
  let maxCrit = -Infinity;
  for (const f of files) maxCrit = Math.max(maxCrit, f.criticality);
  if (maxCrit <= 0) return null;

  const threshold = maxCrit * CLUSTER_RELATIVE_THRESHOLD;
  const near = files.filter((f) => f.criticality >= threshold);
  near.sort((a, b) => b.criticality - a.criticality || a.path.localeCompare(b.path));

  if (near.length >= CLUSTER_MIN_SIZE) {
    return { kind: "cluster", files: near, threshold };
  }
  return { kind: "file", files: [near[0]] };
}

// --------------------------------------------------------------- risk spine

/**
 * DENSITY MODES. One visual grammar — six role lanes, criticality on x,
 * fan-in as mark size, churn as fill — rendered with the mark that stays
 * legible at the repository's actual size. The encoding never changes
 * between modes; only the mark does, so a repository that grows past a
 * threshold does not change what any axis means.
 *
 *   atlas (<= 80 files)  one row per file. Collisions are impossible by
 *                        construction and every file keeps its name.
 *   swarm (<= 800)       beeswarm packing inside the lane; only the
 *                        highest-stakes files keep labels.
 *   ridge (> 800)        per-lane density ribbon tinted by mean churn, with
 *                        the outliers lifted into a reserved band above it
 *                        so the danger corner never dissolves into the mass.
 *
 * DOM cost is bounded in ridge mode (bins per lane, not files), which is what
 * keeps a 5,000-file repository renderable.
 */
type SpineMode = "atlas" | "swarm" | "ridge";

const ATLAS_MAX = 80;
const SWARM_MAX = 800;

function spineModeFor(totalFiles: number): SpineMode {
  if (totalFiles <= ATLAS_MAX) return "atlas";
  if (totalFiles <= SWARM_MAX) return "swarm";
  return "ridge";
}

/**
 * Churn is a magnitude, so it gets a single-hue sequential ramp — never a
 * categorical palette. Role is already carried by the lane (a positional
 * channel), which is what frees color to encode churn without the two
 * fighting. Both step sets are validated against the surface they render on
 * (monotone lightness, adjacent ΔL >= 0.06, surface-nearest step >= 2:1
 * contrast), and are declared as scoped custom properties on the spine root
 * so the light/dark swap happens in CSS rather than in a re-render.
 */
const CHURN_RAMP_VARS =
  "[--spine-c0:#ADA6DF] [--spine-c1:#9187D0] [--spine-c2:#7568C1] " +
  "[--spine-c3:#5A4EA8] [--spine-c4:#42388D] [--spine-c5:#2C2472] " +
  "dark:[--spine-c0:#514992] dark:[--spine-c1:#655AB3] dark:[--spine-c2:#7B6FCD] " +
  "dark:[--spine-c3:#9287E0] dark:[--spine-c4:#A99FEC] dark:[--spine-c5:#C2BAF7]";

const CHURN_STEPS = 6;

function churnFill(churn: number): string {
  const i = Math.min(CHURN_STEPS - 1, Math.max(0, Math.floor(clamp01(churn) * CHURN_STEPS)));
  return `var(--spine-c${i})`;
}

/**
 * The single reserved accent, spent only on the danger corner — the region
 * where high criticality meets a harmful role. Everything else on the spine
 * stays inside the churn ramp, so this is the one thing that can draw the
 * eye without competing.
 */
const DANGER_FROM = 0.7;

/**
 * A peak's own fill color is risk severity, not architectural role — role is
 * already carried by the lane, so doubling it onto every mark would be
 * redundant. Retained for the risk callout, which sits outside the spine and
 * needs a status color rather than a churn step.
 */
function riskTierColor(criticality: number, churn: number): string {
  const c = clamp01(criticality);
  const highCrit = c >= HOT_THRESHOLD;
  const highChurn = clamp01(churn) >= HOT_THRESHOLD;
  if (highCrit && highChurn) return "var(--status-failed)";
  if (highCrit || highChurn) return "var(--status-retry)";
  if (c >= 0.4) return "var(--primary)";
  return "var(--ink-soft)";
}

/** How much harm a role can do when it breaks — the lane order's own
 * rationale, reused as the opacity of the harm rail so the ordering is
 * visible rather than merely asserted. Display only; never a data value. */
const ROLE_HARM: Record<SemanticRole, number> = {
  "auth-boundary": 1,
  "public-api": 0.82,
  "data-access": 0.66,
  "config-surface": 0.5,
  "internal-util": 0.3,
  "test-only": 0.1,
};

/** Rank used to decide which files earn a label when not all of them can:
 * criticality weighted by how many callers a break would reach. */
function stakeOf(f: SemanticFile): number {
  return clamp01(f.criticality) * Math.log(1 + f.importedBy.length);
}

/** Truncate a real path for a label without ever inventing one. Keeps the
 * filename, which is the part that identifies it. */
function shortPath(p: string, max: number): string {
  if (p.length <= max) return p;
  const tail = fileName(p);
  if (tail.length >= max - 2) return `…${tail.slice(-(max - 1))}`;
  return `…/${tail}`;
}

/**
 * Label placement: every mark claims its own footprint before any label is
 * drawn, and a label that cannot find clean space is dropped rather than
 * overlapped. A missing label is always better than two unreadable ones —
 * the mark is still there, still hoverable, still clickable.
 */
class Claims {
  private rects: { x: number; y: number; w: number; h: number }[] = [];

  free(r: { x: number; y: number; w: number; h: number }): boolean {
    for (const o of this.rects) {
      if (r.x < o.x + o.w && r.x + r.w > o.x && r.y < o.y + o.h && r.y + r.h > o.y) return false;
    }
    return true;
  }

  take(r: { x: number; y: number; w: number; h: number }): void {
    this.rects.push(r);
  }
}

// ------------------------------------------------------------------ geometry

const GUTTER_PX = 148;
const GUTTER_NARROW_PX = 104;
const PAD_TOP = 22;
const PAD_RIGHT = 18;
const AXIS_H = 30;
const ATLAS_ROW = 15;
const RIDGE_BAND = 34;
const RIDGE_BINS = 56;
/** No single role may claim more than this share of the plot, however many
 * files land in it — otherwise `internal-util` eats the panel on every
 * real repository. */
const LANE_SHARE_CAP = 0.26;
/** Height past which the spine scrolls internally instead of growing. */
const SPINE_MAX_H = 620;

interface LaneLayout {
  role: SemanticRole;
  files: SemanticFile[];
  top: number;
  height: number;
}

function layoutLanes(byRole: Map<SemanticRole, SemanticFile[]>, mode: SpineMode): LaneLayout[] {
  const counts = LANE_ORDER.map((r) => byRole.get(r)?.length ?? 0);

  let heights: number[];
  if (mode === "atlas") {
    // One row per file. Empty roles collapse to a compact row instead of an
    // empty rectangle, so a four-file repository occupies four small regions.
    heights = counts.map((n) => (n === 0 ? 26 : Math.max(34, n * ATLAS_ROW + 12)));
  } else {
    const weights = counts.map((n) => Math.sqrt(Math.max(1, n)));
    const sum = weights.reduce((a, b) => a + b, 0) || 1;
    const capped = weights.map((w) => Math.min(w / sum, LANE_SHARE_CAP));
    const cSum = capped.reduce((a, b) => a + b, 0) || 1;
    const body = mode === "ridge" ? 520 : 460;
    const min = mode === "ridge" ? 78 : 44;
    heights = capped.map((w, i) => (counts[i] === 0 ? 26 : Math.max(min, (w / cSum) * body)));
  }

  let y = PAD_TOP;
  return LANE_ORDER.map((role, i) => {
    const lane: LaneLayout = {
      role,
      files: byRole.get(role) ?? [],
      top: y,
      height: heights[i],
    };
    y += heights[i];
    return lane;
  });
}

function markRadius(fanIn: number, mode: SpineMode): number {
  const base = mode === "atlas" ? 2.6 : 2.4;
  const k = mode === "atlas" ? 0.95 : mode === "swarm" ? 0.85 : 0.8;
  return Math.min(mode === "atlas" ? 6.5 : 9, base + Math.sqrt(Math.max(0, fanIn)) * k);
}

interface PlacedMark {
  file: SemanticFile;
  cx: number;
  cy: number;
  r: number;
  /** Ridge outliers drop a leader line down to the ribbon they came from. */
  leaderTo?: number;
}

/** A ridge bin, kept addressable so clicking a dense region can drill into
 * the real files it aggregates rather than a synthetic summary. */
interface RidgeRegion {
  role: SemanticRole;
  x: number;
  width: number;
  top: number;
  height: number;
  files: SemanticFile[];
  meanChurn: number;
  /** The bin's real criticality bounds — what the region actually covers, so
   * its label states a measured range rather than a rounded midpoint. */
  critLow: number;
  critHigh: number;
}

// -------------------------------------------------------------------- legend

/** What each mode does, in the panel's own words. Shown on the header chip so
 * the reader always knows which rendering they are looking at and why. */
const MODE_NOTE: Record<SpineMode, string> = {
  atlas: "every file named",
  swarm: "packed, top files named",
  ridge: "density + outliers",
};

/**
 * Density override. `Auto` picks the mode from the repository's own file
 * count — the right default — but a reader comparing repositories, or one who
 * wants the named inventory on a larger repository, can pin a mode. The
 * override changes only which mark is drawn; every value stays the same.
 */
function DensityControl({
  value,
  resolved,
  onChange,
}: {
  value: SpineMode | null;
  resolved: SpineMode;
  onChange: (m: SpineMode | null) => void;
}) {
  const options: { key: SpineMode | null; label: string }[] = [
    { key: null, label: "Auto" },
    { key: "atlas", label: "Atlas" },
    { key: "swarm", label: "Swarm" },
    { key: "ridge", label: "Ridge" },
  ];
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[9px] font-medium uppercase tracking-wider text-ink-soft/60">
        Density
      </span>
      <div
        role="group"
        aria-label="Density mode"
        className="inline-flex gap-0.5 rounded-md border border-border bg-surface-muted/40 p-0.5"
      >
        {options.map((o) => {
          const active = o.key === value;
          return (
            <button
              key={o.label}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(o.key)}
              title={o.key === null ? `Auto — ${resolved} for this repository` : MODE_NOTE[o.key]}
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors ${
                active
                  ? "bg-surface text-ink shadow-sm"
                  : "text-ink-soft hover:bg-surface-muted hover:text-ink"
              }`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function RiskLegend({ mode }: { mode: SpineMode }) {
  const modeNote = MODE_NOTE[mode];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg border border-border/60 bg-surface-muted/30 px-2.5 py-1.5 text-[10px] text-ink-soft">
      <span className="flex items-center gap-1.5">
        <span className="font-medium uppercase tracking-wider text-ink-soft/70">Criticality</span>
        <span aria-hidden className="font-mono text-ink-soft/50">
          →
        </span>
        <span>left to right</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="font-medium uppercase tracking-wider text-ink-soft/70">Fan-in</span>
        <svg width="30" height="11" aria-hidden className="shrink-0">
          <circle cx="4" cy="7" r="2" fill="currentColor" opacity="0.55" />
          <circle cx="13" cy="6.5" r="3.5" fill="currentColor" opacity="0.55" />
          <circle cx="24" cy="6" r="5" fill="currentColor" opacity="0.55" />
        </svg>
        <span>mark size</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="font-medium uppercase tracking-wider text-ink-soft/70">Churn</span>
        <span aria-hidden className="flex gap-px">
          {Array.from({ length: CHURN_STEPS }, (_, i) => (
            <span
              key={i}
              className="block h-2.5 w-3 rounded-[1px]"
              style={{ background: `var(--spine-c${i})` }}
            />
          ))}
        </span>
        <span>fill</span>
      </span>
      <span className="flex items-center gap-1.5 text-ink-soft/70">
        <span className="font-medium uppercase tracking-wider">Density</span>
        <span className="font-mono">{modeNote}</span>
      </span>
    </div>
  );
}

// ------------------------------------------------------------------- tooltip

interface SpineHover {
  file: SemanticFile;
  x: number;
  y: number;
}

function SpineTooltip({ hover, maxFanIn }: { hover: SpineHover; maxFanIn: number }) {
  const { file } = hover;
  const flip = hover.x > 260;
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-20 w-[210px] rounded-lg border border-border bg-surface p-2 shadow-lg"
      style={{
        left: flip ? undefined : hover.x + 12,
        right: flip ? 8 : undefined,
        top: Math.max(0, hover.y - 8),
      }}
    >
      <div className="truncate font-mono text-[10px] font-semibold text-ink" title={file.path}>
        {file.path}
      </div>
      <div className="mt-1 space-y-0.5 text-[10px] text-ink-soft">
        <div className="flex justify-between gap-3">
          <span>Role</span>
          <span className="font-mono text-ink">{ROLE_LABEL[file.role]}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span>Criticality</span>
          <span className="font-mono text-ink">{file.criticality.toFixed(2)}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span>Fan-in</span>
          <span className="font-mono text-ink">
            {file.importedBy.length}
            {maxFanIn > 0 && file.importedBy.length === maxFanIn ? " (highest)" : ""}
          </span>
        </div>
        <div className="flex justify-between gap-3">
          <span>Churn</span>
          <span className="font-mono text-ink">{file.churnWeight.toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- risk spine

/**
 * The Repository Intelligence live view. A deterministic, sorted layout —
 * never force-directed and never simulated — so the same repository renders
 * identically twice and the panel can be compared run over run.
 *
 * Everything drawn traces to a real field on `SemanticFile`. Where a signal
 * is genuinely unavailable from A1's contract it is simply not encoded; no
 * channel is filled with a placeholder.
 */
function RiskSpine({
  files,
  maxFanIn,
  selectedPath,
  highlightPath,
  neighbors,
  onSelectFile,
  onSelectRegion,
  mode,
}: {
  files: SemanticFile[];
  maxFanIn: number;
  selectedPath: string | null;
  /** The highest-risk file, ringed in place so the callout and the spine
   * point at the same mark instead of naming it twice. */
  highlightPath?: string;
  /** One-hop neighbours of the selection — kept lit while everything else
   * fades, so the dependency question is answered without drawing the whole
   * repository as an edge graph. */
  neighbors: Set<string>;
  onSelectFile: (path: string) => void;
  onSelectRegion: (files: SemanticFile[]) => void;
  /** Resolved density mode. Owned by the panel so the header chip, the
   * legend and the plot can never disagree about which mode is showing. */
  mode: SpineMode;
}) {
  const clipBase = `spine-ridge-${useId().replace(/:/g, "")}`;
  const plotRef = useRef<HTMLDivElement | null>(null);
  const [plotW, setPlotW] = useState(640);
  const [hover, setHover] = useState<SpineHover | null>(null);

  useEffect(() => {
    const el = plotRef.current;
    if (!el) return;
    // jsdom reports 0 — fall back to a sane width so tests exercise the same
    // layout code the browser runs.
    const measure = () => setPlotW(Math.max(320, el.clientWidth || 640));
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const byRole = useMemo(() => {
    const m = new Map<SemanticRole, SemanticFile[]>();
    for (const r of LANE_ORDER) m.set(r, []);
    for (const f of files) m.get(f.role)?.push(f);
    return m;
  }, [files]);

  const lanes = useMemo(() => layoutLanes(byRole, mode), [byRole, mode]);

  const totalH = lanes.reduce((sum, l) => sum + l.height, 0) + PAD_TOP + AXIS_H;
  const innerW = Math.max(200, plotW - PAD_RIGHT);
  const xOf = useCallback((c: number) => clamp01(c) * innerW, [innerW]);

  /** Marks, labels and ridge regions for the whole plot. Computed in one
   * pass so label placement can see every mark in every lane before it
   * decides what fits. */
  const scene = useMemo(() => {
    const claims = new Claims();
    const marks: PlacedMark[] = [];
    const regions: RidgeRegion[] = [];
    const silhouettes: { role: SemanticRole; d: string }[] = [];
    const laneMarks: { lane: LaneLayout; placed: PlacedMark[] }[] = [];

    for (const lane of lanes) {
      const placed: PlacedMark[] = [];

      if (lane.files.length === 0) {
        laneMarks.push({ lane, placed });
        continue;
      }

      if (mode === "atlas") {
        // Most critical at the top of its own lane — the descending order is
        // itself readable, and no two marks can ever share a row.
        [...lane.files]
          .sort((a, b) => b.criticality - a.criticality || a.path.localeCompare(b.path))
          .forEach((f, i) => {
            placed.push({
              file: f,
              cx: xOf(f.criticality),
              cy: lane.top + 10 + i * ATLAS_ROW,
              r: markRadius(f.importedBy.length, mode),
            });
          });
      } else if (mode === "ridge") {
        const ribbonTop = lane.top + RIDGE_BAND;
        const ribbonH = lane.height - RIDGE_BAND;
        const binW = innerW / RIDGE_BINS;
        const bins: SemanticFile[][] = Array.from({ length: RIDGE_BINS }, () => []);
        for (const f of lane.files) {
          const b = Math.min(RIDGE_BINS - 1, Math.floor(clamp01(f.criticality) * RIDGE_BINS));
          bins[b].push(f);
        }
        // Weight by fan-in so the ribbon shows exposure, not just headcount.
        // Deliberately NOT smoothed across neighbouring bins: a smoothed bar
        // shows a height that is partly its neighbours' mass, which is a
        // number the repository never produced. Each bar is its own bin.
        const weight = bins.map((b) =>
          b.reduce((s, f) => s + 1 + Math.sqrt(f.importedBy.length) * 0.35, 0),
        );
        const peak = Math.max(...weight, 1);

        const base = ribbonTop + ribbonH - 4;
        const amp = ribbonH - 8;
        const heightAt = (i: number) => (weight[i] / peak) * amp;

        // The silhouette connects the real bin values — straight segments
        // between measured points, the way any area chart joins its samples.
        // It is an outline, not a smoothing: no bin's own height is altered
        // by its neighbours.
        let d = `M 0 ${base.toFixed(1)}`;
        for (let i = 0; i < RIDGE_BINS; i++) {
          d += ` L ${((i + 0.5) * binW).toFixed(1)} ${(base - heightAt(i)).toFixed(1)}`;
        }
        d += ` L ${innerW.toFixed(1)} ${base.toFixed(1)} Z`;
        silhouettes.push({ role: lane.role, d });

        bins.forEach((b, i) => {
          if (b.length === 0) return;
          const churnSum = b.reduce((s, f) => s + clamp01(f.churnWeight), 0);
          regions.push({
            role: lane.role,
            x: i * binW,
            // Columns are clipped to the silhouette, so each spans the full
            // lane height and the outline decides what shows.
            width: binW + 0.6,
            top: ribbonTop,
            height: ribbonH,
            files: b,
            meanChurn: churnSum / b.length,
            critLow: i / RIDGE_BINS,
            critHigh: (i + 1) / RIDGE_BINS,
          });
        });

        // Outliers are lifted into a reserved band above the ribbon so they
        // are never buried inside the mass they belong to. Selection is
        // *relative to the lane* — the top few by stake — never an absolute
        // criticality or fan-in gate. An absolute gate silently draws nothing
        // on a repository whose fan-in never reaches it, which would let the
        // danger corner dissolve exactly where this mode has to hold it.
        const cap = ROLE_HARM[lane.role] > 0.6 ? 6 : 3;
        const ranked = [...lane.files].sort(
          (a, b) => stakeOf(b) - stakeOf(a) || b.criticality - a.criticality,
        );
        const chosen = ranked.slice(0, cap);
        // The risk callout ranks by criticality alone, this band by stake —
        // so the highest-risk file can rank outside the cap. Force it in:
        // the callout naming a file the spine never draws would point the
        // two at different things.
        const pinned = highlightPath ? lane.files.find((f) => f.path === highlightPath) : undefined;
        if (pinned && !chosen.includes(pinned)) {
          chosen[chosen.length - 1] = pinned;
        }
        const outliers = chosen.sort((a, b) => a.criticality - b.criticality);

        let lastX = -Infinity;
        const run: PlacedMark[] = [];
        for (const f of outliers) {
          const r = markRadius(f.importedBy.length, mode);
          const cx = Math.max(xOf(f.criticality), lastX + r * 2 + 3);
          lastX = cx;
          run.push({
            file: f,
            cx,
            r,
            cy: lane.top + RIDGE_BAND - 12,
            leaderTo: lane.top + RIDGE_BAND - 1,
          });
        }
        const last = run[run.length - 1];
        const overflow = last ? last.cx + last.r - (innerW - 2) : 0;
        for (const m of run) placed.push(overflow > 0 ? { ...m, cx: m.cx - overflow } : m);
      } else {
        // Swarm: pack vertically inside the lane so no mark hides another.
        const sorted = [...lane.files].sort(
          (a, b) => a.criticality - b.criticality || a.path.localeCompare(b.path),
        );
        for (const f of sorted) {
          const r = markRadius(f.importedBy.length, mode);
          const cx = xOf(f.criticality);
          const mid = lane.top + lane.height / 2;
          const limit = lane.height / 2 - r - 3;
          let cy = mid;
          for (let step = 0; step <= 60; step++) {
            const off = step === 0 ? 0 : Math.ceil(step / 2) * (r * 1.55) * (step % 2 ? 1 : -1);
            if (Math.abs(off) > limit) continue;
            const ty = mid + off;
            let ok = true;
            for (const p of placed) {
              const dx = p.cx - cx;
              const dy = p.cy - ty;
              if (dx * dx + dy * dy < (p.r + r + 1.2) ** 2) {
                ok = false;
                break;
              }
            }
            if (ok) {
              cy = ty;
              break;
            }
          }
          placed.push({ file: f, cx, cy, r });
        }
      }

      marks.push(...placed);
      laneMarks.push({ lane, placed });
    }

    // Every mark reserves its footprint before any label is drawn, so a label
    // can never land on a dot — including a dot in a different lane.
    for (const m of marks) {
      claims.take({ x: m.cx - m.r - 1, y: m.cy - m.r - 1, w: (m.r + 1) * 2, h: (m.r + 1) * 2 });
    }

    const fs = mode === "atlas" ? 9.5 : 9;
    type Anchor = "start" | "middle" | "end";
    const labels: { text: string; x: number; y: number; anchor: Anchor; path: string }[] = [];

    for (const { lane, placed } of laneMarks) {
      let candidates = [...placed].sort((a, b) => stakeOf(b.file) - stakeOf(a.file));
      if (mode === "swarm") {
        candidates = candidates.slice(0, ROLE_HARM[lane.role] > 0.6 ? 4 : 2);
      } else if (mode === "ridge") {
        candidates = candidates.slice(0, ROLE_HARM[lane.role] > 0.6 ? 4 : 2);
      }

      for (const m of candidates) {
        const text = shortPath(m.file.path, mode === "atlas" ? 38 : 24);
        const w = text.length * fs * 0.605;
        const gap = m.r + 6;

        // Try the side first, then above/below. Without the vertical
        // fallbacks the densest lanes — which are the high-harm ones this
        // panel exists to surface — lose every label to the side being
        // blocked, while sparse low-harm lanes keep theirs.
        const options: { x: number; boxX: number; y: number; anchor: Anchor }[] = [
          { x: m.cx + gap, boxX: m.cx + gap, y: m.cy + 3.2, anchor: "start" },
          { x: m.cx - gap, boxX: m.cx - gap - w, y: m.cy + 3.2, anchor: "end" },
          { x: m.cx, boxX: m.cx - w / 2, y: m.cy - m.r - 4, anchor: "middle" },
          { x: m.cx, boxX: m.cx - w / 2, y: m.cy + m.r + fs + 1, anchor: "middle" },
        ];
        // Ridge outliers sit in a reserved band, so above is their natural
        // first choice and below would collide with the ribbon.
        if (mode === "ridge") options.reverse();

        for (const o of options) {
          if (o.boxX < 0 || o.boxX + w > innerW) continue;
          if (o.y - fs < lane.top || o.y > lane.top + lane.height) continue;
          const box = { x: o.boxX - 2, y: o.y - fs + 1, w: w + 4, h: fs + 3 };
          if (!claims.free(box)) continue;
          claims.take(box);
          labels.push({ text, x: o.x, y: o.y, anchor: o.anchor, path: m.file.path });
          break;
        }
      }
    }

    return { marks, labels, regions, silhouettes, fontSize: fs };
  }, [lanes, mode, innerW, highlightPath, xOf]);

  const dimmed = (path: string) =>
    selectedPath !== null && path !== selectedPath && !neighbors.has(path);

  const gutter = plotW < 420 ? GUTTER_NARROW_PX : GUTTER_PX;

  // Atlas names every file, so its height grows with the repository. Past a
  // ceiling that becomes a page nobody can read: bound it and let the gutter
  // and plot scroll together, rather than silently dropping files or letting
  // the panel run to thousands of pixels.
  const tall = totalH > SPINE_MAX_H;

  return (
    <div
      className={
        tall ? "overscroll-contain overflow-y-auto rounded-md border border-border/50" : undefined
      }
      style={tall ? { maxHeight: SPINE_MAX_H } : undefined}
    >
      <div className="flex items-stretch">
        {/* Lane gutter — the role vocabulary, in the panel's own type and icons. */}
        <div className="shrink-0" style={{ width: gutter }}>
          <div style={{ height: PAD_TOP }} />
          {lanes.map((lane) => {
            const Icon = ROLE_ICON[lane.role];
            const empty = lane.files.length === 0;
            return (
              <div
                key={lane.role}
                className="flex items-start gap-1.5 pr-2"
                style={{ height: lane.height, paddingTop: 4 }}
              >
                {/* Harm rail — why the lanes are in this order, shown rather
                  than asserted. Display only; never a data value. */}
                <span
                  aria-hidden
                  className="mt-0.5 block w-[3px] shrink-0 rounded-full bg-ink"
                  style={{
                    height: Math.max(8, lane.height - 10),
                    opacity: empty ? 0.06 : 0.12 + ROLE_HARM[lane.role] * 0.6,
                  }}
                />
                {/* A collapsed (zero-file) lane puts its name and count on one
                  line — two stacked lines do not fit its height, and would
                  overflow into the lane below. */}
                <span className={`min-w-0 flex-1 ${empty ? "flex items-baseline gap-1.5" : ""}`}>
                  <span
                    className={`flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider ${
                      empty ? "text-ink-soft/40" : "text-ink"
                    }`}
                  >
                    <Icon aria-hidden className="h-3 w-3 shrink-0" />
                    <span className="truncate">{ROLE_LABEL[lane.role]}</span>
                  </span>
                  <span
                    className={`font-mono text-[9px] ${
                      empty ? "shrink-0 text-ink-soft/40" : "mt-0.5 block text-ink-soft"
                    }`}
                  >
                    {lane.files.length} file{lane.files.length === 1 ? "" : "s"}
                  </span>
                </span>
              </div>
            );
          })}
          <div style={{ height: AXIS_H }} />
        </div>

        {/* Plot */}
        <div ref={plotRef} className="relative min-w-0 flex-1">
          <svg
            width={plotW}
            height={totalH}
            viewBox={`0 0 ${plotW} ${totalH}`}
            role="img"
            aria-label={`Risk spine — ${files.length} files across six architectural roles, positioned by criticality`}
            className="block overflow-visible"
            onMouseLeave={() => setHover(null)}
          >
            {/* Danger corner: high criticality in the roles that can do harm. */}
            <rect
              x={xOf(DANGER_FROM)}
              y={PAD_TOP}
              width={innerW - xOf(DANGER_FROM)}
              height={lanes[0].height + lanes[1].height}
              fill="var(--status-retry)"
              opacity={0.06}
            />
            <text
              x={innerW}
              y={PAD_TOP - 7}
              textAnchor="end"
              fontSize={8.5}
              letterSpacing="0.12em"
              fill="var(--status-retry)"
              className="font-mono"
            >
              DANGER CORNER ▾
            </text>

            {/* Lane bands and rules */}
            {lanes.map((lane, i) => (
              <g key={lane.role}>
                <rect
                  x={0}
                  y={lane.top}
                  width={innerW}
                  height={lane.height}
                  fill="var(--ink)"
                  opacity={i % 2 === 0 ? 0.022 : 0}
                />
                <line
                  x1={0}
                  y1={lane.top}
                  x2={innerW}
                  y2={lane.top}
                  stroke="var(--border)"
                  strokeWidth={1}
                />
              </g>
            ))}
            <line
              x1={0}
              y1={PAD_TOP + lanes.reduce((s, l) => s + l.height, 0)}
              x2={innerW}
              y2={PAD_TOP + lanes.reduce((s, l) => s + l.height, 0)}
              stroke="var(--border)"
              strokeWidth={1}
            />

            {/* Criticality gridlines + axis */}
            {[0, 0.25, 0.5, 0.75, 1].map((v) => (
              <g key={v}>
                <line
                  x1={xOf(v)}
                  y1={PAD_TOP}
                  x2={xOf(v)}
                  y2={PAD_TOP + lanes.reduce((s, l) => s + l.height, 0)}
                  stroke="var(--border)"
                  strokeWidth={1}
                  strokeDasharray={v === 0 || v === 1 ? undefined : "2 4"}
                  opacity={v === 0 || v === 1 ? 1 : 0.7}
                />
                <text
                  x={xOf(v)}
                  y={PAD_TOP + lanes.reduce((s, l) => s + l.height, 0) + 13}
                  textAnchor={v === 1 ? "end" : v === 0 ? "start" : "middle"}
                  fontSize={9}
                  fill="var(--ink-soft)"
                  className="font-mono"
                >
                  {v.toFixed(2)}
                </text>
              </g>
            ))}
            <text
              x={0}
              y={PAD_TOP + lanes.reduce((s, l) => s + l.height, 0) + 26}
              fontSize={8.5}
              letterSpacing="0.11em"
              fill="var(--ink-soft)"
              className="font-mono"
              opacity={0.8}
            >
              CRITICALITY → HOW MUCH THE REPOSITORY LEANS ON IT
            </text>

            {/* Ridge ribbons — the lane's density silhouette, filled with one
              column per bin tinted by that bin's own mean churn. The columns
              are clipped to the silhouette, so the outline carries the shape
              and the fill carries the churn. Clickable: a region drills into
              the real files it holds. */}
            <defs>
              {scene.silhouettes.map((sil) => (
                <clipPath key={sil.role} id={`${clipBase}-${sil.role}`}>
                  <path d={sil.d} />
                </clipPath>
              ))}
            </defs>
            {scene.silhouettes.map((sil) => (
              <path
                key={sil.role}
                d={sil.d}
                fill="none"
                stroke={churnFill(0.9)}
                strokeWidth={1}
                opacity={0.5}
              />
            ))}
            {scene.regions.map((reg, i) => (
              <rect
                key={`${reg.role}-${i}`}
                x={reg.x}
                y={reg.top}
                width={reg.width}
                height={reg.height}
                fill={churnFill(reg.meanChurn)}
                opacity={0.85}
                clipPath={`url(#${clipBase}-${reg.role})`}
                className="cursor-pointer"
                role="button"
                tabIndex={0}
                aria-label={`${reg.files.length} file${
                  reg.files.length === 1 ? "" : "s"
                }, criticality ${reg.critLow.toFixed(2)}–${reg.critHigh.toFixed(2)}, ${
                  ROLE_LABEL[reg.role]
                }`}
                onClick={() => onSelectRegion(reg.files)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectRegion(reg.files);
                  }
                }}
              />
            ))}

            {/* Marks */}
            {scene.marks.map((m) => {
              const f = m.file;
              const isSelected = f.path === selectedPath;
              const isHighlight = f.path === highlightPath;
              return (
                <g
                  key={f.path}
                  opacity={dimmed(f.path) ? 0.13 : 1}
                  className="cursor-pointer"
                  role="button"
                  tabIndex={0}
                  aria-label={`${f.path} — criticality ${f.criticality.toFixed(
                    2,
                  )}, churn ${f.churnWeight.toFixed(2)}, imported by ${f.importedBy.length}`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelectFile(f.path);
                    }
                  }}
                  onMouseEnter={(e) => {
                    const box = plotRef.current?.getBoundingClientRect();
                    setHover({
                      file: f,
                      x: box ? e.clientX - box.left : m.cx,
                      y: box ? e.clientY - box.top : m.cy,
                    });
                  }}
                  onMouseLeave={() => setHover(null)}
                  onClick={() => {
                    // The detail panel now carries everything the tooltip said —
                    // leaving it up would state the same numbers twice.
                    setHover(null);
                    onSelectFile(f.path);
                  }}
                >
                  {m.leaderTo != null && (
                    <line
                      x1={m.cx}
                      y1={m.cy + m.r}
                      x2={m.cx}
                      y2={m.leaderTo}
                      stroke="var(--border)"
                      strokeWidth={1}
                    />
                  )}
                  <circle
                    cx={m.cx}
                    cy={m.cy}
                    r={m.r}
                    fill={churnFill(f.churnWeight)}
                    stroke="var(--surface)"
                    strokeWidth={1}
                  />
                  {isHighlight && !isSelected && (
                    <circle
                      cx={m.cx}
                      cy={m.cy}
                      r={m.r + 3.2}
                      fill="none"
                      stroke="var(--status-retry)"
                      strokeWidth={1.4}
                    />
                  )}
                  {isSelected && (
                    <circle
                      cx={m.cx}
                      cy={m.cy}
                      r={m.r + 4.5}
                      fill="none"
                      stroke="var(--ink)"
                      strokeWidth={1.5}
                    />
                  )}
                  <title>{`${f.path} — criticality ${f.criticality.toFixed(2)}, ${
                    f.importedBy.length
                  } dependent${f.importedBy.length === 1 ? "" : "s"}`}</title>
                </g>
              );
            })}

            {/* Direct labels */}
            {scene.labels.map((l) => (
              <text
                key={l.path}
                x={l.x}
                y={l.y}
                textAnchor={l.anchor}
                fontSize={scene.fontSize}
                fill="var(--ink-soft)"
                className="pointer-events-none font-mono"
                opacity={dimmed(l.path) ? 0.13 : 1}
              >
                {l.text}
              </text>
            ))}
          </svg>

          {hover && <SpineTooltip hover={hover} maxFanIn={maxFanIn} />}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- risk callout

interface RiskCalloutData {
  badge: string;
  label: string;
  files: SemanticFile[];
}

function buildRiskCallout(summary: HighestRiskSummary | null): RiskCalloutData | null {
  if (!summary) return null;
  if (summary.kind === "cluster") {
    return {
      badge: "Top risk files",
      label: `${summary.files.length} files · criticality ≥ ${summary.threshold!.toFixed(2)}`,
      files: summary.files,
    };
  }
  const [f] = summary.files;
  return {
    badge: "Highest risk",
    label: `${fileName(f.path)} · ${f.criticality.toFixed(2)}`,
    files: summary.files,
  };
}

/**
 * The single, prominent risk verdict directly below the terrain — the one
 * place a highest-risk file or cluster is named ("do not duplicate this
 * indicator elsewhere"). Clicking opens the file directly, or a compact
 * drill-down list for a cluster.
 */
function RiskCalloutBar({
  callout,
  onSelectFile,
  onSelectBucket,
}: {
  callout: RiskCalloutData | null;
  onSelectFile: (path: string) => void;
  onSelectBucket: (files: SemanticFile[]) => void;
}) {
  if (!callout) return null;
  const top = callout.files[0];
  const accent = riskTierColor(top.criticality, top.churnWeight);
  return (
    <button
      type="button"
      onClick={() =>
        callout.files.length === 1
          ? onSelectFile(callout.files[0].path)
          : onSelectBucket(callout.files)
      }
      aria-label={`${callout.badge}: ${callout.label}`}
      className="inline-flex w-fit items-center gap-2.5 rounded-lg border bg-surface-muted/40 py-2 pl-2.5 pr-3 text-ink shadow-sm transition-colors hover:bg-surface-muted/70"
      style={{
        borderColor: `color-mix(in srgb, ${accent} 45%, var(--border))`,
        boxShadow: `0 0 0 1px transparent, 0 4px 14px -8px color-mix(in srgb, ${accent} 55%, transparent)`,
      }}
    >
      <TriangleAlert
        aria-hidden
        className="h-4 w-4 shrink-0"
        style={{ color: accent }}
        strokeWidth={2.25}
      />
      <span className="flex flex-col items-start leading-tight">
        <span
          className="text-[9px] font-semibold uppercase tracking-wider"
          style={{ color: accent }}
        >
          {callout.badge}
        </span>
        <span className="font-mono text-[13px] font-semibold">{callout.label}</span>
      </span>
    </button>
  );
}

// ---------------------------------------------------------------- drill-down

const FAN_IN_RELATIVE_HIGH = 0.6;

/** Rule-based, not generated: plain comparisons against the file's own
 * fields (and this repository's own fan-in range), never an LLM call. */
function whyItMatters(file: SemanticFile, maxFanIn: number): string {
  const highCrit = file.criticality >= HOT_THRESHOLD;
  const highChurn = file.churnWeight >= HOT_THRESHOLD;
  const highFanIn =
    maxFanIn > 0 &&
    file.importedBy.length > 1 &&
    file.importedBy.length / maxFanIn >= FAN_IN_RELATIVE_HIGH;

  if (highChurn && highCrit) {
    return "High churn and high criticality make this file a priority for investigation.";
  }
  if (highFanIn) {
    return "High fan-in means changes here can affect many callers.";
  }
  if (highCrit) {
    return "High criticality — A1 weighed this file's role and churn as structurally significant.";
  }
  if (highChurn) {
    return "Frequent recent changes make this file worth watching despite moderate criticality.";
  }
  return "Low fan-in and low criticality indicate limited repository impact.";
}

function PeerList({
  title,
  paths,
  onSelect,
}: {
  title: string;
  paths: string[];
  onSelect: (path: string) => void;
}) {
  const LIMIT = 8;
  return (
    <div className="min-w-0">
      <div className="mb-1.5 text-[9px] uppercase tracking-wider text-ink-soft">
        {title} ({paths.length})
      </div>
      {paths.length === 0 ? (
        <div className="text-[10px] text-ink-soft/60">None recorded.</div>
      ) : (
        <div className="space-y-1">
          {paths.slice(0, LIMIT).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => onSelect(p)}
              title={p}
              className="block w-full truncate rounded border border-border/60 bg-surface px-2 py-1 text-left font-mono text-[10px] text-ink-soft transition-colors hover:border-primary/40 hover:text-ink"
            >
              {fileName(p)}
            </button>
          ))}
          {paths.length > LIMIT && (
            <div className="text-[10px] text-ink-soft">+{paths.length - LIMIT} more</div>
          )}
        </div>
      )}
    </div>
  );
}

function FileDetail({
  file,
  maxFanIn,
  onBack,
  onSelect,
}: {
  file: SemanticFile;
  maxFanIn: number;
  onBack: () => void;
  onSelect: (path: string) => void;
}) {
  const Icon = ROLE_ICON[file.role];
  return (
    <div className="rounded-lg border border-border bg-surface-muted/20 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="min-w-0 text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          Selected file — one-hop relationships
        </div>
        <button
          type="button"
          onClick={onBack}
          className="shrink-0 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          ← Back to risk terrain
        </button>
      </div>

      <div className="grid grid-cols-1 items-center gap-3 lg:grid-cols-[minmax(0,1fr)_auto_220px_auto_minmax(0,1fr)]">
        <PeerList title="Imported by" paths={file.importedBy} onSelect={onSelect} />

        <ArrowDown aria-hidden className="h-4 w-4 justify-self-center text-ink-soft/40 lg:hidden" />
        <ArrowRight aria-hidden className="hidden h-4 w-4 text-ink-soft/40 lg:block" />

        <div
          className="rounded-lg border-2 bg-surface p-3 text-center"
          style={{ borderColor: ROLE_COLOR[file.role] }}
        >
          <Icon className="mx-auto h-4 w-4" style={{ color: ROLE_COLOR[file.role] }} aria-hidden />
          <div
            className="mt-1 truncate font-mono text-[11px] font-semibold text-ink"
            title={file.path}
          >
            {fileName(file.path)}
          </div>
          <div className="truncate text-[9px] text-ink-soft" title={file.path}>
            {file.path}
          </div>
          <div
            className="mt-1 text-[9px] font-semibold uppercase tracking-wider"
            style={{ color: ROLE_COLOR[file.role] }}
          >
            {ROLE_LABEL[file.role]}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            <div className="rounded-md border border-border bg-surface-muted/60 px-2 py-1">
              <div className="text-[9px] text-ink-soft">Criticality</div>
              <div className="font-mono text-[12px] font-semibold text-ink">
                {file.criticality.toFixed(2)}
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface-muted/60 px-2 py-1">
              <div className="text-[9px] text-ink-soft">Churn</div>
              <div className="font-mono text-[12px] font-semibold text-ink">
                {file.churnWeight.toFixed(2)}
              </div>
            </div>
          </div>
          <p className="mt-2 text-[9px] leading-snug text-ink-soft">
            {whyItMatters(file, maxFanIn)}
          </p>
        </div>

        <ArrowDown aria-hidden className="h-4 w-4 justify-self-center text-ink-soft/40 lg:hidden" />
        <ArrowRight aria-hidden className="hidden h-4 w-4 text-ink-soft/40 lg:block" />

        <PeerList title="Imports" paths={file.imports} onSelect={onSelect} />
      </div>
    </div>
  );
}

/** A bucket (or the cross-lane top-risk cluster) drilled down into its real
 * member files, sorted by criticality — never the full repository graph. */
function BucketDetail({
  files,
  onBack,
  onSelectFile,
}: {
  files: SemanticFile[];
  onBack: () => void;
  onSelectFile: (path: string) => void;
}) {
  const sorted = useMemo(
    () => [...files].sort((a, b) => b.criticality - a.criticality || a.path.localeCompare(b.path)),
    [files],
  );
  return (
    <div className="rounded-lg border border-border bg-surface-muted/20 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="min-w-0 text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          {files.length} files, ranked by criticality
        </div>
        <button
          type="button"
          onClick={onBack}
          className="shrink-0 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          ← Back to risk terrain
        </button>
      </div>
      <div className="max-h-56 space-y-1 overflow-y-auto">
        {sorted.map((f) => (
          <button
            key={f.path}
            type="button"
            onClick={() => onSelectFile(f.path)}
            title={f.path}
            className="flex w-full items-center gap-2 rounded border border-border/60 bg-surface px-2 py-1 text-left transition-colors hover:border-primary/40"
          >
            <span
              aria-hidden
              className="h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: ROLE_COLOR[f.role] }}
            />
            <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-ink">
              {fileName(f.path)}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-ink-soft">
              {f.criticality.toFixed(2)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- provenance

/**
 * What is actually known about how this graph was produced: real counts
 * from the fetched payload. A1's cache/LLM-classification telemetry
 * (`a1_metrics`) is logged and broadcast as a status-event payload but is
 * not part of the semantic-graph contract this panel reads, so it is not
 * shown here rather than being guessed at.
 */
function ProvenanceStrip({ graph }: { graph: SemanticGraphExport }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] text-ink-soft/80">
      <span className="font-medium uppercase tracking-wider text-ink-soft/60">Provenance</span>
      <span className="font-mono text-ink-soft">{graph.totalFiles}</span>
      <span>files</span>
      <span className="text-ink-soft/40">·</span>
      <span className="font-mono text-ink-soft">{graph.totalEdges}</span>
      <span>import edges</span>
      <span className="text-ink-soft/40">·</span>
      <span className="font-mono text-ink-soft">{graph.sourceRoots.length}</span>
      <span>source root{graph.sourceRoots.length === 1 ? "" : "s"}</span>
    </div>
  );
}

/**
 * "Find file or module…" — essential once a repository is too large to scan
 * by eye. Matching is a plain substring test against real paths (no fuzzy
 * scoring to misrepresent as smarter than it is); a match opens that file's
 * Level-3 detail directly, which already carries its architectural region,
 * criticality, and dependencies.
 */
function FindInput({
  value,
  onChange,
  onSubmit,
  notFound,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  notFound: boolean;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor="semantic-terrain-search" className="sr-only">
        Find file or module
      </label>
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-ink-soft/60"
        />
        <input
          id="semantic-terrain-search"
          type="search"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSubmit();
          }}
          placeholder="Find file or module…"
          className="w-40 rounded-md border border-border bg-surface-muted/40 py-1 pl-6 pr-2 text-[10px] text-ink outline-none placeholder:text-ink-soft/60 focus:border-primary/50 sm:w-56"
        />
      </div>
      <button
        type="button"
        onClick={onSubmit}
        className="shrink-0 rounded-md border border-border px-2 py-1 text-[10px] font-medium text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
      >
        Focus
      </button>
      {notFound && <span className="text-[9px] text-status-failed">No match</span>}
    </div>
  );
}

function HowToRead() {
  return (
    <details className="group text-[10px] text-ink-soft">
      <summary className="cursor-pointer select-none list-none font-medium text-ink-soft/70 [&::-webkit-details-marker]:hidden">
        How to read this{" "}
        <span className="inline-block transition-transform group-open:rotate-180">⌄</span>
      </summary>
      <p className="mt-1 leading-snug">
        Row = A1&apos;s architectural role, ordered by how much harm it can do. Left to right =
        criticality. Mark size = fan-in (files that depend on it). Fill = churn (how often it
        changes). So the shaded upper-right corner — critical, widely depended on, frequently
        changed, in a role that matters — is where a bug hurts most. Click a mark to ring it and see
        only its one-hop imports and dependents; import edges are never all drawn at once. In large
        repositories each lane becomes a density ribbon tinted by average churn, with the outliers
        still drawn and named individually — click a ribbon to list the files inside it. Source:{" "}
        <code className="font-mono">GET /api/runs/{"{run_id}"}/semantic-graph</code>
      </p>
    </details>
  );
}

// ------------------------------------------------------------------- panel

export function SemanticArchitecturePanel({
  runId,
  status,
}: {
  runId: string;
  /** The agent's live status — refetches once A1 transitions to a settled state. */
  status?: AgentStatus;
}) {
  const [graph, setGraph] = useState<SemanticGraphExport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [selectedBucket, setSelectedBucket] = useState<SemanticFile[] | null>(null);
  const [search, setSearch] = useState("");
  /** null = follow the repository's file count. */
  const [densityOverride, setDensityOverride] = useState<SpineMode | null>(null);
  const [searchError, setSearchError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getSemanticGraph(runId)
      .then((g) => {
        if (!cancelled) setGraph(g);
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
    // Refetch when the agent settles (running -> completed/failed) so a panel
    // mounted while A1 was still executing picks up the graph without a
    // manual retry, without polling while nothing has changed.
  }, [runId, attempt, status]);

  const maxFanIn = useMemo(() => {
    if (!graph) return 1;
    let max = 1;
    for (const f of graph.files) max = Math.max(max, f.importedBy.length);
    return max;
  }, [graph]);

  /** One-hop neighbours of the selected file, from its own real `imports` /
   * `importedBy` — the set the spine keeps lit while everything else fades.
   * Empty when nothing is selected, so the spine renders undimmed. */
  const selectionNeighbors = useMemo(() => {
    const set = new Set<string>();
    if (!graph || !selectedPath) return set;
    const f = graph.files.find((x) => x.path === selectedPath);
    if (!f) return set;
    for (const p of f.imports) set.add(p);
    for (const p of f.importedBy) set.add(p);
    return set;
  }, [graph, selectedPath]);

  const hotCount = useMemo(
    () =>
      graph
        ? graph.files.filter(
            (f) => f.criticality >= HOT_THRESHOLD && f.churnWeight >= HOT_THRESHOLD,
          ).length
        : 0,
    [graph],
  );

  const highestRisk = useMemo(() => (graph ? buildHighestRiskSummary(graph.files) : null), [graph]);
  const callout = useMemo(() => buildRiskCallout(highestRisk), [highestRisk]);
  const highlightFile =
    highestRisk?.kind === "file" && highestRisk.files.length === 1
      ? highestRisk.files[0]
      : undefined;

  const autoMode = spineModeFor(graph?.files.length ?? 0);
  const spineMode = densityOverride ?? autoMode;

  const selectedFile = graph?.files.find((f) => f.path === selectedPath) ?? null;
  const generatedInsight = graph ? buildGeneratedInsight(graph, hotCount) : "";
  const moduleCount = useMemo(
    () => (graph ? new Set(graph.files.map((f) => moduleOf(f.path))).size : 0),
    [graph],
  );
  const roleCount = useMemo(
    () => (graph ? LANE_ORDER.filter((r) => (graph.roleCounts[r] ?? 0) > 0).length : 0),
    [graph],
  );

  const selectFile = (path: string) => {
    setSelectedBucket(null);
    setSelectedPath(path);
  };
  const selectBucket = (files: SemanticFile[]) => {
    setSelectedPath(null);
    setSelectedBucket(files);
  };
  const backToTerrain = () => {
    setSelectedPath(null);
    setSelectedBucket(null);
  };

  /** Substring match against real paths only — the first match by path,
   * deterministic tiebreak. Finds the file and opens the same Level-3 file
   * inspection any terrain click opens; there is no separate "focus" view
   * to keep in sync. */
  const runSearch = () => {
    const q = search.trim().toLowerCase();
    if (!q || !graph) return;
    const match = [...graph.files]
      .sort((a, b) => a.path.localeCompare(b.path))
      .find((f) => f.path.toLowerCase().includes(q));
    if (match) {
      setSearchError(false);
      selectFile(match.path);
    } else {
      setSearchError(true);
    }
  };

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Semantic Risk Terrain
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Loading…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!graph) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Semantic Risk Terrain
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "Semantic mapping — running. A1 is classifying files now; this panel renders once it publishes."
            : "Pending — A1 has not published a semantic graph for this run yet."}
        </p>
      </section>
    );
  }

  if (graph.files.length === 0) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Semantic Risk Terrain
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          A1 completed but classified no production files in this repository.
        </p>
      </section>
    );
  }

  return (
    <section
      className={`${CHURN_RAMP_VARS} space-y-3 rounded-2xl border border-border bg-surface p-4 motion-safe:animate-fade-in`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            Semantic Risk Terrain
          </h3>
          <p className="mt-1 text-[12px] font-medium leading-snug text-ink">{generatedInsight}</p>
          <div className="mt-1 font-mono text-[9px] text-ink-soft/60">
            {graph.totalFiles} file{graph.totalFiles === 1 ? "" : "s"}
            {moduleCount > 1 && (
              <>
                {" "}
                · {moduleCount} module{moduleCount === 1 ? "" : "s"}
              </>
            )}{" "}
            · {roleCount} role{roleCount === 1 ? "" : "s"}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="rounded-full border border-border bg-surface-muted/50 px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider text-ink-soft">
            {spineMode} — {MODE_NOTE[spineMode]}
            {densityOverride === null ? " · auto" : ""}
          </span>
          <DensityControl
            value={densityOverride}
            resolved={autoMode}
            onChange={setDensityOverride}
          />
        </div>
        {graph.files.length > 20 && (
          <FindInput
            value={search}
            onChange={(v) => {
              setSearch(v);
              setSearchError(false);
            }}
            onSubmit={runSearch}
            notFound={searchError}
          />
        )}
      </div>

      <RiskLegend mode={spineMode} />

      {/* The spine stays on screen while a file is inspected — selecting a
          file focuses it in place rather than replacing the repository
          overview with a detail view. */}
      <RiskSpine
        files={graph.files}
        maxFanIn={maxFanIn}
        selectedPath={selectedPath}
        highlightPath={highlightFile?.path}
        neighbors={selectionNeighbors}
        onSelectFile={selectFile}
        onSelectRegion={selectBucket}
        mode={spineMode}
      />

      <RiskCalloutBar callout={callout} onSelectFile={selectFile} onSelectBucket={selectBucket} />

      {selectedFile ? (
        <FileDetail
          file={selectedFile}
          maxFanIn={maxFanIn}
          onBack={backToTerrain}
          onSelect={selectFile}
        />
      ) : selectedBucket ? (
        <BucketDetail files={selectedBucket} onBack={backToTerrain} onSelectFile={selectFile} />
      ) : null}

      <div className="flex flex-wrap items-start justify-between gap-2 border-t border-border/60 pt-2">
        <ProvenanceStrip graph={graph} />
        <HowToRead />
      </div>
    </section>
  );
}
