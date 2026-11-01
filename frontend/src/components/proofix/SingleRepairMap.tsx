import { FileCode2, GitPullRequest, ShieldCheck, ArrowRight } from "lucide-react";
import type { RepairStep } from "./repairPlanTypes";

export function repairTitle(step: RepairStep): string {
  if (step.why?.kind === "static_finding" && step.why.message) return step.why.message;
  if (step.why?.kind === "cve") return `Address advisory in ${step.why.package ?? "dependency"}`;
  const symbol = step.evidence?.changes.find((change) => change.targetFunction)?.targetFunction;
  return symbol
    ? `Repair ${symbol}`
    : step.files.length
      ? `Repair ${step.files[0].split("/").pop()}`
      : "Proposed repair";
}

export function SingleRepairMap({
  step,
  onSelect,
  selected,
}: {
  step: RepairStep;
  onSelect: (id: string) => void;
  selected: boolean;
}) {
  const evidence = step.evidence;
  const validation = evidence?.validation ?? "unavailable";
  return (
    <div className="ag-single-map" role="group" aria-label="Single repair relationships">
      <div className="ag-relationship-card">
        <FileCode2 size={22} aria-hidden="true" />
        <span className="ag-eyebrow">TARGET FILES</span>
        <strong>{step.files[0] ?? "Target unavailable"}</strong>
        {step.files.length > 1 && (
          <span>+{step.files.length - 1} more · select change to inspect</span>
        )}
      </div>
      <div className="ag-relationship-link">
        <span>targeted by</span>
        <ArrowRight size={20} aria-hidden="true" />
      </div>
      <button
        className={`ag-relationship-card ag-change-target ${selected ? "ag-selected" : ""}`}
        onClick={() => onSelect(step.issueId)}
        aria-label="Inspect repair change"
        aria-pressed={selected}
      >
        <GitPullRequest size={22} aria-hidden="true" />
        <span className="ag-eyebrow">CHANGE {String(step.position).padStart(2, "0")}</span>
        <strong>{repairTitle(step)}</strong>
        <span>
          {evidence?.execution === "applied"
            ? "Files applied"
            : evidence?.execution === "partially_applied"
              ? "Some files applied"
              : "Proposed"}
        </span>
        <small>Select to inspect reasoning and diff</small>
      </button>
      <div className="ag-relationship-link">
        <span>{evidence?.validationScope ? "linked evidence" : "not yet linked"}</span>
        <ArrowRight size={20} aria-hidden="true" />
      </div>
      <div className={`ag-relationship-card ag-check-${validation}`}>
        <ShieldCheck size={22} aria-hidden="true" />
        <span className="ag-eyebrow">VALIDATION</span>
        <strong>
          {validation === "passed"
            ? "Bundle checks passed"
            : validation === "failed"
              ? "Bundle checks failed"
              : "Evidence unavailable"}
        </strong>
        <span>
          {evidence?.validationScope ? "Whole patch bundle" : "No matching validation record"}
        </span>
      </div>
      <p className="ag-relationship-caption">
        One repair candidate · connections show relationships, not extra execution steps.
      </p>
    </div>
  );
}
