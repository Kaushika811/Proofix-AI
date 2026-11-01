/**
 * A5 — Blast Radius corridor map.
 *
 * This panel's job is not to draw "a graph"; it is to explain how consequence
 * spreads from one candidate change. The corridor therefore stays directional
 * and distance-preserving: upstream on the left, downstream on the right, hop
 * 1 nearest the origin, farther hops pushed outward. Large repositories do not
 * get a different picture — dense columns collapse into count chips, but the
 * traversal shape stays the same.
 */
import { useMemo, useState } from "react";
import { Pin } from "lucide-react";
import type { BlastEdge, BlastImpact, BlastScopeFile } from "./blastTypes";

const MAX_VISIBLE_PER_COLUMN = 5;

const HOP_X: Record<"backward" | "forward", Record<number, number>> = {
  backward: { 1: 38, 2: 24, 3: 10 },
  forward: { 1: 62, 2: 76, 3: 90 },
};
const ORIGIN_XY = { x: 50, y: 50 };
const BAND_TOP = 12;
const BAND_HEIGHT = 76;

const CONTAINMENT_X = {
  backward: (HOP_X.backward[2] + HOP_X.backward[3]) / 2,
  forward: (HOP_X.forward[2] + HOP_X.forward[3]) / 2,
};

const REAL_EDGE = "var(--color-status-completed)";
const RISKY_EDGE = "var(--color-status-retry)";
const ORIGIN_COLOR = "var(--color-status-running)";
const REVIEW_COLOR = "var(--color-status-blocked)";

interface Placed {
  file: BlastScopeFile;
  x: number;
  y: number;
  collapsed: boolean;
  columnKey: string;
}

interface ColumnGroup {
  direction: "backward" | "forward";
  hop: number;
  x: number;
  files: BlastScopeFile[];
}

interface DensityChip {
  key: string;
  direction: "backward" | "forward";
  hop: number;
  x: number;
  y: number;
  count: number;
  files: BlastScopeFile[];
}

function primaryDirection(
  file: BlastScopeFile,
  edgeByDestinationAndHop: Map<string, "backward" | "forward">,
): "backward" | "forward" | null {
  if (file.directions.length === 1) return file.directions[0];
  if (file.directions.length === 0) return null;
  const viaEdge = edgeByDestinationAndHop.get(`${file.path}:${file.hopCount}`);
  return viaEdge ?? file.directions[0];
}

function buildColumns(scope: BlastScopeFile[], edges: BlastEdge[]): ColumnGroup[] {
  const edgeByDestinationAndHop = new Map<string, "backward" | "forward">();
  for (const e of edges) edgeByDestinationAndHop.set(`${e.to}:${e.hopCount}`, e.direction);

  const groups = new Map<string, ColumnGroup>();
  for (const file of scope) {
    if (file.hopCount === null || file.hopCount < 1 || file.hopCount > 3) continue;
    const direction = primaryDirection(file, edgeByDestinationAndHop);
    if (direction === null) continue;
    const x = HOP_X[direction][file.hopCount];
    if (x === undefined) continue;
    const key = `${direction}:${file.hopCount}`;
    const existing = groups.get(key);
    if (existing) {
      existing.files.push(file);
    } else {
      groups.set(key, { direction, hop: file.hopCount, x, files: [file] });
    }
  }
  return [...groups.values()];
}

function placeColumn(group: ColumnGroup): Placed[] {
  const files = [...group.files].sort(
    (a, b) => (b.propagationConfidence ?? -1) - (a.propagationConfidence ?? -1),
  );
  const slots = Math.min(files.length, MAX_VISIBLE_PER_COLUMN);
  return files.map((file, index) => {
    const slot = Math.min(index, slots - 1);
    const y = BAND_TOP + ((slot + 0.5) / slots) * BAND_HEIGHT;
    return {
      file,
      x: group.x,
      y,
      collapsed: index >= MAX_VISIBLE_PER_COLUMN - 1 && files.length > MAX_VISIBLE_PER_COLUMN,
      columnKey: `${group.direction}:${group.hop}`,
    };
  });
}

function directionLabel(direction: "backward" | "forward"): string {
  return direction === "backward"
    ? "upstream lane"
    : "downstream lane";
}

function isOriginPath(path: string | null | undefined, impact: BlastImpact): boolean {
  if (!path) return false;
  return (
    path === impact.origin?.resolvedPath ||
    path === impact.origin?.normalizedPath ||
    impact.origins.includes(path)
  );
}

function buildFocusSet(path: string | null, scopeByPath: Map<string, BlastScopeFile>): Set<string> {
  const focused = new Set<string>();
  let cursor = path;
  while (cursor) {
    if (focused.has(cursor)) break;
    focused.add(cursor);
    cursor = scopeByPath.get(cursor)?.reachedVia ?? null;
  }
  return focused;
}

function isBeyondContainment(x: number, direction: "backward" | "forward"): boolean {
  return direction === "backward" ? x < CONTAINMENT_X.backward : x > CONTAINMENT_X.forward;
}

function confidenceLabel(file: BlastScopeFile): string {
  return file.propagationConfidence !== null
    ? `${Math.round(file.propagationConfidence * 100)}% confidence`
    : "confidence not measured";
}

export function BlastRadiusMap({
  impact,
  onSelect,
  selectedPath,
}: {
  impact: BlastImpact;
  onSelect: (file: BlastScopeFile) => void;
  selectedPath?: string | null;
}) {
  const [hoveredPath, setHoveredPath] = useState<string | null>(null);

  const { columns, placedByPath, densityChips, scopeByPath, hasBackward, hasForward, denseMode } =
    useMemo(() => {
      const nextColumns = buildColumns(impact.scope, impact.edges);
      const nextPlacedByPath = new Map<string, Placed>();
      const nextDensityChips: DensityChip[] = [];
      const nextScopeByPath = new Map<string, BlastScopeFile>();

      for (const file of impact.scope) nextScopeByPath.set(file.path, file);

      for (const column of nextColumns) {
        const placed = placeColumn(column);
        for (const point of placed) {
          nextPlacedByPath.set(point.file.path, point);
        }

        const hidden = placed.filter((point) => point.collapsed).map((point) => point.file);
        if (hidden.length > 0) {
          const anchor = placed.find((point) => point.collapsed);
          if (anchor) {
            nextDensityChips.push({
              key: `${column.direction}:${column.hop}`,
              direction: column.direction,
              hop: column.hop,
              x: anchor.x,
              y: anchor.y,
              count: hidden.length,
              files: hidden,
            });
          }
        }
      }

      return {
        columns: nextColumns,
        placedByPath: nextPlacedByPath,
        densityChips: nextDensityChips,
        scopeByPath: nextScopeByPath,
        hasBackward: nextColumns.some((column) => column.direction === "backward"),
        hasForward: nextColumns.some((column) => column.direction === "forward"),
        denseMode: nextColumns.some((column) => column.files.length > MAX_VISIBLE_PER_COLUMN),
      };
    }, [impact.edges, impact.scope]);

  const visibleNodes = useMemo(
    () => [...placedByPath.values()].filter((point) => !point.collapsed),
    [placedByPath],
  );

  const chipByColumn = useMemo(() => {
    const byColumn = new Map<string, DensityChip>();
    for (const chip of densityChips) byColumn.set(chip.key, chip);
    return byColumn;
  }, [densityChips]);

  const focusPath = hoveredPath ?? selectedPath ?? null;
  const focusSet = useMemo(() => buildFocusSet(focusPath, scopeByPath), [focusPath, scopeByPath]);

  const edgeLines = useMemo(() => {
    return impact.edges
      .map((edge) => {
        const target = placedByPath.get(edge.to);
        if (!target) return null;

        const source = isOriginPath(edge.from, impact)
          ? { x: ORIGIN_XY.x, y: ORIGIN_XY.y, columnKey: null }
          : placedByPath.get(edge.from);
        if (!source) return null;

        const targetChip = target.collapsed ? chipByColumn.get(target.columnKey) : null;
        const sourceChip =
          "columnKey" in source && source.columnKey && placedByPath.get(edge.from)?.collapsed
            ? chipByColumn.get(source.columnKey)
            : null;

        const fromPoint = sourceChip ?? source;
        const toPoint = targetChip ?? target;

        const inFocus =
          focusSet.size === 0 ||
          (focusSet.has(edge.to) && (isOriginPath(edge.from, impact) || focusSet.has(edge.from)));

        return {
          key: `${edge.from}->${edge.to}:${edge.direction}:${edge.hopCount}`,
          edge,
          fromPoint,
          toPoint,
          dimmed: !inFocus,
        };
      })
      .filter((line) => line !== null);
  }, [chipByColumn, focusSet, impact, placedByPath]);

  if (!hasBackward && !hasForward) return null;

  return (
    <div
      role="group"
      aria-label="Blast radius corridor map"
      className="overflow-hidden rounded-2xl border border-border bg-surface-muted/20"
    >
      <div className="border-b border-border/70 px-3 py-2.5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-ink-soft">
              Causal Corridor
            </div>
            <p className="mt-1 max-w-2xl text-[11px] text-ink-soft">
              Read outward from the change origin. Left is possible cause surface, right is
              possible fallout. Distance is preserved by hop count, and dense lanes collapse
              instead of reflowing.
            </p>
          </div>
          <div className="rounded-full border border-border bg-surface px-2.5 py-1 text-[10px] text-ink-soft">
            {denseMode ? "Dense repo mode" : "Sparse repo mode"}
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-3 text-[9px] uppercase tracking-wider text-ink-soft">
          <span className="flex items-center gap-1.5">
            <span
              className="h-[2px] w-3 rounded-full"
              style={{ backgroundColor: REAL_EDGE }}
              aria-hidden
            />
            precise import edge
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="h-[2px] w-3 rounded-full"
              style={{
                backgroundImage: `repeating-linear-gradient(90deg, ${RISKY_EDGE} 0 3px, transparent 3px 6px)`,
              }}
              aria-hidden
            />
            name-matched, could be false
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="h-2.5 w-2.5 rounded-full border"
              style={{ borderColor: REVIEW_COLOR }}
              aria-hidden
            />
            past containment line
          </span>
        </div>
      </div>

      <div className="relative min-h-[18rem] px-3 py-3 lg:min-h-[20rem]">
        <div
          className="absolute inset-x-0 top-0 h-28 opacity-60"
          style={{
            background:
              "radial-gradient(circle at center, rgba(59,130,246,0.10), transparent 45%), linear-gradient(90deg, rgba(244,114,182,0.06), transparent 32%, rgba(59,130,246,0.08) 50%, transparent 68%, rgba(52,211,153,0.06))",
          }}
          aria-hidden
        />

        <svg
          data-testid="blast-corridor-edges"
          className="absolute inset-0 h-full w-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden
        >
          {hasBackward && (
            <line
              x1={CONTAINMENT_X.backward}
              x2={CONTAINMENT_X.backward}
              y1={BAND_TOP - 4}
              y2={BAND_TOP + BAND_HEIGHT + 4}
              stroke="var(--color-border)"
              strokeWidth={0.6}
              strokeDasharray="2 2"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {hasForward && (
            <line
              x1={CONTAINMENT_X.forward}
              x2={CONTAINMENT_X.forward}
              y1={BAND_TOP - 4}
              y2={BAND_TOP + BAND_HEIGHT + 4}
              stroke="var(--color-border)"
              strokeWidth={0.6}
              strokeDasharray="2 2"
              vectorEffect="non-scaling-stroke"
            />
          )}

          {edgeLines.map((line) => (
            <path
              key={line.key}
              d={`M${line.fromPoint.x},${line.fromPoint.y} C ${(line.fromPoint.x + line.toPoint.x) / 2},${line.fromPoint.y} ${(line.fromPoint.x + line.toPoint.x) / 2},${line.toPoint.y} ${line.toPoint.x},${line.toPoint.y}`}
              fill="none"
              stroke={line.edge.basis === "name_contains" ? RISKY_EDGE : REAL_EDGE}
              strokeWidth={focusSet.size > 0 && !line.dimmed ? 0.75 : 0.5}
              strokeDasharray={line.edge.basis === "name_contains" ? "2 1.5" : undefined}
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              style={{ opacity: line.dimmed ? 0.12 : 0.75, transition: "opacity 150ms ease-out" }}
            />
          ))}
        </svg>

        {columns.map((column) => (
          <div
            key={`${column.direction}:${column.hop}`}
            className="absolute -translate-x-1/2 text-center text-[8px] font-medium uppercase tracking-wider text-ink-soft"
            style={{ left: `${column.x}%`, top: 0 }}
          >
            hop {column.hop}
          </div>
        ))}

        {hasBackward && (
          <div className="absolute left-[10%] top-3 -translate-x-1/2 text-[8px] uppercase tracking-[0.28em] text-ink-soft/70">
            possible cause
          </div>
        )}
        {hasForward && (
          <div className="absolute right-[10%] top-3 translate-x-1/2 text-[8px] uppercase tracking-[0.28em] text-ink-soft/70">
            possible fallout
          </div>
        )}

        <div
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-xl border px-3 py-2 shadow-sm"
          style={{
            borderColor: ORIGIN_COLOR,
            background:
              "linear-gradient(180deg, color-mix(in srgb, var(--color-surface) 88%, transparent), var(--color-surface))",
            boxShadow: "0 0 0 1px color-mix(in srgb, var(--color-status-running) 12%, transparent)",
          }}
        >
          <div
            className="whitespace-nowrap text-center text-[8px] font-semibold uppercase tracking-[0.28em]"
            style={{ color: ORIGIN_COLOR }}
          >
            origin
          </div>
          <div
            className="max-w-[8rem] truncate font-mono text-[10px] font-semibold text-ink"
            title={impact.origin?.resolvedPath ?? impact.origin?.normalizedPath ?? ""}
          >
            {(impact.origin?.resolvedPath ?? impact.origin?.normalizedPath ?? "").split("/").pop()}
          </div>
        </div>

        {visibleNodes.map((point) => {
          const direction: "backward" | "forward" =
            point.x < ORIGIN_XY.x ? "backward" : "forward";
          const beyond = isBeyondContainment(point.x, direction);
          const pinned = impact.patchAuthorityOverlap.includes(point.file.path);
          const inFocus = focusSet.size === 0 || focusSet.has(point.file.path);

          return (
            <button
              key={point.file.path}
              type="button"
              onClick={() => onSelect(point.file)}
              onMouseEnter={() => setHoveredPath(point.file.path)}
              onMouseLeave={() => setHoveredPath(null)}
              onFocus={() => setHoveredPath(point.file.path)}
              onBlur={() => setHoveredPath(null)}
              className="absolute -translate-x-1/2 -translate-y-1/2 rounded-xl border px-2 py-1 text-left transition-[opacity,transform,border-color] duration-150 ease-out hover:z-10 hover:-translate-y-[calc(50%+1px)]"
              style={{
                left: `${point.x}%`,
                top: `${point.y}%`,
                borderColor:
                  selectedPath === point.file.path
                    ? ORIGIN_COLOR
                    : beyond
                      ? REVIEW_COLOR
                      : "var(--color-border)",
                borderStyle: beyond && !point.file.autoPatchable ? "dashed" : "solid",
                backgroundColor: "var(--color-surface)",
                boxShadow:
                  selectedPath === point.file.path
                    ? "0 0 0 1px color-mix(in srgb, var(--color-status-running) 22%, transparent)"
                    : undefined,
                opacity: inFocus ? 1 : 0.28,
              }}
              aria-label={`${point.file.path}, hop ${point.file.hopCount}, ${directionLabel(direction)}, ${confidenceLabel(point.file)}${pinned ? ", pinned into auto-patch scope" : ""}`}
            >
              <span className="flex items-center gap-1.5">
                {pinned && (
                  <Pin
                    className="h-2.5 w-2.5 shrink-0"
                    style={{ color: "var(--color-status-retry)" }}
                    aria-hidden
                  />
                )}
                <span className="max-w-[7rem] truncate font-mono text-[9px] text-ink">
                  {point.file.path.split("/").pop()}
                </span>
              </span>
            </button>
          );
        })}

        {densityChips.map((chip) => {
          const inFocus =
            focusSet.size === 0 || chip.files.some((file) => focusSet.has(file.path));

          return (
            <div
              key={chip.key}
              className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border border-dashed px-2 py-1 text-center"
              style={{
                left: `${chip.x}%`,
                top: `${chip.y}%`,
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-surface-muted)",
                opacity: inFocus ? 1 : 0.28,
              }}
              title={`${chip.count} more file${chip.count === 1 ? "" : "s"} in ${directionLabel(chip.direction)}, hop ${chip.hop}`}
            >
              <span className="font-mono text-[9px] text-ink-soft">+{chip.count}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
