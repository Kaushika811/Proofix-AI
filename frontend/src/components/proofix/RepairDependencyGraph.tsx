import { useId, useState } from "react";
import { repairTitle } from "./SingleRepairMap";
import type { RepairPlan } from "./repairPlanTypes";

/** Layout only; never changes the supplied execution order. */
export function RepairDependencyGraph({
  plan,
  onSelect,
  selectedPath,
  highlighted,
}: {
  plan: RepairPlan;
  onSelect: (id: string) => void;
  selectedPath?: string | null;
  highlighted?: Set<string>;
}) {
  const marker = useId().replace(/:/g, "");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [limit, setLimit] = useState(12);
  const nodes = plan.steps;
  const byId = new Map(nodes.map((s) => [s.issueId, s]));
  const levels = new Map<string, number>();
  const children = new Map<string, string[]>();
  const degrees = new Map<string, number>();
  let missing = 0;
  for (const step of nodes) {
    const parents = [
      ...new Set([...step.dependsOn, ...step.incomingEdges.map((e) => e.fromIssue)]),
    ];
    degrees.set(step.issueId, parents.filter((id) => byId.has(id)).length);
    for (const id of parents) {
      if (!byId.has(id)) {
        missing++;
        continue;
      }
      children.set(id, [...(children.get(id) ?? []), step.issueId]);
    }
  }
  const queue = nodes.filter((s) => degrees.get(s.issueId) === 0).map((s) => s.issueId);
  queue.forEach((id) => levels.set(id, 0));
  for (let i = 0; i < queue.length; i++) {
    const id = queue[i];
    for (const child of children.get(id) ?? []) {
      levels.set(child, Math.max(levels.get(child) ?? 0, levels.get(id)! + 1));
      degrees.set(child, degrees.get(child)! - 1);
      if (degrees.get(child) === 0) queue.push(child);
    }
  }
  if (queue.length !== nodes.length)
    return (
      <p className="ag-warning" role="alert">
        Dependency cycle detected. A valid DAG cannot be displayed; inspect the recorded
        dependencies below.
      </p>
    );
  const dense = nodes.length > 24;
  const maxLevel = Math.max(0, ...levels.values());
  const bandSize = Math.max(1, Math.ceil((maxLevel + 1) / 12));
  const groups = new Map<string, typeof nodes>();
  for (const step of nodes) {
    const id = dense ? `band-${Math.floor(levels.get(step.issueId)! / bandSize)}` : step.issueId;
    groups.set(id, [...(groups.get(id) ?? []), step]);
  }
  const rows = new Map<number, number>();
  const placed = [...groups].map(([id, steps]) => {
    const l = Math.floor(levels.get(steps[0].issueId)! / bandSize);
    const column = rows.get(l) ?? 0;
    rows.set(l, column + 1);
    return { id, steps, x: 24 + column * 240, y: 30 + l * 118 };
  });
  const positions = new Map(placed.flatMap((p) => p.steps.map((s) => [s.issueId, p] as const)));
  const links = new Map<string, { from: (typeof placed)[number]; to: (typeof placed)[number] }>();
  for (const [parent, dependents] of children)
    for (const child of dependents) {
      const from = positions.get(parent)!;
      const to = positions.get(child)!;
      if (from !== to) links.set(`${from.id}:${to.id}`, { from, to });
    }
  const width = Math.max(280, ...placed.map((p) => p.x + 224));
  const height = Math.max(180, ...placed.map((p) => p.y + 100));
  const group = placed.find((p) => p.id === expanded);
  return (
    <>
      <div className="ag-viewport" tabIndex={0} aria-label="Repair dependency graph">
        <div className="ag-canvas" style={{ width, height }}>
          <svg width={width} height={height} aria-hidden="true">
            <defs>
              <marker id={marker} markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto">
                <path d="M0,0 L6,3 L0,6" fill="var(--color-status-running)" />
              </marker>
            </defs>
            {[...links].map(([id, { from: a, to: p }]) => (
              <path
                key={id}
                d={`M${a.x + 100},${a.y + 70} C${a.x + 100},${a.y + 95} ${p.x + 100},${p.y - 25} ${p.x + 100},${p.y}`}
                fill="none"
                stroke="var(--color-status-running)"
                markerEnd={`url(#${marker})`}
              />
            ))}
          </svg>
          {placed.map((p) => (
            <button
              className={`ag-dag-node ${p.steps.some((s) => highlighted?.has(s.issueId)) ? "ag-selected" : ""}`}
              aria-pressed={p.steps.some((s) => s.issueId === selectedPath)}
              key={p.id}
              style={{ left: p.x, top: p.y }}
              onClick={() => {
                if (p.steps.length === 1) onSelect(p.steps[0].issueId);
                else {
                  setExpanded(p.id);
                  setLimit(12);
                }
              }}
            >
              <span>
                {p.steps.length > 1 ? "DEPENDENCY GROUP" : `STEP ${p.steps[0].position}`} ·{" "}
                {p.steps.every((s) => s.evidence?.execution === "applied")
                  ? "FILES APPLIED"
                  : p.steps.some((s) => s.evidence?.appliedFiles.length)
                    ? "PARTIAL FILE COVERAGE"
                    : "PROPOSED"}
              </span>
              <strong>
                {p.steps.length > 1
                  ? `${p.steps.length} candidates · expand`
                  : repairTitle(p.steps[0])}
              </strong>
              <small>
                {p.steps.length === 1
                  ? (p.steps[0].files[0] ?? "Target unavailable")
                  : "Select to explore candidates"}
              </small>
            </button>
          ))}
        </div>
      </div>
      {missing > 0 && (
        <p className="ag-warning">
          {missing} prerequisite references are missing from this plan. Graph is incomplete.
        </p>
      )}
      {group && (
        <div className="ag-inspector">
          <h5>{group.steps.length} candidates in this dependency group</h5>
          <div className="ag-file-list">
            {group.steps.slice(0, limit).map((step) => (
              <button key={step.issueId} onClick={() => onSelect(step.issueId)}>
                {step.position}. {step.issueId}
              </button>
            ))}
          </div>
          {limit < group.steps.length && (
            <button onClick={() => setLimit(limit + 12)}>Show next 12 candidates in group</button>
          )}
        </div>
      )}
      <p className="ag-caveat">
        Arrows represent recorded prerequisites. Same-level cards have no recorded ordering between
        them; parallel execution is not established.
        {dense
          ? " Groups summarize dependency depth, not execution phases. Expand to inspect all candidates and internal prerequisites."
          : ""}
      </p>
    </>
  );
}
