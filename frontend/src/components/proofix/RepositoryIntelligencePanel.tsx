/**
 * A0.5 — Repository Knowledge Graph.
 *
 * Everything on this panel is fetched from the backend's existing read-only
 * knowledge endpoints (`backend/api/routes/knowledge.py`) — the graph is never
 * reconstructed, scanned, or guessed client-side. `graph`/`dependencyGraph` are
 * the server's own `GraphView` export (`services/graph_export.py`); `metrics`
 * is the server's own `KnowledgeGraphMetrics`; the Node Inspector's per-file
 * counts come from the server's own named traversals
 * (`services/knowledge_graph.py::KnowledgeQueryEngine`).
 *
 * Layout is computed once per graph payload (memoized) and never recomputed on
 * pan/zoom/hover — those only change an SVG transform attribute. There is no
 * synthetic "building" animation: the graph either has not loaded yet (shown as
 * "Loading…"), has not been published for this run (shown as "Pending"), or is
 * on screen in full.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  ZoomIn,
  ZoomOut,
  Maximize2,
  RotateCcw,
  Table2,
  Network,
  FolderTree,
  X,
  Database,
  FileCode2,
  FlaskConical,
  Settings2,
  Component,
  Braces,
  ArrowRight,
  ArrowDown,
  ChevronRight,
  CheckCircle2,
  type LucideIcon,
} from "lucide-react";
import type { IntelligencePayload } from "./visualizationTypes";
import type {
  KGEdge,
  KGNode,
  KnowledgeCapability,
  KnowledgeGraphExport,
  KnowledgeHotspot,
  KnowledgeMetrics,
  KnowledgeQueryResult,
} from "./knowledgeGraphTypes";
import {
  getKnowledgeCapabilities,
  getKnowledgeGraph,
  getKnowledgeHotspots,
  getKnowledgeMetrics,
  getKnowledgeQuery,
} from "@/lib/runService";

type ViewMode = "graph" | "table" | "structure";

const MAX_GRAPH_NODES = 220;

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load the repository graph</span>
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

function StatChip({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-border bg-surface-muted/60 px-2.5 py-1.5">
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">{label}</div>
      <div className="font-mono text-sm font-semibold text-ink">{value}</div>
    </div>
  );
}

/**
 * A controlled, deterministic stand-in for the real graph — a hub with one
 * spoke-cluster per node type actually present in `nodes_by_type`, sized
 * (never counted) by that type's real proportion of the total. This is a
 * visual metaphor for "the repository became a connected graph", not a
 * rendering of real nodes/edges: it carries no labels and no node identity,
 * and the dashed spokes deliberately do not claim to be real edges. The
 * literal node/edge totals are shown as `StatChip`s beside it, sourced from
 * `metrics.node_count` / `metrics.edge_count`. Node count is capped at 8–14
 * so it never becomes the hairball the real graph would be at repo scale.
 */
function MiniGraph({
  typeCounts,
  colorOf,
}: {
  typeCounts: [string, number][];
  colorOf: (type: string) => string;
}) {
  if (typeCounts.length === 0) return null;

  const MIN_DOTS = 8;
  const MAX_DOTS = 14;
  const budget = Math.min(MAX_DOTS, Math.max(MIN_DOTS, typeCounts.length + 4));
  const total = typeCounts.reduce((sum, [, count]) => sum + count, 0) || 1;

  const slots = typeCounts.map(([type, count]) => ({
    type,
    n: Math.max(1, Math.round((count / total) * (budget - typeCounts.length))),
  }));
  // Trim deterministically (left to right) until the visual budget is met —
  // never below one dot per type actually present.
  let over = slots.reduce((sum, s) => sum + s.n, 0) - budget;
  for (let i = 0; over > 0 && i < slots.length * 4; i += 1) {
    const s = slots[i % slots.length];
    if (s.n > 1) {
      s.n -= 1;
      over -= 1;
    }
  }

  const HUB = { x: 110, y: 74 };
  const RADIUS = 52;
  const angleStep = (2 * Math.PI) / slots.length;
  const dots = slots.flatMap((s, typeIdx) => {
    const base = typeIdx * angleStep - Math.PI / 2;
    return Array.from({ length: s.n }, (_, k) => {
      const spread = s.n > 1 ? (k / (s.n - 1) - 0.5) * 0.7 : 0;
      const r = RADIUS + (k % 2) * 12;
      return {
        type: s.type,
        x: HUB.x + r * Math.cos(base + spread),
        y: HUB.y + r * Math.sin(base + spread),
      };
    });
  });

  return (
    <svg
      viewBox="0 0 220 148"
      role="img"
      aria-label="Repository structure represented as a connected knowledge graph"
      className="h-[144px] w-full"
    >
      {dots.map((d, i) => (
        <line
          key={`spoke-${i}`}
          x1={HUB.x}
          y1={HUB.y}
          x2={d.x}
          y2={d.y}
          stroke="currentColor"
          strokeWidth={1}
          strokeDasharray="2.5 2.5"
          className="text-border"
        />
      ))}
      <circle cx={HUB.x} cy={HUB.y} r={6} fill="currentColor" className="text-ink-soft" />
      {dots.map((d, i) => (
        <circle key={`dot-${i}`} cx={d.x} cy={d.y} r={4} fill={colorOf(d.type)} />
      ))}
    </svg>
  );
}

/**
 * What A0.5 actually did, in the order it did it — the real named phases from
 * its own event (`_INDEX_PHASES` in `ui_projection.py`: parsing the structural
 * graph, the call graph, ownership from git blame, commit history, then
 * documentation). A phase that took 0ms is omitted upstream because it did not
 * run, not because it was fast — so this list is never padded to look busier
 * than the index build actually was. This is the clearest single answer to
 * "what does this agent do", so it renders above the graph, not beside it.
 */
function IndexingPipeline({
  phases,
  totalMs,
}: {
  phases: { label: string; ms: number }[];
  totalMs: number;
}) {
  if (phases.length === 0) return null;
  const sum = phases.reduce((a, p) => a + p.ms, 0) || 1;
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between text-[10px] font-medium uppercase tracking-wider text-ink-soft">
        <span>Indexing pipeline</span>
        <span className="font-mono normal-case text-ink-soft">{totalMs}ms total</span>
      </div>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-muted">
        {phases.map((p, i) => (
          <div
            key={p.label}
            className="h-full bg-primary"
            style={{ width: `${(p.ms / sum) * 100}%`, opacity: 1 - i * 0.15 }}
            title={`${p.label}: ${p.ms}ms`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
        {phases.map((p, i) => (
          <span key={p.label} className="flex items-center gap-1 text-[10px] text-ink-soft">
            <span
              className="h-1.5 w-1.5 rounded-full bg-primary"
              style={{ opacity: 1 - i * 0.15 }}
            />
            {p.label}
            <span className="font-mono text-ink">{p.ms}ms</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- tree layout

interface Positioned {
  node: KGNode;
  x: number;
  y: number;
  depth: number;
}

/**
 * A simple, deterministic tree layout over `CONTAINS` edges — the "repository"
 * view is a containment tree by construction (repository → package → file →
 * class/function), so no force simulation is needed. Leaves are placed left to
 * right in a stable order; a parent sits above the midpoint of its children.
 */
function layoutTree(nodes: KGNode[], edges: KGEdge[]) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const children = new Map<string, string[]>();
  const hasParent = new Set<string>();
  for (const e of edges) {
    if (e.type !== "CONTAINS") continue;
    if (!byId.has(e.source) || !byId.has(e.target)) continue;
    if (!children.has(e.source)) children.set(e.source, []);
    children.get(e.source)!.push(e.target);
    hasParent.add(e.target);
  }
  for (const list of children.values()) list.sort();

  const roots = nodes.filter((n) => !hasParent.has(n.id)).map((n) => n.id);
  // Wide enough for a diagonal label under every leaf without overlap on a
  // typical single-package repository (a few dozen files); a very large graph
  // still lays out correctly, it is just denser to read — which is what the
  // truncation notice and the Table/Structure fallbacks are for.
  const XGAP = 64;
  const YGAP = 72;
  let cursor = 0;
  const positions = new Map<string, Positioned>();

  const place = (id: string, depth: number): number => {
    const node = byId.get(id);
    if (!node) return cursor;
    const kids = children.get(id) ?? [];
    if (kids.length === 0) {
      const x = cursor * XGAP;
      cursor += 1;
      positions.set(id, { node, x, y: depth * YGAP, depth });
      return x;
    }
    const xs = kids.map((k) => place(k, depth + 1));
    const x = xs.reduce((a, b) => a + b, 0) / xs.length;
    positions.set(id, { node, x, y: depth * YGAP, depth });
    return x;
  };

  for (const r of roots.sort()) place(r, 0);
  // Orphans (no CONTAINS edge at all in this view) get their own row.
  for (const n of nodes) {
    if (!positions.has(n.id)) {
      const x = cursor * XGAP;
      cursor += 1;
      positions.set(n.id, { node: n, x, y: 0, depth: 0 });
    }
  }

  const maxX = Math.max(1, ...[...positions.values()].map((p) => p.x));
  const maxY = Math.max(1, ...[...positions.values()].map((p) => p.y));
  return { positions, width: maxX + XGAP, height: maxY + YGAP };
}

// --------------------------------------------------------------- inspector

interface NodeQueryState {
  functions: KnowledgeQueryResult | null | "loading";
  tests: KnowledgeQueryResult | null | "loading";
  owners: KnowledgeQueryResult | null | "loading";
}

function AttributeRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-[10px] uppercase tracking-wider text-ink-soft">{label}</span>
      <span
        className="max-w-[65%] truncate text-right font-mono text-[11px] text-ink"
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function NodeInspector({
  node,
  runId,
  connections,
  onClose,
  onFocus,
}: {
  node: KGNode;
  runId: string;
  connections: { imports: number; importedBy: number; total: number } | null;
  onClose: () => void;
  onFocus: () => void;
}) {
  const [queried, setQueried] = useState<NodeQueryState>({
    functions: null,
    tests: null,
    owners: null,
  });

  useEffect(() => {
    setQueried({ functions: null, tests: null, owners: null });
    if (node.type !== "file" && node.type !== "test") return;
    let cancelled = false;
    setQueried({ functions: "loading", tests: "loading", owners: "loading" });
    Promise.all([
      getKnowledgeQuery(runId, "functions_in_file", { file: node.file }),
      getKnowledgeQuery(runId, "supporting_tests", { file: node.file }),
      getKnowledgeQuery(runId, "owners_of", { file: node.file }),
    ]).then(([functions, tests, owners]) => {
      if (!cancelled) setQueried({ functions, tests, owners });
    });
    return () => {
      cancelled = true;
    };
  }, [node.id, node.file, node.type, runId]);

  const attrs = node.attributes;
  const isFileLike = node.type === "file" || node.type === "test";

  return (
    <aside className="w-full shrink-0 rounded-2xl border border-border bg-surface p-4 lg:w-[300px]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[13px] font-semibold text-ink" title={node.label}>
            {node.label}
          </div>
          <div className="mt-0.5 truncate text-[10px] text-ink-soft" title={node.file}>
            {node.file || "—"}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close node inspector"
          className="shrink-0 rounded-md p-1 text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="mt-3 divide-y divide-border/60 border-t border-border/60">
        <AttributeRow label="Type" value={node.type} />
        {node.qualname && <AttributeRow label="Qualname" value={node.qualname} />}
        {typeof attrs.lineno === "number" && attrs.lineno > 0 && (
          <AttributeRow
            label="Lines"
            value={`${attrs.lineno}${attrs.end_lineno ? `–${attrs.end_lineno}` : ""}`}
          />
        )}
        {typeof attrs.is_async === "boolean" && (
          <AttributeRow label="Async" value={attrs.is_async ? "yes" : "no"} />
        )}
        {typeof attrs.is_test === "boolean" && (
          <AttributeRow label="Test file" value={attrs.is_test ? "yes" : "no"} />
        )}
        {Array.isArray(attrs.decorators) && attrs.decorators.length > 0 && (
          <AttributeRow label="Decorators" value={attrs.decorators.join(", ")} />
        )}
        {Array.isArray(attrs.bases) && attrs.bases.length > 0 && (
          <AttributeRow label="Bases" value={attrs.bases.join(", ")} />
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-1.5">
        <StatChip label="Imports" value={connections ? connections.imports : "Not measured"} />
        <StatChip
          label="Imported by"
          value={connections ? connections.importedBy : "Not measured"}
        />
        {isFileLike && (
          <>
            <StatChip
              label="Functions"
              value={
                queried.functions === "loading"
                  ? "…"
                  : queried.functions
                    ? queried.functions.count
                    : "Not measured"
              }
            />
            <StatChip
              label="Tests"
              value={
                queried.tests === "loading"
                  ? "…"
                  : queried.tests
                    ? queried.tests.count
                    : "Not measured"
              }
            />
          </>
        )}
      </div>

      {isFileLike && queried.owners && queried.owners !== "loading" && queried.owners.count > 0 && (
        <div className="mt-3">
          <div className="text-[10px] uppercase tracking-wider text-ink-soft">Owners</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {queried.owners.results.map((o) => (
              <span
                key={o.id}
                className="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[10px] text-ink-soft"
              >
                {o.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {typeof attrs.docstring === "string" && attrs.docstring && (
        <p className="mt-3 rounded-md border border-border bg-surface-muted/40 p-2 text-[11px] text-ink-soft">
          {attrs.docstring}
        </p>
      )}

      <button
        type="button"
        onClick={onFocus}
        className="mt-3 w-full rounded-md border border-border px-2 py-1.5 text-[11px] font-medium text-ink transition-colors hover:bg-surface-muted"
      >
        View connections
      </button>
    </aside>
  );
}

// ------------------------------------------------------------------ graph

function GraphCanvas({
  nodes,
  edges,
  selectedId,
  matchIds,
  focusId,
  onSelect,
}: {
  nodes: KGNode[];
  edges: KGEdge[];
  selectedId: string | null;
  matchIds: Set<string> | null;
  focusId: string | null;
  onSelect: (id: string) => void;
}) {
  const layout = useMemo(() => layoutTree(nodes, edges), [nodes, edges]);
  // Below ~70 nodes every label fits without becoming a smear; past that,
  // labels appear on hover/selection only (the Table and Structure views
  // exist precisely for reading a large graph's labels).
  const showAllLabels = nodes.length <= 70;
  const [transform, setTransform] = useState({ x: 24, y: 24, scale: 1 });
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const visibleEdgeSet = useMemo(() => {
    if (!focusId) return null;
    const s = new Set<string>();
    for (const e of edges) {
      if (e.source === focusId) s.add(e.target);
      if (e.target === focusId) s.add(e.source);
    }
    s.add(focusId);
    return s;
  }, [edges, focusId]);

  const fit = () => {
    const el = svgRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const scale = Math.min(
      1.4,
      Math.max(0.15, Math.min(rect.width / layout.width, 420 / layout.height)),
    );
    setTransform({ x: 24, y: 24, scale });
  };

  useEffect(() => {
    fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout.width, layout.height]);

  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-surface-muted/30">
      <div className="absolute right-2 top-2 z-10 flex gap-1">
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => setTransform((t) => ({ ...t, scale: Math.min(3, t.scale * 1.25) }))}
          className="rounded-md border border-border bg-surface p-1.5 text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <ZoomIn className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => setTransform((t) => ({ ...t, scale: Math.max(0.15, t.scale * 0.8) }))}
          className="rounded-md border border-border bg-surface p-1.5 text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <ZoomOut className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          aria-label="Fit to screen"
          onClick={fit}
          className="rounded-md border border-border bg-surface p-1.5 text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <Maximize2 className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          aria-label="Reset view"
          onClick={() => setTransform({ x: 24, y: 24, scale: 1 })}
          className="rounded-md border border-border bg-surface p-1.5 text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </button>
      </div>

      <svg
        ref={svgRef}
        role="img"
        aria-label="Repository knowledge graph"
        className="h-[420px] w-full cursor-grab touch-none active:cursor-grabbing"
        onWheel={(e) => {
          e.preventDefault();
          const factor = e.deltaY > 0 ? 0.9 : 1.1;
          setTransform((t) => ({ ...t, scale: Math.min(3, Math.max(0.15, t.scale * factor)) }));
        }}
        onMouseDown={(e) => {
          dragRef.current = { x: e.clientX, y: e.clientY, ox: transform.x, oy: transform.y };
        }}
        onMouseMove={(e) => {
          if (!dragRef.current) return;
          const dx = e.clientX - dragRef.current.x;
          const dy = e.clientY - dragRef.current.y;
          setTransform((t) => ({ ...t, x: dragRef.current!.ox + dx, y: dragRef.current!.oy + dy }));
        }}
        onMouseUp={() => {
          dragRef.current = null;
        }}
        onMouseLeave={() => {
          dragRef.current = null;
        }}
      >
        <g transform={`translate(${transform.x} ${transform.y}) scale(${transform.scale})`}>
          {edges.map((e, i) => {
            const a = layout.positions.get(e.source);
            const b = layout.positions.get(e.target);
            if (!a || !b) return null;
            const dimmed =
              visibleEdgeSet && !(visibleEdgeSet.has(e.source) && visibleEdgeSet.has(e.target));
            return (
              <line
                key={`${e.source}-${e.target}-${i}`}
                x1={a.x + 6}
                y1={a.y + 6}
                x2={b.x + 6}
                y2={b.y + 6}
                stroke="currentColor"
                strokeWidth={0.8}
                className={dimmed ? "text-border/30" : "text-border"}
              />
            );
          })}
          {[...layout.positions.values()].map((p) => {
            const dimmed = visibleEdgeSet && !visibleEdgeSet.has(p.node.id);
            const isMatch = matchIds?.has(p.node.id);
            const isSelected = p.node.id === selectedId;
            return (
              <g
                key={p.node.id}
                transform={`translate(${p.x} ${p.y})`}
                className={`cursor-pointer transition-opacity duration-150 ${dimmed ? "opacity-25" : "opacity-100"}`}
                onClick={() => onSelect(p.node.id)}
                onMouseEnter={() => setHoverId(p.node.id)}
                onMouseLeave={() => setHoverId((h) => (h === p.node.id ? null : h))}
              >
                <circle
                  r={isSelected ? 7 : 5.5}
                  fill={p.node.color}
                  stroke={isSelected || isMatch ? "hsl(var(--primary))" : "transparent"}
                  strokeWidth={isSelected ? 2.5 : isMatch ? 2 : 0}
                />
                {showAllLabels ||
                hoverId === p.node.id ||
                isSelected ||
                p.node.type === "repository" ||
                p.node.type === "package" ? (
                  <text
                    x={5}
                    y={5}
                    transform="rotate(38 5 5)"
                    className={`fill-current font-mono text-[9px] ${
                      hoverId === p.node.id || isSelected
                        ? "text-ink font-semibold"
                        : "text-ink-soft"
                    }`}
                  >
                    {p.node.label}
                  </text>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

// -------------------------------------------------------------------- tree

const STRUCTURE_ICON: Record<string, LucideIcon> = {
  repository: Database,
  package: FolderTree,
  directory: FolderTree,
  file: FileCode2,
  test: FlaskConical,
  config: Settings2,
  class: Component,
  function: Braces,
  method: Braces,
};

/**
 * A `tree`-style directory listing — vertical guide rules and `├──`/`└──`
 * connectors, the shape every engineer already reads a file tree in — over
 * the same `CONTAINS` edges the Graph view draws. `ancestorContinues` tracks,
 * for every level above the current row, whether that ancestor still has
 * later siblings; that is what turns a plain indent into a connected tree
 * instead of a bulleted list.
 */
function StructureTree({
  nodes,
  edges,
  selectedId,
  onSelect,
}: {
  nodes: KGNode[];
  edges: KGEdge[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const children = useMemo(() => {
    const map = new Map<string, string[]>();
    const hasParent = new Set<string>();
    for (const e of edges) {
      if (e.type !== "CONTAINS") continue;
      if (!byId.has(e.source) || !byId.has(e.target)) continue;
      if (!map.has(e.source)) map.set(e.source, []);
      map.get(e.source)!.push(e.target);
      hasParent.add(e.target);
    }
    for (const list of map.values())
      list.sort((a, b) => (byId.get(a)?.label ?? "").localeCompare(byId.get(b)?.label ?? ""));
    return {
      map,
      roots: nodes
        .filter((n) => !hasParent.has(n.id))
        .map((n) => n.id)
        .sort(),
    };
  }, [nodes, edges, byId]);

  const renderNode = (
    id: string,
    guides: boolean[],
    isLast: boolean,
    isRoot: boolean,
  ): React.ReactNode => {
    const node = byId.get(id);
    if (!node) return null;
    const kids = children.map.get(id) ?? [];
    const Icon = STRUCTURE_ICON[node.type] ?? FileCode2;
    // Guides for this node's own children: one column per ancestor level,
    // plus this node's level once it is no longer the invisible root.
    const childGuides = isRoot ? [] : [...guides, !isLast];

    return (
      <li key={id}>
        <button
          type="button"
          onClick={() => onSelect(id)}
          className={`group flex w-full items-center gap-1 rounded px-1 py-[3px] text-left font-mono text-[11px] transition-colors hover:bg-surface-muted ${
            id === selectedId ? "bg-primary/10 text-primary" : "text-ink"
          }`}
        >
          {/* Guide rails: one column per ancestor level, `│` while that
              ancestor still has siblings below it, blank once it doesn't. */}
          {guides.map((cont, i) => (
            <span key={i} className="w-3.5 shrink-0 select-none text-center text-ink-soft/25">
              {cont ? "│" : ""}
            </span>
          ))}
          {!isRoot && (
            <span className="w-3.5 shrink-0 select-none text-ink-soft/40">
              {isLast ? "└─" : "├─"}
            </span>
          )}
          <Icon
            className="h-3 w-3 shrink-0"
            style={{ color: node.color }}
            strokeWidth={node.type === "repository" ? 2.5 : 2}
          />
          <span className={`truncate ${isRoot ? "font-semibold" : ""}`}>{node.label}</span>
          {kids.length > 0 && (
            <span className="ml-1 shrink-0 text-[9px] text-ink-soft/50 group-hover:text-ink-soft">
              {kids.length}
            </span>
          )}
        </button>
        {kids.length > 0 && (
          <ul>{kids.map((k, i) => renderNode(k, childGuides, i === kids.length - 1, false))}</ul>
        )}
      </li>
    );
  };

  return (
    <div className="max-h-[420px] overflow-y-auto rounded-lg border border-border bg-surface-muted/30 p-2.5">
      <ul>
        {children.roots.map((r, i) => renderNode(r, [], i === children.roots.length - 1, true))}
      </ul>
    </div>
  );
}

// ------------------------------------------------------------------- table

function NodesTable({
  nodes,
  edges,
  selectedId,
  onSelect,
}: {
  nodes: KGNode[];
  edges: KGEdge[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const degree = useMemo(() => {
    const d = new Map<string, number>();
    for (const e of edges) {
      d.set(e.source, (d.get(e.source) ?? 0) + 1);
      d.set(e.target, (d.get(e.target) ?? 0) + 1);
    }
    return d;
  }, [edges]);

  const rows = nodes.slice(0, 200);

  return (
    <div className="max-h-[420px] overflow-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-left text-[11px]">
        <thead className="sticky top-0 bg-surface">
          <tr className="border-b border-border text-[9px] uppercase tracking-wider text-ink-soft">
            <th className="px-2 py-1.5 font-medium">Node</th>
            <th className="px-2 py-1.5 font-medium">Type</th>
            <th className="px-2 py-1.5 font-medium">Path</th>
            <th className="px-2 py-1.5 text-right font-medium">Connections</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((n) => (
            <tr
              key={n.id}
              onClick={() => onSelect(n.id)}
              className={`cursor-pointer border-b border-border/60 transition-colors hover:bg-surface-muted ${
                n.id === selectedId ? "bg-primary/5" : ""
              }`}
            >
              <td className="max-w-[160px] truncate px-2 py-1 font-mono text-ink" title={n.label}>
                {n.label}
              </td>
              <td className="px-2 py-1 text-ink-soft">{n.type}</td>
              <td
                className="max-w-[220px] truncate px-2 py-1 font-mono text-ink-soft"
                title={n.file}
              >
                {n.file || "—"}
              </td>
              <td className="px-2 py-1 text-right font-mono text-ink">{degree.get(n.id) ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {nodes.length > rows.length && (
        <div className="border-t border-border px-2 py-1.5 text-[10px] text-ink-soft">
          Showing {rows.length} of {nodes.length} nodes.
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- panel

export function RepositoryIntelligencePanel({
  runId,
  intelligence,
}: {
  runId: string;
  intelligence?: IntelligencePayload;
}) {
  const [metrics, setMetrics] = useState<KnowledgeMetrics | null>(null);
  const [graph, setGraph] = useState<KnowledgeGraphExport | null>(null);
  const [depGraph, setDepGraph] = useState<KnowledgeGraphExport | null>(null);
  const [capabilities, setCapabilities] = useState<KnowledgeCapability[] | null>(null);
  const [hotspots, setHotspots] = useState<KnowledgeHotspot[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Structure first: a labelled file tree is legible at a glance and is
  // exactly what A0.5 produced — the abstract node/edge graph is the power
  // view for someone who already knows what they're looking for.
  const [viewMode, setViewMode] = useState<ViewMode>("structure");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<Set<string> | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      getKnowledgeMetrics(runId),
      getKnowledgeGraph(runId, "repository", MAX_GRAPH_NODES),
      getKnowledgeGraph(runId, "dependency", MAX_GRAPH_NODES),
      getKnowledgeCapabilities(runId),
      getKnowledgeHotspots(runId),
    ])
      .then(([m, g, d, c, h]) => {
        if (cancelled) return;
        setMetrics(m);
        setGraph(g);
        setDepGraph(d);
        setCapabilities(c);
        setHotspots(h);
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
  }, [runId, attempt]);

  const nodeTypes = useMemo(
    () => (graph ? [...new Set(graph.nodes.map((n) => n.type))].sort() : []),
    [graph],
  );
  const activeTypes = useMemo(() => typeFilter ?? new Set(nodeTypes), [typeFilter, nodeTypes]);

  const filteredNodes = useMemo(() => {
    if (!graph) return [];
    return graph.nodes.filter((n) => activeTypes.has(n.type));
  }, [graph, activeTypes]);
  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((n) => n.id)), [filteredNodes]);
  const filteredEdges = useMemo(
    () =>
      graph
        ? graph.edges.filter((e) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target))
        : [],
    [graph, filteredNodeIds],
  );

  const matchIds = useMemo(() => {
    if (!search.trim() || !graph) return null;
    const q = search.trim().toLowerCase();
    return new Set(
      graph.nodes
        .filter((n) => n.label.toLowerCase().includes(q) || n.file.toLowerCase().includes(q))
        .map((n) => n.id),
    );
  }, [search, graph]);

  const selectedNode = graph?.nodes.find((n) => n.id === selectedId) ?? null;

  const connections = useMemo(() => {
    if (!selectedId || !depGraph) return null;
    let imports = 0;
    let importedBy = 0;
    for (const e of depGraph.edges) {
      if (e.type !== "IMPORTS" && e.type !== "DEPENDS_ON") continue;
      if (e.source === selectedId) imports += 1;
      if (e.target === selectedId) importedBy += 1;
    }
    return { imports, importedBy, total: imports + importedBy };
  }, [selectedId, depGraph]);

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Repository Knowledge Graph
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Loading…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!graph || !metrics) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Repository Knowledge Graph
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          Pending — Repository Intelligence has not published a graph for this run yet.
        </p>
      </section>
    );
  }

  const dna = metrics.nodes_by_type;
  const languages = Object.entries(metrics.workspace.languages ?? {}).sort((a, b) => b[1] - a[1]);
  const typeCounts = Object.entries(dna)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const colorOfType = (type: string) => graph.nodes.find((n) => n.type === type)?.color ?? "#888";
  // Real, already-fetched structural labels for the repository box — the same
  // "package" nodes the Structure tree and node-type filters use, never a new
  // fetch or a derived guess.
  const packageLabels = graph.nodes
    .filter((n) => n.type === "package" || n.type === "directory")
    .map((n) => n.label)
    .sort();

  return (
    <section className="space-y-4 rounded-2xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            Repository Knowledge Graph
          </h3>
          <p className="mt-0.5 text-[11px] text-ink-soft">
            Repository structure transformed into reusable knowledge.
          </p>
        </div>
        {intelligence && (
          <span
            className={`shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
              intelligence.mode === "cache hit"
                ? "border-status-completed/30 bg-status-completed-bg text-status-completed"
                : "border-border bg-surface-muted text-ink-soft"
            }`}
          >
            {intelligence.mode === "cache hit" ? "● Cache hit" : `● ${intelligence.mode}`}
          </span>
        )}
      </div>

      {/* Hero — Repository transformed into a knowledge graph. The single
          thing this agent does, shown rather than stated: real repository
          facts on the left, the real aggregate graph scale on the right,
          connected by one directional arrow. */}
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-[1fr_auto_1.15fr] lg:items-stretch">
        <div className="rounded-xl border border-border bg-surface-muted/30 p-3">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
            <FolderTree className="h-3.5 w-3.5" />
            Repository
          </div>
          {packageLabels.length > 0 && (
            <div className="mt-2 space-y-0.5 font-mono text-[11px] text-ink-soft">
              {packageLabels.slice(0, 4).map((p) => (
                <div key={p} className="truncate" title={p}>
                  {p}/
                </div>
              ))}
              {packageLabels.length > 4 && (
                <div className="text-ink-soft/60">+{packageLabels.length - 4} more</div>
              )}
            </div>
          )}
          <div className="mt-3 grid grid-cols-3 gap-1.5">
            <StatChip label="Files" value={dna.file ?? 0} />
            <StatChip label="Packages" value={dna.package ?? 0} />
            <StatChip label="Classes" value={dna.class ?? 0} />
            <StatChip label="Callables" value={(dna.function ?? 0) + (dna.method ?? 0)} />
            <StatChip label="Tests" value={dna.test ?? 0} />
            <StatChip label="Import Edges" value={metrics.edges_by_type.IMPORTS ?? 0} />
          </div>
          {languages.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {languages.map(([lang, count]) => (
                <span
                  key={lang}
                  className="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[10px] text-ink-soft"
                >
                  {lang} {count}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-center py-1 text-ink-soft/50 lg:py-0">
          <ArrowDown className="h-4 w-4 lg:hidden" aria-hidden="true" />
          <ArrowRight className="hidden h-4 w-4 lg:block" aria-hidden="true" />
        </div>

        <div className="rounded-xl border border-border bg-surface-muted/30 p-3">
          <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
            <Network className="h-3.5 w-3.5" />
            Knowledge graph
          </div>
          <MiniGraph typeCounts={typeCounts} colorOf={colorOfType} />
          {typeCounts.length > 1 && (
            <div className="flex flex-wrap gap-x-2 gap-y-0.5">
              {typeCounts.map(([type, count]) => (
                <span key={type} className="flex items-center gap-1 text-[9px] text-ink-soft">
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: colorOfType(type) }}
                  />
                  {type}
                  <span className="font-mono text-ink">{count}</span>
                </span>
              ))}
            </div>
          )}
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            <StatChip label="Nodes" value={metrics.node_count} />
            <StatChip label="Edges" value={metrics.edge_count} />
          </div>
        </div>
      </div>

      {/* Step 8 — index/cache status, from A0.5's own event. This is A0.5's
          entire reason to exist as a separate stage: whether it reused a
          previous index or had to build one. */}
      {intelligence && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface-muted/40 p-2.5 text-[11px] text-ink-soft">
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
              intelligence.mode === "cache hit" ? "bg-status-completed" : "bg-status-retry"
            }`}
          />
          <span>{intelligence.modeDetail}</span>
          {intelligence.totalMs > 0 && (
            <span className="font-mono text-ink">· {intelligence.totalMs}ms</span>
          )}
        </div>
      )}

      {/* What the agent actually did, in the order it did it — the real answer
          to "what does this agent do", not an illustration of it. */}
      {intelligence && intelligence.phases.length > 0 && (
        <IndexingPipeline phases={intelligence.phases} totalMs={intelligence.totalMs} />
      )}

      {/* Intelligence strip — what A0.5 discovered, not just what it counted. */}
      <div>
        <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Intelligence
        </div>
        <div className="grid grid-cols-2 gap-1.5 sm:w-64">
          <StatChip label="Capabilities" value={capabilities?.length ?? "Not measured"} />
          <StatChip label="Hotspots" value={hotspots?.length ?? "Not measured"} />
        </div>
        {((capabilities && capabilities.length > 0) || (hotspots && hotspots.length > 0)) && (
          <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {capabilities && capabilities.length > 0 && (
              <div className="rounded-lg border border-border p-2">
                <div className="text-[9px] uppercase tracking-wider text-ink-soft">
                  Capabilities
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {capabilities.slice(0, 4).map((c) => (
                    <span
                      key={c.slug}
                      className="rounded-full border border-border bg-surface-muted px-2 py-0.5 text-[10px] text-ink"
                      title={c.why || undefined}
                    >
                      {c.name}
                      {typeof c.confidence === "number" && (
                        <span className="ml-1 font-mono text-ink-soft">
                          {Math.round(c.confidence * 100)}%
                        </span>
                      )}
                    </span>
                  ))}
                  {capabilities.length > 4 && (
                    <span className="self-center text-[10px] text-ink-soft">
                      +{capabilities.length - 4} more
                    </span>
                  )}
                </div>
              </div>
            )}
            {hotspots && hotspots.length > 0 && (
              <div className="rounded-lg border border-border p-2">
                <div className="text-[9px] uppercase tracking-wider text-ink-soft">Hotspots</div>
                <div className="mt-1 space-y-1">
                  {hotspots.slice(0, 4).map((h, i) => (
                    <div
                      key={`${h.kind}-${h.target}-${i}`}
                      className="flex items-center gap-1.5 text-[10px]"
                    >
                      <span
                        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                          h.severity >= 0.7
                            ? "bg-status-failed"
                            : h.severity >= 0.4
                              ? "bg-status-retry"
                              : "bg-status-completed"
                        }`}
                        title={`severity ${h.severity}`}
                      />
                      <span className="truncate font-mono text-ink" title={h.target}>
                        {h.target}
                      </span>
                    </div>
                  ))}
                  {hotspots.length > 4 && (
                    <div className="text-[10px] text-ink-soft">+{hotspots.length - 4} more</div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Collapsed by default — the hero already answers "what did A0.5 do";
          this is the file-browser-style explorer for someone who wants to
          drill into a specific node, not part of the at-a-glance story. */}
      <details className="group rounded-lg border border-border">
        <summary className="flex cursor-pointer select-none items-center gap-1.5 px-3 py-2 text-[11px] font-medium text-ink-soft [&::-webkit-details-marker]:hidden">
          <ChevronRight className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90" />
          Repository structure
        </summary>
        <div className="space-y-3 border-t border-border p-3">
          {/* Toolbar: view switch, search, filters. */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-md border border-border p-0.5">
              {(
                [
                  ["graph", Network, "Graph"],
                  ["structure", FolderTree, "Structure"],
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

            <div className="relative flex-1 min-w-[160px]">
              <Search className="pointer-events-none absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-ink-soft" />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search files, functions, classes…"
                aria-label="Search repository nodes"
                className="w-full rounded-md border border-border bg-surface py-1 pl-7 pr-2 text-[11px] text-ink outline-none focus:border-primary/50"
              />
            </div>

            {focusId && (
              <button
                type="button"
                onClick={() => setFocusId(null)}
                className="rounded-md border border-primary/30 bg-primary/5 px-2 py-1 text-[11px] font-medium text-primary"
              >
                Clear focus
              </button>
            )}
          </div>

          {/* Step 11 — node-type filters, built only from types actually present. */}
          {nodeTypes.length > 1 && (
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by node type">
              {nodeTypes.map((t) => {
                const on = activeTypes.has(t);
                const color = graph.nodes.find((n) => n.type === t)?.color ?? "#999";
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() =>
                      setTypeFilter((prev) => {
                        const base = prev ?? new Set(nodeTypes);
                        const next = new Set(base);
                        if (next.has(t)) next.delete(t);
                        else next.add(t);
                        return next;
                      })
                    }
                    className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                      on ? "border-border text-ink" : "border-border/50 text-ink-soft opacity-50"
                    }`}
                  >
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
                    {t}
                  </button>
                );
              })}
            </div>
          )}

          <div className="flex flex-col gap-3 lg:flex-row">
            <div className="min-w-0 flex-1">
              {viewMode === "graph" && (
                <GraphCanvas
                  nodes={filteredNodes}
                  edges={filteredEdges}
                  selectedId={selectedId}
                  matchIds={matchIds}
                  focusId={focusId}
                  onSelect={setSelectedId}
                />
              )}
              {viewMode === "structure" && (
                <StructureTree
                  nodes={filteredNodes}
                  edges={filteredEdges}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
              )}
              {viewMode === "table" && (
                <NodesTable
                  nodes={filteredNodes}
                  edges={filteredEdges}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                />
              )}
              {graph.truncated && (
                <p className="mt-1.5 text-[10px] text-ink-soft">
                  Showing the {graph.nodes.length} most-connected of {graph.total_nodes} nodes — the
                  full graph is too large to render legibly.
                </p>
              )}
            </div>

            {selectedNode && (
              <NodeInspector
                node={selectedNode}
                runId={runId}
                connections={connections}
                onClose={() => setSelectedId(null)}
                onFocus={() => setFocusId(selectedNode.id)}
              />
            )}
          </div>
        </div>
      </details>

      {/* Terminal status — reached only once metrics/graph loaded without
          error, so this is a real state, not a decorative badge. */}
      <div className="flex items-center gap-1.5 rounded-lg border border-status-completed/30 bg-status-completed-bg px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-status-completed">
        <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
        Index ready <span className="mx-0.5 opacity-60">·</span>
        <span className="font-medium normal-case tracking-normal">
          Reusable by downstream agents
        </span>
      </div>
    </section>
  );
}
