/**
 * A6 — Repair Impact Map.
 *
 * Every fact drawn here traces to a field on `RepairPlan`
 * (`repairPlanTypes.ts`, sourced from `services/ui_projection.py::build_repair_plan`).
 * Nothing is computed client-side except index-based layout and the LLM-vs-graph
 * position comparison, which reads `deterministicOrder` verbatim — it is A6's
 * own `topological_execution_order`, never recomputed here.
 *
 * The one fact this whole visualization exists to make unmissable:
 * `executionAuthority` — A7 reads exactly `execution_order[0]` as a label and
 * derives its real patch targets from A5/A4. Everything else A6 built is a
 * proposal. Two structural devices carry that, not a sentence:
 *
 * 1. The execution boundary — a hard rule after the one consumed step, solid/
 *    filled above, hollow/dashed/muted below.
 * 2. A step whose `ordered` flag is false never appears on the spine at all —
 *    it did not earn a sequence position, so it is not drawn as having one.
 *
 * Adaptive by design: a 1-step plan has no DAG worth drawing (`RepairFocus`),
 * a multi-step plan gets the full spine + file tracks (`RepairSpine`). Both
 * share the same underlying step/file/order derivations below.
 */
import { useMemo, useState } from "react";
import type { RepairPlan, RepairStep } from "./repairPlanTypes";

const MAX_TRACKS = 6;

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

function whyText(step: RepairStep): { label: string; severity: string; measured: boolean } {
  const why = step.why;
  if (why === null)
    return { label: "No matching A3 finding or A2 CVE record", severity: "", measured: false };
  if (why.kind === "cve") {
    return {
      label: `${why.package ?? "dependency"} — reachable via ${why.reachPath?.join(", ") ?? "an unrecorded path"}`,
      severity: why.severity ?? "",
      measured: why.severity !== null,
    };
  }
  return {
    label: why.message ?? "static finding",
    severity: why.severityMeasured && why.severity !== null ? why.severity.toFixed(2) : "",
    measured: why.severityMeasured && why.severity !== null,
  };
}

function isCveOrigin(step: RepairStep): boolean {
  return step.why?.kind === "cve";
}

// ------------------------------------------------------------- derivations

interface PlanDerived {
  onSpine: RepairStep[];
  offSpine: RepairStep[];
  boundaryIdx: number;
  files: string[];
  overflowFiles: number;
  fileToSteps: Map<string, RepairStep[]>;
  graphPos: Map<string, number> | null; // reindexed within onSpine overlap, only when orderingSource === "llm" with data
}

function useDerived(plan: RepairPlan): PlanDerived {
  return useMemo(() => {
    const onSpine = plan.steps.filter((s) => s.ordered);
    const offSpine = plan.steps.filter((s) => !s.ordered);
    const boundaryIdx = onSpine.findIndex((s) => s.isHandoffTarget);

    const allFiles: string[] = [];
    const seen = new Set<string>();
    const fileToSteps = new Map<string, RepairStep[]>();
    for (const step of onSpine) {
      for (const file of step.files) {
        if (!seen.has(file)) {
          seen.add(file);
          allFiles.push(file);
        }
        const list = fileToSteps.get(file) ?? [];
        list.push(step);
        fileToSteps.set(file, list);
      }
    }
    const files = allFiles.slice(0, MAX_TRACKS);
    const overflowFiles = Math.max(0, allFiles.length - MAX_TRACKS);

    let graphPos: Map<string, number> | null = null;
    if (plan.orderingSource === "llm" && plan.deterministicOrder.length > 0) {
      const onSpineIds = new Set(onSpine.map((s) => s.issueId));
      const overlap = plan.deterministicOrder.filter((id) => onSpineIds.has(id));
      graphPos = new Map(overlap.map((id, idx) => [id, idx + 1]));
    }

    return { onSpine, offSpine, boundaryIdx, files, overflowFiles, fileToSteps, graphPos };
  }, [plan]);
}

// ------------------------------------------------------------- shared bits

function SeverityMark({ measured, severity }: { measured: boolean; severity: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap font-mono text-[9px]"
      title={measured ? `severity ${severity}` : "severity not measured"}
    >
      <span
        className={
          measured
            ? "inline-block h-1.5 w-1.5 rounded-full bg-status-retry"
            : "inline-block h-1.5 w-1.5 rounded-full border border-ink-soft/50"
        }
        aria-hidden
      />
      {measured ? severity : "unmeasured"}
    </span>
  );
}

function StepDetail({
  step,
  plan,
  graphPos,
  modelPos,
}: {
  step: RepairStep;
  plan: RepairPlan;
  graphPos: Map<string, number> | null;
  modelPos: number;
}) {
  const why = whyText(step);
  const showEvidence = step.isHandoffTarget && plan.carriedForward !== null;
  return (
    <div className="col-span-full border-t border-dashed border-border/70 bg-surface-muted/20 px-3 py-2.5 text-[10px]">
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        <div>
          <div className="text-[8px] uppercase tracking-wider text-ink-soft">Repair</div>
          <div className="font-mono text-ink">{step.issueId}</div>
        </div>
        <div>
          <div className="text-[8px] uppercase tracking-wider text-ink-soft">Files</div>
          <div className="font-mono text-ink-soft">
            {step.files.length ? step.files.map((f) => <div key={f}>{f}</div>) : "—"}
          </div>
        </div>
        <div>
          <div className="text-[8px] uppercase tracking-wider text-ink-soft">Why</div>
          <div className="text-ink">{why.label || "not measured"}</div>
        </div>
        <div>
          <div className="text-[8px] uppercase tracking-wider text-ink-soft">Depends on</div>
          <div className="font-mono text-ink-soft">
            {step.incomingEdges.length
              ? step.incomingEdges.map((e) => (
                  <div key={`${e.fromIssue}-${e.reason}`}>
                    {e.fromIssue}
                    {e.reason ? <span className="text-ink-soft/70"> ({e.reason})</span> : null}
                  </div>
                ))
              : "none recorded"}
          </div>
        </div>
        <div>
          <div className="text-[8px] uppercase tracking-wider text-ink-soft">Conflicts</div>
          <div className="font-mono text-status-retry">
            {step.conflictsWith.length ? step.conflictsWith.join(", ") : "none"}
          </div>
        </div>
        <div>
          <div className="text-[8px] uppercase tracking-wider text-ink-soft">Ordering</div>
          <div className="text-ink-soft">
            Model position: <span className="font-mono text-ink">{modelPos}</span>
            {graphPos && (
              <>
                {" · "}Graph position:{" "}
                <span className="font-mono text-ink">
                  {graphPos.get(step.issueId) ?? "not in overlap"}
                </span>
              </>
            )}
          </div>
        </div>
      </div>
      {showEvidence ? (
        <div className="mt-2 grid grid-cols-1 gap-2 border-t border-border/50 pt-2 sm:grid-cols-2">
          <div>
            <div className="text-[8px] uppercase tracking-wider text-ink-soft">
              Acceptance criteria (A5.5)
            </div>
            {plan.carriedForward!.acceptanceCriteria.length ? (
              <ul className="list-disc pl-3 text-ink">
                {plan.carriedForward!.acceptanceCriteria.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            ) : (
              <div className="text-ink-soft">none produced</div>
            )}
          </div>
          <div>
            <div className="text-[8px] uppercase tracking-wider text-ink-soft">
              Patch constraints (A5.5)
            </div>
            {plan.carriedForward!.patchConstraints.length ? (
              <ul className="list-disc pl-3 text-ink">
                {plan.carriedForward!.patchConstraints.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            ) : (
              <div className="text-ink-soft">none produced</div>
            )}
          </div>
        </div>
      ) : (
        <p className="mt-2 border-t border-border/50 pt-2 text-ink-soft/70">
          Acceptance criteria and patch constraints are A5.5&apos;s evidence for A7&apos;s actual
          target — they are not tied to this proposed step.
        </p>
      )}
    </div>
  );
}

// -------------------------------------------------------------- Case 1: single step

function RepairFocus({ plan }: { plan: RepairPlan }) {
  const step = plan.steps[0];
  const why = whyText(step);
  const consumed = step.isHandoffTarget;

  return (
    <div
      role="group"
      aria-label="Repair impact map"
      className="rounded-md border border-border bg-surface p-4"
    >
      <div className="mx-auto flex max-w-sm flex-col items-center text-center">
        <div className="text-[9px] uppercase tracking-widest text-ink-soft">A6 proposal</div>
        <div className="my-1 h-4 w-px bg-border" aria-hidden />
        <div className="w-full rounded border border-border bg-surface-muted/30 p-3 text-left">
          <div className="flex items-center gap-1.5 font-mono text-[11px] font-semibold text-ink">
            <span className="flex h-4 w-4 items-center justify-center rounded-full bg-status-completed-bg text-[8px] text-status-completed">
              {step.position}
            </span>
            {step.issueId}
          </div>
          <div className="mt-1.5 font-mono text-[10px] text-ink-soft">
            {step.files.join(", ") || "—"}
          </div>
          <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-ink">
            <span>{why.label}</span>
            <SeverityMark measured={why.measured} severity={why.severity} />
          </div>
        </div>
        <div className="my-1 h-4 w-px bg-border" aria-hidden />
        <div
          className={`w-full rounded border px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider ${
            consumed
              ? "border-status-completed/50 bg-status-completed-bg/30 text-status-completed"
              : "border-ink-soft/30 bg-surface-muted/20 text-ink-soft"
          }`}
        >
          {consumed ? "A7 input — consumed now" : "Proposed only — not handed to A7"}
        </div>

        <div
          role="separator"
          aria-label="Execution boundary"
          className="mt-3 w-full border-t-2 border-dashed border-status-completed/50 pt-1.5 text-center text-[8px] font-semibold uppercase tracking-widest text-status-completed"
        >
          Execution boundary
        </div>
        <div className="mt-1 text-[9px] uppercase tracking-wider text-ink-soft">
          No other steps currently proposed
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------- Case 2: spine + tracks

function RepairSpine({ plan, derived }: { plan: RepairPlan; derived: PlanDerived }) {
  const { onSpine, offSpine, boundaryIdx, files, overflowFiles, fileToSteps, graphPos } = derived;
  const [hoveredStep, setHoveredStep] = useState<string | null>(null);
  const [hoveredFile, setHoveredFile] = useState<string | null>(null);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);

  const sharedFiles = useMemo(
    () => files.filter((f) => (fileToSteps.get(f)?.length ?? 0) > 1),
    [files, fileToSteps],
  );

  const gridTemplateColumns = `1.5rem minmax(11rem,1fr) repeat(${files.length}, 1.75rem)${
    overflowFiles > 0 ? " auto" : ""
  }`;

  // Related to the *hovered* step specifically, both directions: what it
  // conflicts with / depends on, and what depends on it — computed once from
  // the hovered step's own real fields, not from each row's own fields (a
  // step's dependency source would otherwise never light up when its
  // dependent is hovered, since the source has no incoming edge of its own).
  const hoveredRelated = useMemo(() => {
    const ids = new Set<string>();
    if (hoveredStep === null) return ids;
    const hoveredObj = onSpine.find((s) => s.issueId === hoveredStep);
    if (!hoveredObj) return ids;
    for (const id of hoveredObj.conflictsWith) ids.add(id);
    for (const edge of hoveredObj.incomingEdges) ids.add(edge.fromIssue);
    for (const s of onSpine) {
      if (s.incomingEdges.some((e) => e.fromIssue === hoveredStep)) ids.add(s.issueId);
    }
    return ids;
  }, [hoveredStep, onSpine]);

  return (
    <div
      role="group"
      aria-label="Repair impact map"
      className="rounded-md border border-border bg-surface p-3"
    >
      <div className="overflow-x-auto">
        <div className="min-w-max">
          {/* file-track header */}
          <div className="grid items-end gap-x-1 pb-1.5" style={{ gridTemplateColumns }}>
            <div />
            <div className="text-[9px] uppercase tracking-wider text-ink-soft">Repair step</div>
            {files.map((file) => {
              const shared = sharedFiles.includes(file);
              const dimmed = hoveredFile !== null && hoveredFile !== file;
              return (
                <button
                  key={file}
                  type="button"
                  className="flex justify-center bg-transparent"
                  title={file}
                  onMouseEnter={() => setHoveredFile(file)}
                  onMouseLeave={() => setHoveredFile(null)}
                  onFocus={() => setHoveredFile(file)}
                  onBlur={() => setHoveredFile(null)}
                  style={{ opacity: dimmed ? 0.35 : 1 }}
                >
                  <span
                    className={`rotate-[-40deg] whitespace-nowrap text-[8px] ${
                      shared ? "font-semibold text-status-retry" : "text-ink-soft"
                    }`}
                  >
                    {basename(file)}
                  </span>
                </button>
              );
            })}
            {overflowFiles > 0 && (
              <div className="pl-1 text-[8px] whitespace-nowrap text-ink-soft">
                +{overflowFiles} file{overflowFiles === 1 ? "" : "s"}
              </div>
            )}
          </div>

          {/* shared-file callouts — always visible, not just on hover */}
          {sharedFiles.length > 0 && (
            <div className="mb-1.5 space-y-0.5">
              {sharedFiles.map((file) => {
                const steps = fileToSteps.get(file) ?? [];
                return (
                  <div
                    key={file}
                    className="flex flex-wrap items-center gap-1 text-[9px] text-status-retry"
                  >
                    <span aria-hidden>⋈</span>
                    <span className="font-semibold uppercase tracking-wider">Shared file</span>
                    <span className="font-mono text-ink">{file}</span>
                    <span className="text-ink-soft">
                      — {steps.length} repair{steps.length === 1 ? "" : "s"} touch it (
                      {steps.map((s) => s.issueId).join(", ")}) — cannot be treated as independent
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {/* rows */}
          <div>
            {onSpine.map((step, i) => {
              const executed = boundaryIdx >= 0 && i <= boundaryIdx;
              const isHovered = hoveredStep === step.issueId;
              const dimmedByStep =
                hoveredStep !== null && !isHovered && !hoveredRelated.has(step.issueId);
              const dimmedByFile = hoveredFile !== null && !step.files.includes(hoveredFile);
              const dimmed = dimmedByStep || dimmedByFile;
              const cve = isCveOrigin(step);
              const why = whyText(step);
              const prevStep = i > 0 ? onSpine[i - 1] : null;
              const chainedDependency =
                prevStep !== null &&
                step.incomingEdges.some((e) => e.fromIssue === prevStep.issueId);
              const otherIncoming = step.incomingEdges.filter(
                (e) => !prevStep || e.fromIssue !== prevStep.issueId,
              );

              return (
                <div key={step.issueId} className="contents">
                  {i > 0 && (
                    <div
                      className="col-span-full grid gap-x-1"
                      style={{ gridTemplateColumns }}
                      aria-hidden
                    >
                      <div className="flex justify-center">
                        <span
                          className={`h-3 w-px ${
                            chainedDependency ? "bg-status-running" : "bg-border"
                          }`}
                          style={{ opacity: chainedDependency ? 0.9 : 0.5 }}
                        />
                      </div>
                    </div>
                  )}

                  <div
                    className="grid items-center gap-x-1 border-t border-border/60 transition-opacity"
                    style={{ gridTemplateColumns, opacity: dimmed ? 0.3 : 1, minHeight: 40 }}
                  >
                    <div className="flex items-center justify-center">
                      <span
                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[8px] font-semibold ${
                          executed
                            ? "bg-status-completed-bg text-status-completed"
                            : "border border-dashed border-ink-soft/40 text-ink-soft"
                        }`}
                      >
                        {step.position}
                      </span>
                    </div>

                    <button
                      type="button"
                      className="flex min-w-0 items-center gap-1.5 bg-transparent py-1 pr-2 text-left"
                      onMouseEnter={() => setHoveredStep(step.issueId)}
                      onMouseLeave={() => setHoveredStep(null)}
                      onFocus={() => setHoveredStep(step.issueId)}
                      onBlur={() => setHoveredStep(null)}
                      onClick={() =>
                        setSelectedStep((cur) => (cur === step.issueId ? null : step.issueId))
                      }
                      aria-expanded={selectedStep === step.issueId}
                    >
                      <span
                        className={`truncate font-mono text-[10px] font-medium ${
                          executed ? "text-ink" : "text-ink-soft"
                        }`}
                        title={step.issueId}
                      >
                        {step.issueId}
                      </span>
                      <SeverityMark measured={why.measured} severity={why.severity} />
                      {step.conflictsWith.length > 0 && (
                        <span className="shrink-0 text-[9px] text-status-retry" aria-hidden>
                          ⋈
                        </span>
                      )}
                      {otherIncoming.length > 0 && (
                        <span
                          className="shrink-0 text-[9px] text-status-running"
                          title={otherIncoming
                            .map(
                              (e) => `depends on ${e.fromIssue}${e.reason ? ` (${e.reason})` : ""}`,
                            )
                            .join("; ")}
                          aria-hidden
                        >
                          ⤴
                        </span>
                      )}
                    </button>

                    {files.map((file) => {
                      const active = step.files.includes(file);
                      const shared = sharedFiles.includes(file);
                      return (
                        <div
                          key={file}
                          className="relative flex h-full items-center justify-center"
                        >
                          <span
                            className="absolute inset-y-0 left-1/2 -translate-x-1/2"
                            style={{
                              width: shared ? 2 : 1,
                              backgroundColor: shared
                                ? "var(--color-status-retry)"
                                : "var(--color-border)",
                              opacity: executed ? 0.8 : 0.35,
                            }}
                            aria-hidden
                          />
                          {active && (
                            <span
                              className={`relative h-2.5 w-2.5 rounded-full ${
                                executed
                                  ? "bg-status-completed"
                                  : "border-[1.5px] border-dashed border-ink-soft bg-surface"
                              }`}
                              aria-label={`${step.issueId} touches ${file}`}
                            />
                          )}
                        </div>
                      );
                    })}
                    {overflowFiles > 0 && <div />}

                    {cve && (
                      <span
                        className="col-span-full -mt-0.5 pl-6 text-[8px] text-status-retry"
                        title={`CVE reachability${step.why?.kind === "cve" ? ` — ${step.why.package ?? ""} ${step.why.installedVersion ?? ""}` : ""}`}
                      >
                        ◀ CVE / package advisory — reaches this repository
                      </span>
                    )}
                  </div>

                  {selectedStep === step.issueId && (
                    <StepDetail step={step} plan={plan} graphPos={graphPos} modelPos={i + 1} />
                  )}

                  {i === boundaryIdx && (
                    <div className="col-span-full">
                      <div
                        role="separator"
                        aria-label="A7 execution boundary — only this step is consumed"
                        className="relative my-1 border-y-2 border-dashed border-status-completed/70 bg-status-completed-bg/10 py-1 text-center"
                      >
                        <span className="text-[8px] font-semibold uppercase tracking-widest text-status-completed">
                          A7 execution boundary — only step {step.position} is consumed
                        </span>
                        <div className="text-[8px] text-ink-soft/80">
                          {plan.executionAuthority.note}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* file-hover caption — always renders a slot so layout doesn't jump */}
      <div className="mt-1.5 min-h-[1.2rem] text-[9px] text-ink-soft">
        {hoveredFile && (
          <span>
            <span className="font-mono text-ink">{hoveredFile}</span> —{" "}
            {fileToSteps.get(hoveredFile)?.length ?? 0} repair
            {(fileToSteps.get(hoveredFile)?.length ?? 0) === 1 ? "" : "s"} touch this file
          </span>
        )}
      </div>

      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-ink-soft">
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-status-completed" aria-hidden />
          consumed by A7
        </span>
        <span className="flex items-center gap-1">
          <span
            className="h-2 w-2 rounded-full border-[1.5px] border-dashed border-ink-soft bg-surface"
            aria-hidden
          />
          proposed, not executed
        </span>
        <span className="flex items-center gap-1 text-status-running">⤴ dependency</span>
        <span className="flex items-center gap-1 text-status-retry">⋈ shared file</span>
      </div>

      {plan.totalDependencyEdges === 0 && onSpine.length > 0 && (
        <p className="mt-2 rounded border border-border/70 bg-surface-muted/20 px-2 py-1.5 text-[9px] text-ink-soft">
          <span className="font-semibold uppercase tracking-wider text-ink">
            No dependency edges
          </span>{" "}
          — the current repair candidates have no dependency edges. This does not mean they carry no
          risk.
        </p>
      )}

      {offSpine.length > 0 && (
        <div className="mt-3 rounded border border-dashed border-border/70 p-2">
          <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
            ◇ Unordered repair candidates
          </div>
          <ul className="mt-1 space-y-1">
            {offSpine.map((step) => (
              <li key={step.issueId} className="text-[10px]">
                <span className="font-mono text-ink">{step.issueId}</span>{" "}
                <span className="font-mono text-ink-soft">{step.files.join(", ") || "—"}</span>
                <div className="text-[9px] text-ink-soft/80">
                  Not assigned a reliable execution position.
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------- LLM vs graph order

const RIBBON_INSET = 12;

function ribbonY(index: number, maxIndex: number): number {
  if (maxIndex === 0) return 50;
  return RIBBON_INSET + (index / maxIndex) * (100 - 2 * RIBBON_INSET);
}

function OrderComparison({ plan, derived }: { plan: RepairPlan; derived: PlanDerived }) {
  if (plan.orderingSource !== "llm") return null;

  const modelOrder = derived.onSpine.map((s) => s.issueId);

  if (derived.graphPos === null) {
    return (
      <div className="rounded-md border border-status-retry/40 bg-status-retry-bg/20 p-3">
        <div className="text-[9px] font-semibold uppercase tracking-wider text-status-retry">
          LLM-proposed order
        </div>
        <p className="mt-1 text-[10px] text-ink-soft">Dependency validation unavailable.</p>
      </div>
    );
  }

  const graphPos = derived.graphPos;
  const rows = modelOrder
    .map((issueId, i) => {
      const gp = graphPos.get(issueId);
      return gp === undefined ? null : { issueId, modelIndex: i, graphIndex: gp - 1 };
    })
    .filter((r): r is { issueId: string; modelIndex: number; graphIndex: number } => r !== null);

  if (rows.length === 0) return null;

  const differing = rows.filter((r) => r.modelIndex !== r.graphIndex).length;
  const maxIdx = Math.max(1, rows.length - 1);

  return (
    <div
      role="group"
      aria-label="Order comparison"
      className="rounded-md border border-border bg-surface p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
          Model order vs. dependency order
        </div>
        <div className="font-mono text-[11px] font-semibold text-ink">
          {differing === 0
            ? "Model order matches dependency order"
            : `${differing} position${differing === 1 ? "" : "s"} differ from dependency order`}
        </div>
      </div>
      <p className="mt-0.5 text-[9px] text-ink-soft">
        Dependency order is A6&apos;s own{" "}
        <code className="font-mono">topological_execution_order</code> — not recomputed here.
      </p>

      <div className="mt-2" style={{ height: Math.max(90, rows.length * 22) }}>
        <div className="relative h-full w-full">
          <svg
            className="absolute inset-0 h-full w-full"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            aria-hidden
          >
            {rows.map((r) => {
              const y1 = ribbonY(r.modelIndex, maxIdx);
              const y2 = ribbonY(r.graphIndex, maxIdx);
              const agrees = r.modelIndex === r.graphIndex;
              return (
                <path
                  key={r.issueId}
                  d={`M6,${y1} C 40,${y1} 60,${y2} 94,${y2}`}
                  fill="none"
                  stroke={agrees ? "var(--color-status-completed)" : "var(--color-status-retry)"}
                  strokeWidth={agrees ? 0.6 : 0.9}
                  strokeDasharray={agrees ? undefined : "2 1.5"}
                  vectorEffect="non-scaling-stroke"
                  style={{ opacity: agrees ? 0.5 : 0.9 }}
                />
              );
            })}
          </svg>
          <div className="absolute left-0 top-0 flex h-full w-[38%] flex-col justify-between py-1">
            <div className="mb-0.5 text-[8px] uppercase tracking-widest text-ink-soft/70">
              Model order
            </div>
            {rows.map((r) => (
              <div
                key={`ml-${r.issueId}`}
                className="truncate rounded border border-border bg-surface px-1 font-mono text-[9px] text-ink"
                style={{
                  position: "absolute",
                  top: `${ribbonY(r.modelIndex, maxIdx)}%`,
                  transform: "translateY(-50%)",
                }}
              >
                {r.modelIndex + 1}. {r.issueId}
              </div>
            ))}
          </div>
          <div className="absolute right-0 top-0 flex h-full w-[38%] flex-col items-end justify-between py-1 text-right">
            <div className="mb-0.5 text-[8px] uppercase tracking-widest text-ink-soft/70">
              Graph order
            </div>
            {rows.map((r) => {
              const agrees = r.modelIndex === r.graphIndex;
              return (
                <div
                  key={`gr-${r.issueId}`}
                  className={`truncate rounded border px-1 font-mono text-[9px] ${
                    agrees
                      ? "border-border bg-surface text-ink"
                      : "border-status-retry/40 bg-status-retry-bg/40 text-status-retry"
                  }`}
                  style={{
                    position: "absolute",
                    top: `${ribbonY(r.graphIndex, maxIdx)}%`,
                    transform: "translateY(-50%)",
                  }}
                >
                  {r.graphIndex + 1}. {r.issueId}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------- plan truth

export function PlanTruthPanel({ plan, derived }: { plan: RepairPlan; derived: PlanDerived }) {
  const authority =
    plan.orderingSource === "llm"
      ? "LLM-proposed ⚠"
      : plan.orderingSource === "deterministic"
        ? "Graph-derived ✓"
        : "Not measured";
  const consumes = derived.boundaryIdx >= 0 ? 1 : 0;

  const rows: [string, string][] = [
    ["A7 consumes", `${consumes} step${consumes === 1 ? "" : "s"}`],
    ["A6 proposes", `${plan.steps.length} step${plan.steps.length === 1 ? "" : "s"}`],
    ["Dependencies", `${plan.totalDependencyEdges}`],
    ["Conflict batches", `${plan.conflictBatches.length}`],
    ["Unordered", `${derived.offSpine.length}`],
  ];

  return (
    <aside
      role="group"
      aria-label="Plan truth"
      className="w-full shrink-0 rounded-md border border-border bg-surface p-3 sm:w-56"
    >
      <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
        Plan truth
      </div>
      <dl className="mt-2 space-y-1.5 text-[11px]">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-2">
            <dt className="text-ink-soft">{label}</dt>
            <dd className="font-mono font-semibold text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-2.5 border-t border-border/70 pt-2">
        <div className="text-[8px] uppercase tracking-wider text-ink-soft">Order authority</div>
        <div
          className={`font-mono text-[11px] font-semibold ${
            plan.orderingSource === "llm" ? "text-status-retry" : "text-ink"
          }`}
        >
          {authority}
        </div>
      </div>
      <div className="mt-2.5 border-t border-border/70 pt-2">
        <div className="text-[8px] uppercase tracking-wider text-ink-soft">Execution guarantee</div>
        <div className="font-mono text-[11px] font-semibold text-ink">
          {consumes === 1 ? "First step only" : "None"}
        </div>
      </div>
    </aside>
  );
}

// -------------------------------------------------------------------- root

export function RepairImpactMap({ plan }: { plan: RepairPlan }) {
  const derived = useDerived(plan);

  if (plan.steps.length === 0) return null;

  return (
    <div className="flex flex-col gap-3 sm:flex-row">
      <div className="min-w-0 flex-1 space-y-3">
        {plan.steps.length === 1 ? (
          <RepairFocus plan={plan} />
        ) : (
          <>
            <RepairSpine plan={plan} derived={derived} />
            <OrderComparison plan={plan} derived={derived} />
          </>
        )}
      </div>
      <PlanTruthPanel plan={plan} derived={derived} />
    </div>
  );
}
