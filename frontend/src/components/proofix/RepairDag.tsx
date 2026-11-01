import { useState } from "react";
import { ArrowDown, GitBranch, ChevronDown } from "lucide-react";
import type { RepairPlan, RepairStep } from "./repairPlanTypes";
import "./agentGraphs.css";
import { SingleRepairMap, repairTitle } from "./SingleRepairMap";
import { RepairDependencyGraph } from "./RepairDependencyGraph";

export function RepairDag({ plan }: { plan: RepairPlan }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(12);
  const byId = new Map(plan.steps.map((s) => [s.issueId, s]));
  const prerequisites = new Set<string>();
  const pending = selected ? [selected] : [];
  while (pending.length) {
    const id = pending.pop()!;
    if (prerequisites.has(id)) continue;
    prerequisites.add(id);
    const step = byId.get(id);
    pending.push(
      ...(step?.dependsOn ?? []),
      ...(step?.incomingEdges.map((e) => e.fromIssue) ?? []),
    );
  }
  const filtered = plan.steps.filter(
    (s) =>
      !query ||
      `${s.issueId} ${s.files.join(" ")} ${repairTitle(s)}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const shown = filtered.slice(0, limit);
  const orderDiffers =
    plan.deterministicOrder.length > 0 &&
    plan.steps.filter((s) => s.ordered).some((s, i) => plan.deterministicOrder[i] !== s.issueId);
  return (
    <div className="agent-graph" role="group" aria-label="Repair impact map">
      <div className="ag-heading">
        <div>
          <span className="ag-eyebrow">A6 / PROPOSED REPAIR DAG</span>
          <h4>Every change has a reason.</h4>
          <p>Inspect repair candidates, prerequisites, and shared-file coordination.</p>
        </div>
        <GitBranch size={24} aria-hidden="true" />
      </div>
      <div className="ag-toolbar">
        <input
          aria-label="Find repair step"
          placeholder="Find a step or file…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setLimit(12);
          }}
        />
        <span>
          {filtered.length} candidates · {plan.totalDependencyEdges} dependencies
        </span>
        {selected && (
          <button onClick={() => setSelected(null)}>Clear prerequisite highlight</button>
        )}
      </div>
      <details className="ag-authority">
        <summary>Execution scope & evidence limits</summary>
        <p className="ag-caveat">
          {plan.executionAuthority.note} File application evidence is shown when recorded. Test
          results refer to the whole patch bundle.
        </p>
      </details>
      {orderDiffers && (
        <p className="ag-warning">
          Proposed order differs from the dependency order. Inspect prerequisites before applying
          changes.
        </p>
      )}
      {plan.steps.length === 1 ? (
        <SingleRepairMap
          step={plan.steps[0]}
          selected={selected === plan.steps[0].issueId}
          onSelect={setSelected}
        />
      ) : (
        <RepairDependencyGraph
          plan={plan}
          selectedPath={selected}
          highlighted={prerequisites}
          onSelect={(id) => {
            setSelected(id);
            setQuery("");
            setLimit(Math.max(12, plan.steps.findIndex((s) => s.issueId === id) + 1));
          }}
        />
      )}
      {query && (
        <div className="ag-file-list ag-search-results">
          {shown.map((step) => (
            <button
              key={step.issueId}
              onClick={() => {
                setSelected(step.issueId);
                setQuery("");
              }}
            >
              {repairTitle(step)} · {step.files.join(", ")}
            </button>
          ))}
        </div>
      )}
      {!selected && (
        <p className="ag-caveat">
          Select a change in the graph to inspect its rationale, dependencies, and evidence.
        </p>
      )}
      <div className="ag-plan">
        {plan.steps
          .filter((step) => step.issueId === selected)
          .map((step) => {
            const parents = [
              ...new Set([...step.dependsOn, ...step.incomingEdges.map((e) => e.fromIssue)]),
            ];
            const shared = plan.steps
              .filter(
                (other) =>
                  other.issueId !== step.issueId && other.files.some((f) => step.files.includes(f)),
              )
              .map((s) => s.issueId);
            return (
              <article
                key={step.issueId}
                className={`ag-change ${selected && prerequisites.has(step.issueId) ? "ag-selected" : ""}`}
              >
                <div className="ag-step-number">{String(step.position).padStart(2, "0")}</div>
                <div className="ag-change-body">
                  {parents.length > 0 && (
                    <div className="ag-prerequisites">
                      <ArrowDown size={14} aria-hidden="true" />
                      <span>Depends on</span>
                      {parents.map((id) => (
                        <button
                          key={id}
                          onClick={() => {
                            setSelected(id);
                            setQuery("");
                            setLimit(
                              Math.max(12, plan.steps.findIndex((s) => s.issueId === id) + 1),
                            );
                          }}
                          title={
                            step.incomingEdges.find((e) => e.fromIssue === id)?.reason ||
                            "Ordering reason unavailable"
                          }
                        >
                          {id}
                          {!byId.has(id) ? " (not in plan)" : ""}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="ag-node-top">
                    <span className="ag-status">
                      {step.evidence?.execution === "applied"
                        ? "Files applied"
                        : step.evidence?.execution === "partially_applied"
                          ? "Some files applied"
                          : "Proposed"}
                    </span>
                    {step.isHandoffTarget && <span>Handoff identifier</span>}
                    {!step.ordered && <span className="ag-warning">Order not assigned</span>}
                  </div>
                  <h5>{repairTitle(step)}</h5>
                  <p className="ag-issue-id">{step.issueId}</p>
                  <div className="ag-paths">
                    {step.files.length ? (
                      step.files.map((f) => <code key={f}>{f}</code>)
                    ) : (
                      <span>Target files unavailable</span>
                    )}
                  </div>
                  <button
                    className="ag-trace"
                    onClick={() => setSelected(selected === step.issueId ? null : step.issueId)}
                    aria-pressed={selected === step.issueId}
                  >
                    <GitBranch size={13} /> Highlight prerequisites
                  </button>
                  <details open>
                    <summary>
                      Inspect change & ordering <ChevronDown size={14} />
                    </summary>
                    <div className="ag-detail-grid">
                      <div>
                        <span className="ag-eyebrow">WHY THIS ORDER</span>
                        {parents.length ? (
                          parents.map((id) => (
                            <p key={id}>
                              <strong>{id}</strong> →{" "}
                              {step.incomingEdges.find((e) => e.fromIssue === id)?.reason ||
                                "Dependency recorded; explanation unavailable."}
                            </p>
                          ))
                        ) : (
                          <p>
                            No prerequisites recorded. This does not establish that changes can
                            execute independently.
                          </p>
                        )}
                      </div>
                      <div>
                        <span className="ag-eyebrow">CHANGE EVIDENCE</span>
                        <p>
                          Recorded file writes, expected outcomes, and applied diffs appear below
                          when available.
                        </p>
                        <p>
                          File coverage does not establish that this repair candidate is correct.
                          Validation is scoped to the whole patch bundle.
                        </p>
                      </div>
                    </div>
                    {shared.length > 0 && (
                      <p className="ag-warning">
                        Shared files with {shared.join(", ")}. Coordinate these changes.
                      </p>
                    )}
                    {step.evidence?.changes.map((change) => (
                      <div key={change.file} className="ag-evidence">
                        <strong>
                          {change.file}
                          {change.targetFunction ? ` · ${change.targetFunction}` : ""}
                        </strong>
                        <p>{change.expectedOutcome || "Expected outcome not recorded"}</p>
                        <details>
                          <summary>Inspect applied diff</summary>
                          <pre style={{ overflowX: "auto", maxHeight: 300 }}>{change.diff}</pre>
                        </details>
                      </div>
                    ))}
                    {step.evidence && <p className="ag-caveat">{step.evidence.note}</p>}
                    <div className="ag-validation">
                      <span>Execution: {step.evidence?.execution ?? "unavailable"}</span>
                      <span>
                        Validation: {step.evidence?.validation ?? "unavailable"}
                        {step.evidence?.validationScope ? " (whole patch bundle)" : ""}
                      </span>
                    </div>
                  </details>
                </div>
              </article>
            );
          })}
      </div>
      {query && shown.length === 0 && (
        <p className="ag-caveat">No repair candidates match this search.</p>
      )}
      {query && filtered.length > limit && (
        <button className="ag-more" onClick={() => setLimit(limit + 12)}>
          Show next 12 candidates ({filtered.length - limit} remaining)
        </button>
      )}
      <p className="ag-caveat">
        Numbering follows the supplied plan. Only recorded prerequisites imply ordering; cards
        without an edge are not automatically independent.
      </p>
    </div>
  );
}
