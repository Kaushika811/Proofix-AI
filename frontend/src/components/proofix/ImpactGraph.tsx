import { useId, useMemo, useState } from "react";
import { ArrowRight, FileCode2, Folder, GitBranch, Search } from "lucide-react";
import type { BlastImpact, BlastScopeFile } from "./blastTypes";
import "./agentGraphs.css";

const directory = (path: string) =>
  path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "Repository root";

/** File-level traversal only: arrow direction represents A5 exploration, not a call graph. */
export function ImpactGraph({
  impact,
  onSelect,
  selectedPath,
}: {
  impact: BlastImpact;
  onSelect: (file: BlastScopeFile) => void;
  selectedPath?: string | null;
}) {
  const marker = useId().replace(/:/g, "");
  const [edgeIndex, setEdgeIndex] = useState<string | null>(null);
  const [expandedCanvas, setExpandedCanvas] = useState(false);
  const [eligibility, setEligibility] = useState("all");
  const [depth, setDepth] = useState("all");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(12);
  const [exploreAll, setExploreAll] = useState(false);
  const [localPath, setLocalPath] = useState<string | null>(null);
  const [trace, setTrace] = useState(false);
  const origin = impact.origin?.resolvedPath ?? impact.origin?.normalizedPath ?? impact.origins[0];
  const scopeByPath = useMemo(() => new Map(impact.scope.map((f) => [f.path, f])), [impact.scope]);
  const focusPath = localPath ?? selectedPath;
  const focused =
    (focusPath ? scopeByPath.get(focusPath) : undefined) ??
    [...impact.scope]
      .filter((f) => f.hopCount !== null)
      .sort((a, b) => (b.hopCount ?? 0) - (a.hopCount ?? 0))[0];
  const traced = useMemo(() => {
    const paths = new Set<string>();
    let cursor = focused;
    while (cursor && !paths.has(cursor.path)) {
      paths.add(cursor.path);
      const parent: BlastScopeFile | undefined = cursor.reachedVia
        ? scopeByPath.get(cursor.reachedVia)
        : undefined;
      if (!parent || !impact.edges.some((e) => e.from === parent.path && e.to === cursor?.path))
        break;
      cursor = parent;
    }
    return paths;
  }, [focused, scopeByPath, impact.edges]);
  const visible = impact.scope.filter(
    (f) =>
      f.path === origin ||
      ((depth === "all" || (f.hopCount !== null && f.hopCount <= Number(depth))) &&
        (eligibility === "all" ||
          (eligibility === "auto" ? f.autoPatchable : f.humanReviewRequired)) &&
        (!query || f.path.toLowerCase().includes(query.toLowerCase())) &&
        (!trace || traced.has(f.path))),
  );
  const pathMode = !exploreAll && !query && eligibility === "all" && depth === "all";
  const dense = visible.length > 10;
  const groups = new Map<string, BlastScopeFile[]>();
  for (const file of visible) {
    const key = pathMode
      ? traced.has(file.path) || file.path === origin
        ? file.path
        : `branch:${file.hopCount ?? "?"}`
      : file.path === origin
        ? file.path
        : dense
          ? `${file.hopCount ?? "?"}:${visible.length > 200 ? "all modules" : directory(file.path)}`
          : file.path;
    groups.set(key, [...(groups.get(key) ?? []), file]);
  }
  const hopOrder = [...new Set(visible.map((f) => f.hopCount ?? -1))].sort(
    (a, b) => (a === -1 ? Infinity : a) - (b === -1 ? Infinity : b),
  );
  const lanes = new Map<number, number>();
  const nodes = [...groups.entries()]
    .sort(([a], [b]) => Number(a.startsWith("branch:")) - Number(b.startsWith("branch:")))
    .map(([id, files]) => {
      const hop = files[0].hopCount ?? -1;
      const row = lanes.get(hop) ?? 0;
      lanes.set(hop, row + 1);
      return { id, files, x: 24 + hopOrder.indexOf(hop) * 192, y: 64 + row * 92 };
    });
  const fileNode = new Map(nodes.flatMap((n) => n.files.map((f) => [f.path, n] as const)));
  const edgeGroups = new Map<string, (typeof impact.edges)[number]>();
  for (const edge of impact.edges) {
    const from = fileNode.get(edge.from);
    const to = fileNode.get(edge.to);
    if (
      from &&
      to &&
      from !== to &&
      (!pathMode || (!from.id.startsWith("branch:") && !to.id.startsWith("branch:")))
    )
      edgeGroups.set(`${from.id}:${to.id}:${edge.basis}`, edge);
  }
  const edges = [...edgeGroups.values()];
  const width = Math.max(300, ...nodes.map((n) => n.x + 176));
  const height = Math.max(190, ...nodes.map((n) => n.y + 112));
  const selectedGroup = nodes.find((n) => n.id === expanded);
  return (
    <div
      className={`agent-graph dependency-trace ${pathMode ? "trace-path-mode" : ""}`}
      role="group"
      aria-label="Change-centered impact graph"
    >
      <div className="ag-heading">
        <div>
          <span className="ag-eyebrow">A5 / CHANGE IMPACT</span>
          <h4>Dependency path explorer</h4>
          <p>Explore what could be affected through recorded import relationships.</p>
        </div>
        <GitBranch size={24} aria-hidden="true" />
      </div>
      <div className="trace-stats">
        <span>
          <strong>{impact.scope.length}</strong> In scope
        </span>
        <span>
          <strong>{impact.scope.filter((f) => f.autoPatchable).length}</strong> Auto-patch eligible
        </span>
        <span>
          <strong>{impact.scope.filter((f) => f.humanReviewRequired).length}</strong> Needs review
        </span>
      </div>
      <div className="ag-toolbar">
        <label className="ag-search">
          <Search size={14} aria-hidden="true" />
          <input
            aria-label="Find affected file"
            placeholder="Find a file…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label>
          Depth{" "}
          <select
            aria-label="Dependency depth"
            value={depth}
            onChange={(e) => setDepth(e.target.value)}
          >
            <option value="all">All recorded</option>
            {Array.from({ length: impact.maxHop }, (_, i) => (
              <option key={i} value={i + 1}>
                {i + 1} hop{i ? "s" : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Scope{" "}
          <select
            aria-label="Patch eligibility filter"
            value={eligibility}
            onChange={(e) => {
              setEligibility(e.target.value);
              setExpanded(null);
            }}
          >
            <option value="all">All in scope</option>
            <option value="auto">Auto-patch eligible</option>
            <option value="review">Needs review</option>
          </select>
        </label>
        <button disabled={!focused} aria-pressed={trace} onClick={() => setTrace(!trace)}>
          Trace selected path
        </button>
        <button aria-expanded={expandedCanvas} onClick={() => setExpandedCanvas(!expandedCanvas)}>
          {expandedCanvas ? "Compact view" : "Expand canvas"}
        </button>
        <span>
          {visible.length} / {impact.scope.length} files
        </span>
      </div>
      <div className="trace-overview" aria-label="Scope by recorded hop">
        {hopOrder.map((hop) => {
          const count = visible.filter((f) => (f.hopCount ?? -1) === hop).length;
          return (
            <button
              key={hop}
              onClick={() => {
                const file = visible.find((f) => (f.hopCount ?? -1) === hop);
                if (file) {
                  setLocalPath(file.path);
                  onSelect(file);
                }
                setExploreAll(false);
                setExpanded(null);
              }}
              title="Follow a recorded path to this depth"
            >
              <span>{hop === 0 ? "Source" : hop === -1 ? "Unknown depth" : `Hop ${hop}`}</span>
              <strong>{count}</strong>
              <span className="trace-meter">
                <i
                  style={{ width: `${Math.max(4, (count / Math.max(1, visible.length)) * 100)}%` }}
                />
              </span>
            </button>
          );
        })}
        <button aria-pressed={exploreAll} onClick={() => setExploreAll(!exploreAll)}>
          {exploreAll ? "Focus path" : "Explore full scope"}
        </button>
      </div>
      <p className="ag-caveat">
        File-level analysis · import matches may be approximate · repository-wide completeness is
        not measured. Arrows show traversal direction.
      </p>
      <div
        className="ag-viewport"
        style={{ maxHeight: expandedCanvas ? 640 : 420 }}
        tabIndex={0}
        aria-label="Scrollable impact graph"
      >
        <div className="ag-canvas" style={{ width, height }}>
          <svg width={width} height={height} aria-hidden="true">
            <defs>
              <marker id={marker} markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
                <path d="M0,0 L6,3 L0,6" fill="var(--color-status-running)" />
              </marker>
            </defs>
            <g data-testid="impact-traversal-edges">
              {edges.map((edge, i) => {
                const a = fileNode.get(edge.from)!;
                const b = fileNode.get(edge.to)!;
                if (a === b) return null;
                return (
                  <path
                    key={i}
                    d={`M${a.x + 160},${a.y + 32} H${a.x + 176} V${b.y + 32} H${b.x}`}
                    opacity={
                      focused && !trace && !(traced.has(edge.from) && traced.has(edge.to))
                        ? 0.15
                        : 0.8
                    }
                    stroke={
                      edge.basis === "name_contains"
                        ? "var(--color-status-retry)"
                        : "var(--color-status-running)"
                    }
                    strokeDasharray={edge.basis === "name_contains" ? "4 4" : undefined}
                    fill="none"
                    markerEnd={`url(#${marker})`}
                  />
                );
              })}
            </g>
          </svg>
          {pathMode &&
            edges.map((edge) => {
              const a = fileNode.get(edge.from)!;
              const key = `${edge.from}|${edge.to}|${edge.basis}`;
              return (
                <button
                  key={key}
                  className="trace-edge-control"
                  style={{ left: a.x + 162, top: a.y + 22 }}
                  aria-label={`Connection from ${edge.from} to ${edge.to}`}
                  aria-expanded={edgeIndex === key}
                  onClick={() => setEdgeIndex(edgeIndex === key ? null : key)}
                >
                  ↗
                </button>
              );
            })}
          {[...lanes.keys()].map((hop) => (
            <span
              className="ag-lane-label"
              key={hop}
              style={{ left: 24 + hopOrder.indexOf(hop) * 192 }}
            >
              {hop === 0
                ? "SUSPECTED SOURCE"
                : hop === -1
                  ? "DEPTH UNKNOWN"
                  : hop === 1
                    ? "DIRECT CONNECTIONS"
                    : `FURTHER CONNECTIONS · HOP ${hop}`}
            </span>
          ))}
          {nodes.map((node) => (
            <button
              key={node.id}
              className={`ag-node ${node.id.startsWith("branch:") ? "trace-junction" : "trace-file-tab"} ${node.files.some((f) => f.path === origin) ? "ag-origin" : ""} ${node.files.some((f) => f.path === focused?.path) ? "ag-selected" : ""}`}
              style={{
                left: node.x,
                top: node.y,
                opacity:
                  focused && !trace && !node.files.some((f) => traced.has(f.path)) ? 0.55 : 1,
              }}
              onClick={() => {
                if (node.files.length === 1 && !node.id.startsWith("branch:")) {
                  setLocalPath(node.files[0].path);
                  onSelect(node.files[0]);
                }
                setExpanded(node.id);
                setLimit(12);
              }}
              aria-label={`Inspect ${node.files.length > 1 ? (visible.length > 200 ? `Depth ${node.files[0].hopCount ?? "unknown"}` : directory(node.files[0].path)) : node.files[0].path}`}
            >
              <span className="ag-node-top">
                {node.files.length > 1 ? <Folder size={15} /> : <FileCode2 size={15} />}{" "}
                {node.id.startsWith("branch:")
                  ? `+${node.files.length} other files · explore`
                  : node.files.length > 1
                    ? `${node.files.length} files · expand`
                    : node.files[0].role === "test-only"
                      ? "Related test"
                      : node.files[0].path === origin
                        ? "Repair origin"
                        : "Potential impact"}
              </span>
              <strong>
                {node.id.startsWith("branch:")
                  ? "Branch collection"
                  : node.files.length > 1
                    ? visible.length > 200
                      ? `Depth ${node.files[0].hopCount ?? "unknown"}`
                      : directory(node.files[0].path)
                    : node.files[0].path.split("/").at(-1)}
              </strong>
              <span className="trace-directory" title={node.files[0].path}>
                {node.files.length === 1
                  ? directory(node.files[0].path)
                  : "Select to explore files"}
              </span>
              <span className="ag-node-foot">
                {node.files.some((f) => f.humanReviewRequired)
                  ? "Human review required"
                  : "Inspect dependency evidence"}
                <ArrowRight size={13} />
              </span>
            </button>
          ))}
        </div>
      </div>
      {edgeIndex &&
        edges
          .filter((edge) => `${edge.from}|${edge.to}|${edge.basis}` === edgeIndex)
          .map((edge) => (
            <div className="ag-inspector trace-edge-detail" key={edgeIndex}>
              <code>
                {edge.from} → {edge.to}
              </code>
              <p>
                {edge.basis === "name_contains"
                  ? "Approximate name match — may be a false edge."
                  : "Path suffix match — not verified import resolution."}{" "}
                Recorded traversal: {edge.direction}.
              </p>
              <button onClick={() => setEdgeIndex(null)}>Close connection details</button>
            </div>
          ))}
      {visible.length <= 1 && (
        <p className="ag-caveat">
          No other files match this view. Clear filters to inspect the recorded scope.
        </p>
      )}
      {selectedGroup &&
        (selectedGroup.files.length > 1 || selectedGroup.id.startsWith("branch:")) && (
          <div className="ag-inspector">
            <h5>
              {visible.length > 200
                ? "Files at this depth"
                : directory(selectedGroup.files[0].path)}{" "}
              · {selectedGroup.files.length} files
            </h5>
            <div className="ag-file-list">
              {selectedGroup.files.slice(0, limit).map((f) => (
                <button
                  key={f.path}
                  onClick={() => {
                    setLocalPath(f.path);
                    onSelect(f);
                  }}
                  aria-pressed={selectedPath === f.path}
                >
                  {f.path}
                  <ArrowRight size={13} />
                </button>
              ))}
            </div>
            {limit < selectedGroup.files.length && (
              <button onClick={() => setLimit(limit + 12)}>Show 12 more files</button>
            )}
          </div>
        )}
      {focused && (
        <details className="ag-inspector trace-details" key={focused.path}>
          <summary className="ag-eyebrow">Why is this file here?</summary>
          <div className="trace-details-content">
            <h5>{focused.path}</h5>
            <div className="trace-breadcrumb" aria-label="Recorded dependency path">
              {[...traced].reverse().map((path, index) => (
                <span key={path}>
                  {index > 0 && <ArrowRight size={12} aria-hidden="true" />}
                  <code>{path}</code>
                </span>
              ))}
            </div>
            <p>
              {focused.reachedVia
                ? `Reached through ${focused.reachedVia}`
                : focused.hopCount === 0
                  ? "Traversal origin; no incoming step recorded."
                  : "No predecessor recorded; a complete source path is unavailable."}
            </p>
            {impact.edges
              .filter((e) => e.to === focused.path || e.from === focused.path)
              .map((e, i) => (
                <div className="ag-evidence" key={i}>
                  <code>
                    {e.from} → {e.to}
                  </code>
                  <span>
                    {e.direction === "forward"
                      ? "Imports matched dependency"
                      : "Reached a possible importer"}{" "}
                    ·{" "}
                    {e.basis === "name_contains"
                      ? "Name match only — may be a false edge"
                      : "Path suffix match"}
                  </span>
                </div>
              ))}
            <p>
              Patch eligibility:{" "}
              {focused.autoPatchable ? "in auto-patch scope" : "outside auto-patch scope"}. Planned
              modification: unavailable in this analysis.
            </p>
          </div>
        </details>
      )}
    </div>
  );
}
