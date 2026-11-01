import { useMemo, useState } from "react";
import { FileCode2, Folder, Search, ArrowUpRight, ListOrdered } from "lucide-react";
import type { RepairPlan, RepairStep } from "./repairPlanTypes";
import "./repairScopeEditor.css";

const folder = (path: string) =>
  path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "Repository root";
const title = (step: RepairStep) =>
  step.why?.kind === "static_finding" && step.why.message
    ? step.why.message
    : step.why?.kind === "cve"
      ? `Address advisory in ${step.why.package ?? "dependency"}`
      : `Plan for ${step.files[0]?.split("/").at(-1) ?? step.issueId}`;

export function RepairScopeEditor({ plan }: { plan: RepairPlan }) {
  const [selection, setSelection] = useState({
    file: plan.steps[0]?.files[0] ?? "",
    id: plan.steps[0]?.issueId,
  });
  const [stepLimit, setStepLimit] = useState(8);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(40);
  const files = useMemo(() => {
    const map = new Map<string, RepairStep[]>();
    for (const step of plan.steps)
      for (const file of step.files) map.set(file, [...(map.get(file) ?? []), step]);
    return map;
  }, [plan]);
  const matches = [...files.keys()]
    .filter((file) => file.toLowerCase().includes(query.toLowerCase()))
    .sort();
  const groups = new Map<string, string[]>();
  for (const file of matches.slice(0, limit))
    groups.set(folder(file), [...(groups.get(folder(file)) ?? []), file]);
  const active = plan.steps.find((step) => step.issueId === selection.id) ?? plan.steps[0];
  const selectStep = (step: RepairStep) =>
    setSelection({ file: step.files[0] ?? "", id: step.issueId });
  const fileSteps = files.get(selection.file) ?? (active ? [active] : []);
  if (!active) return null;
  return (
    <div className="repair-scope-editor" aria-label="Repair scope editor">
      <header className="rse-header">
        <span>
          <ListOrdered size={15} /> REPAIR BLUEPRINT
        </span>
        <span>
          {files.size} files · {plan.steps.length} planned steps
        </span>
      </header>
      <div className="rse-layout">
        <aside className="rse-sidebar" aria-label="Planned files">
          <label className="rse-search">
            <Search size={13} />
            <input
              aria-label="Search planned files"
              placeholder="Find a planned file…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setLimit(40);
              }}
            />
          </label>
          <div className="rse-files">
            {[...groups].map(([directory, paths]) => (
              <details key={directory} open>
                <summary>
                  <Folder size={13} />
                  <span>{directory}</span>
                  <small>{paths.length}</small>
                </summary>
                {paths.map((file) => (
                  <button
                    key={file}
                    title={file}
                    aria-pressed={selection.file === file}
                    onClick={() => {
                      setSelection({ file, id: files.get(file)![0].issueId });
                      setStepLimit(8);
                    }}
                  >
                    <FileCode2 size={13} />
                    <span>{file.split("/").at(-1)}</span>
                    <small>{files.get(file)!.length} steps</small>
                  </button>
                ))}
              </details>
            ))}
            {!matches.length && <p className="rse-muted">No planned files match.</p>}
            {matches.length > limit && (
              <button className="rse-more" onClick={() => setLimit(limit + 40)}>
                Show 40 more · {matches.length - limit} remaining
              </button>
            )}
            {plan.steps
              .filter((step) => !step.files.length)
              .map((step) => (
                <button key={step.issueId} onClick={() => selectStep(step)}>
                  Step {step.position} · Target unspecified
                </button>
              ))}
          </div>
        </aside>
        <div className="rse-inspector">
          <div className="rse-path">
            <FileCode2 size={14} />
            <code>{selection.file || "Target file not recorded"}</code>
          </div>
          <div
            className="rse-map-scroll"
            tabIndex={0}
            aria-label="Planned step to file relationships"
          >
            <div className="rse-map">
              <div className="rse-map-target">
                <span className="rse-label">PLANNED TARGET</span>
                <div className="rse-target-card">
                  <FileCode2 size={24} />
                  <strong>{selection.file.split("/").at(-1) || "Unspecified file"}</strong>
                  <small>{fileSteps.length} planned steps</small>
                </div>
                <span className="rse-map-caption">Connected steps target this file</span>
              </div>
              <div className="rse-map-steps" aria-label="Steps for selected file">
                {fileSteps.slice(0, stepLimit).map((step) => (
                  <button
                    className="rse-step-node"
                    key={step.issueId}
                    aria-pressed={step.issueId === active.issueId}
                    onClick={() => setSelection({ ...selection, id: step.issueId })}
                  >
                    <span className="rse-node-number">
                      {String(step.position).padStart(2, "0")}
                    </span>
                    <span>
                      <small>
                        {step.why?.kind === "cve" ? "DEPENDENCY ADVISORY" : "REPAIR CANDIDATE"}
                      </small>
                      <strong>{title(step)}</strong>
                      <em>
                        {step.dependsOn.length} prerequisites · {step.conflictsWith.length}{" "}
                        shared-file links
                      </em>
                    </span>
                  </button>
                ))}
                {fileSteps.length > stepLimit && (
                  <button className="rse-more" onClick={() => setStepLimit(stepLimit + 8)}>
                    Show {Math.min(8, fileSteps.length - stepLimit)} more steps
                  </button>
                )}
              </div>
            </div>
          </div>
          <div className="rse-selected-heading">
            <span className="rse-label">
              SELECTED STEP {String(active.position).padStart(2, "0")}
            </span>
            <code className="rse-muted">{active.issueId}</code>
          </div>
          <details className="rse-evidence" key={active.issueId}>
            <summary>
              Explain this decision <ArrowUpRight size={13} />
            </summary>
            {active.why?.kind === "static_finding" ? (
              <>
                <p>{active.why.message || "Finding description not recorded."}</p>
                <p className="rse-muted">
                  Source: static finding
                  {active.why.tools.length ? ` · ${active.why.tools.join(", ")}` : ""}
                </p>
              </>
            ) : active.why?.kind === "cve" ? (
              <p>
                Dependency advisory · {active.why.package ?? "Package unspecified"} · Installed
                version: {active.why.installedVersion ?? "unavailable"}
              </p>
            ) : (
              <p>No supporting finding was linked to this step.</p>
            )}
          </details>
          <section className="rse-section">
            <h5>PREREQUISITES</h5>
            <div className="rse-prerequisites">
              {active.dependsOn.length ? (
                active.dependsOn.map((id) => {
                  const dependency = plan.steps.find((step) => step.issueId === id);
                  return dependency ? (
                    <button key={id} onClick={() => selectStep(dependency)} title={id}>
                      Step {dependency.position} · {dependency.files[0]?.split("/").at(-1) ?? id}
                      <ArrowUpRight size={12} />
                    </button>
                  ) : (
                    <span key={id}>{id} · Step unavailable</span>
                  );
                })
              ) : (
                <p className="rse-muted">No prerequisite recorded.</p>
              )}
            </div>
          </section>
          {!!active.conflictsWith.length && (
            <details className="rse-evidence" key={`coord-${active.issueId}`}>
              <summary>{active.conflictsWith.length} shared-file relationships · inspect</summary>
              <div className="rse-prerequisites">
                {active.conflictsWith.map((id) => {
                  const step = plan.steps.find((s) => s.issueId === id);
                  return step ? (
                    <button key={id} onClick={() => selectStep(step)}>
                      Step {step.position}
                      <ArrowUpRight size={12} />
                    </button>
                  ) : (
                    <code key={id}>{id}</code>
                  );
                })}
              </div>
            </details>
          )}
          <p className="rse-note">
            Lines connect planned steps to their target file, not execution order. Plan-wide
            constraints and acceptance criteria are available below.
          </p>
        </div>
      </div>
    </div>
  );
}
