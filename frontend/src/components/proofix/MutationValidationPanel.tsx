/**
 * A8 — Mutation Validation, as a "mutation battle map".
 *
 * Everything here comes from `GET /api/runs/{runId}/mutation`
 * (`services/ui_projection.py::build_mutation_validation`, sourced from A8's
 * own `MutationValidationResult`). One question drives the entire layout:
 *
 *   "Did the test suite notice when we deliberately broke the patch?"
 *
 * The patched code IS the visualization — mutants are markers in a gutter
 * beside the exact lines they attacked, not a separate chart. Survivor
 * evidence sits above the code (the finding A8 exists to produce); the
 * gauntlet that got us there is a compact wire, not a card; the mutation
 * score is a demoted, bottom-of-page fact, never the headline.
 *
 * Attribution is honest about what A8 actually measured:
 * - Every mutant carries `function` (real, from mutmut's own naming).
 * - A bounded subset (survivors first, then killed) also carries a real
 *   `line` and before/after diff, fetched from `mutmut show`. The rest
 *   render as function-level density, never a guessed line.
 * - `patchedLines` is a real diff against the original file — which lines
 *   the patch changed, distinct from which lines a mutant merely attacked.
 * - `mutationStatus !== "scored"` renders "NOT MEASURED", never "0%".
 * - No specific test name is ever attributed to a specific mutant — mutmut's
 *   own output does not carry that link, so nothing here invents one.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type {
  MutantBucket,
  MutationFunctionAttack,
  MutationMarker,
  MutationRetryContext,
  MutationStage,
  MutationValidationReport,
} from "./mutationValidationTypes";
import { classifyMutation, highlightPythonLine } from "./mutationSabotage";
import { getMutationValidation } from "@/lib/runService";
import type { AgentStatus } from "./data";

const BUCKET_HOVER_LABEL: Record<MutantBucket, string> = {
  killed: "A test detected this mutation.",
  survived: "No test detected this mutation.",
  inconclusive: "Excluded from the mutation score — no conclusive result.",
};

function basename(path: string): string {
  return path.split("/").pop() ?? path;
}

function rowId(fnName: string, line: number): string {
  return `battle-row-${fnName}-${line}`;
}

interface IndexedMarker extends MutationMarker {
  function: string;
}

// ================================================================ gauntlet wire

type GateState = "passed" | "failed" | "not_reached" | "unavailable";

interface Gate {
  id: MutationStage;
  title: string;
  lines: string[];
  state: GateState;
  expandable?: boolean;
}

function deriveGates(report: MutationValidationReport): Gate[] {
  const order: MutationStage[] = ["target_test", "regression", "mutation"];
  const stopIndex = order.indexOf(report.stage);

  const stateFor = (idx: number, gate: MutationStage): GateState => {
    if (report.stage === "not_reached") {
      if (idx === 0 && !report.pytestAvailable) return "failed";
      if (gate === "mutation" && report.mutationStatus === "unavailable") return "unavailable";
      return "not_reached";
    }
    if (idx < stopIndex) return "passed";
    if (idx === stopIndex) {
      if (gate !== "mutation") return "failed";
      return report.mutantSurvived ? "failed" : "passed";
    }
    return "not_reached";
  };

  const newCount = report.newFailures.length;
  const preCount = report.preExistingFailures.length;

  return [
    {
      id: "target_test",
      title: "① Target test",
      lines: [
        report.targetTestId ?? "(no target test)",
        !report.pytestAvailable
          ? "PYTEST DID NOT RUN"
          : report.targetTestPassed === null
            ? "—"
            : report.targetTestPassed
              ? "PASS"
              : "FAIL",
      ],
      state: stateFor(0, "target_test"),
    },
    {
      id: "regression",
      title: "② Regression",
      lines:
        report.regressionTestsPassed === null
          ? ["not reached"]
          : [`${newCount} new`, `${preCount} pre-existing`],
      state: stateFor(1, "regression"),
      expandable: newCount + preCount > 0,
    },
    {
      id: "mutation",
      title: "③ Sabotage",
      lines:
        report.mutationStatus === "scored"
          ? [`${report.killedMutants ?? 0} killed`, `${report.survivedMutants ?? 0} survived`]
          : report.mutationStatus === "unavailable"
            ? ["NOT MEASURED"]
            : ["not reached"],
      state: stateFor(2, "mutation"),
    },
  ];
}

const GATE_RING: Record<GateState, string> = {
  passed: "border-status-completed bg-status-completed text-surface",
  failed: "border-status-failed bg-status-failed text-surface",
  unavailable: "border-status-retry text-status-retry",
  not_reached: "border-border text-ink-soft",
};

function GateNode({ gate, onToggle, open }: { gate: Gate; onToggle?: () => void; open?: boolean }) {
  const Comp = gate.expandable ? "button" : "div";
  return (
    <Comp
      type={gate.expandable ? "button" : undefined}
      onClick={gate.expandable ? onToggle : undefined}
      className="flex min-w-0 flex-1 flex-col items-center gap-1 text-center"
    >
      <div
        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 text-[10px] font-bold ${GATE_RING[gate.state]}`}
      >
        {gate.state === "passed"
          ? "●"
          : gate.state === "failed"
            ? "●"
            : gate.state === "unavailable"
              ? "◎"
              : "○"}
      </div>
      <div className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
        {gate.title}
        {gate.expandable && (
          <ChevronDown className={`h-2.5 w-2.5 transition-transform ${open ? "rotate-180" : ""}`} />
        )}
      </div>
      {gate.lines.map((l, i) => (
        <div
          key={i}
          className={`truncate text-[10px] leading-tight ${
            i === 0 && gate.id === "regression" && report_newFailureEmphasis(l)
              ? "font-semibold text-status-failed"
              : "text-ink-soft"
          } ${i === gate.lines.length - 1 && gate.state === "failed" ? "font-semibold text-status-failed" : ""} ${gate.state === "not_reached" ? "opacity-40" : ""}`}
        >
          {l}
        </div>
      ))}
    </Comp>
  );
}

// "0 new" should read as neutral; "N new" (N>0) should read as alarm. Kept as
// a tiny local check rather than passing another prop through.
function report_newFailureEmphasis(text: string): boolean {
  const match = /^(\d+) new$/.exec(text);
  return !!match && match[1] !== "0";
}

function GauntletWire({
  report,
  regressionOpen,
  onToggleRegression,
}: {
  report: MutationValidationReport;
  regressionOpen: boolean;
  onToggleRegression: () => void;
}) {
  const gates = deriveGates(report);
  return (
    <div className="flex items-start gap-0 rounded-lg border border-border bg-surface-muted/20 px-2 py-2">
      {gates.map((gate, i) => (
        <div key={gate.id} className="flex flex-1 items-start">
          {i > 0 && (
            <div
              className={`mt-3 h-px flex-1 ${gates[i - 1].state === "passed" ? "bg-status-completed" : "bg-border"}`}
              aria-hidden
            />
          )}
          <GateNode
            gate={gate}
            open={gate.id === "regression" ? regressionOpen : undefined}
            onToggle={gate.id === "regression" ? onToggleRegression : undefined}
          />
        </div>
      ))}
    </div>
  );
}

function RegressionDetail({ report }: { report: MutationValidationReport }) {
  return (
    <div className="grid grid-cols-1 gap-3 rounded-lg border border-border bg-surface-muted/20 p-2.5 sm:grid-cols-2">
      <div>
        <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          New (caused by this patch)
        </div>
        <ul className="mt-1 space-y-1">
          {report.newFailures.length === 0 ? (
            <li className="text-[10px] text-ink-soft">none</li>
          ) : (
            report.newFailures.map((t) => (
              <li
                key={t}
                className="rounded border border-status-failed/20 bg-status-failed-bg/20 px-1.5 py-1 font-mono text-[10px] font-medium text-status-failed"
              >
                {t}
              </li>
            ))
          )}
        </ul>
      </div>
      <div>
        <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          Pre-existing (not caused by this patch)
        </div>
        <ul className="mt-1 space-y-1">
          {report.preExistingFailures.length === 0 ? (
            <li className="text-[10px] text-ink-soft">none</li>
          ) : (
            report.preExistingFailures.map((t) => (
              <li
                key={t}
                className="rounded px-1.5 py-1 font-mono text-[10px] text-ink-soft/70 line-through decoration-ink-soft/40"
              >
                {t}
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}

// ================================================================ survivor evidence

function SurvivorEvidence({ report }: { report: MutationValidationReport }) {
  const survived = report.survivedMutants ?? 0;
  if (report.mutationStatus !== "scored" || survived === 0) return null;
  const unlined = report.survivors.filter((s) => s.line === null).length;
  return (
    <div
      role="alert"
      className="rounded-lg border border-status-failed/40 bg-status-failed-bg/30 px-3 py-2"
    >
      <div className="font-mono text-sm font-bold text-status-failed">
        {survived} mutant{survived === 1 ? "" : "s"} survived
      </div>
      <p className="mt-0.5 text-[11px] text-ink">
        We deliberately broke the patch and nothing in the suite noticed. Click a hollow red marker
        below to see exactly what changed.
        {unlined > 0 && ` (${unlined} more survived without an exact line — see Test Armor below.)`}
      </p>
    </div>
  );
}

// ================================================================ mutation battle map

const DOT_BASE = "h-2.5 w-2.5 shrink-0 rounded-full border-2 transition-all";
const DOT_STYLE: Record<MutantBucket, string> = {
  killed: "bg-status-completed border-status-completed",
  survived: "bg-transparent border-status-failed",
  inconclusive: "bg-transparent border-status-retry",
};

function MutantDot({
  marker,
  dimmed,
  hovered,
  selected,
  onHover,
  onClick,
}: {
  marker: IndexedMarker;
  dimmed: boolean;
  hovered: boolean;
  selected: boolean;
  onHover: (v: boolean) => void;
  onClick: () => void;
}) {
  const cls = classifyMutation(marker.before ?? "", marker.after ?? "");
  const title =
    marker.before && marker.after
      ? `${BUCKET_HOVER_LABEL[marker.status]} (${cls?.operator ?? `${marker.before} → ${marker.after}`})`
      : BUCKET_HOVER_LABEL[marker.status];
  return (
    <button
      type="button"
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      onClick={onClick}
      title={title}
      aria-pressed={selected}
      className={`${DOT_BASE} ${DOT_STYLE[marker.status]} ${
        hovered || selected ? "scale-150" : dimmed ? "opacity-25" : ""
      }`}
    />
  );
}

function MutantEvidenceCard({ marker }: { marker: IndexedMarker }) {
  const cls = marker.before && marker.after ? classifyMutation(marker.before, marker.after) : null;

  if (marker.status === "inconclusive") {
    return (
      <div className="ml-8 mb-2 rounded-md border border-status-retry/30 bg-status-retry-bg/15 p-2.5 text-[11px]">
        <div className="font-mono font-semibold text-status-retry">INCONCLUSIVE</div>
        <p className="mt-1 text-ink">No test result was available for this mutant.</p>
        <p className="text-ink-soft">This mutant is excluded from the mutation score.</p>
      </div>
    );
  }

  if (marker.status === "killed") {
    return (
      <div className="ml-8 mb-2 rounded-md border border-status-completed/30 bg-status-completed-bg/15 p-2.5 text-[11px]">
        <div className="font-mono font-semibold text-status-completed">
          KILLED · line {marker.line}
        </div>
        {cls && <div className="mt-1 font-mono text-ink">{cls.operator}</div>}
        <p className="mt-1 text-ink">A test in the suite detected this mutation.</p>
        <p className="text-[10px] text-ink-soft">
          mutmut does not report which specific test caught it.
        </p>
      </div>
    );
  }

  // survived
  return (
    <div
      id={`survivor-card-${marker.mutantId}`}
      className="ml-8 mb-2 rounded-md border border-status-failed/40 bg-status-failed-bg/20 p-3 text-[11px]"
    >
      <div className="font-mono font-semibold text-status-failed">
        SURVIVOR · line {marker.line}
      </div>
      {cls ? (
        <>
          <div className="mt-2 font-mono text-sm text-ink">{cls.operator}</div>
          <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
            <span className="text-[9px] font-semibold uppercase tracking-wide text-ink-soft">
              Mutation
            </span>
            <span className="text-ink">This is a {cls.label} change.</span>
            <span className="text-[9px] font-semibold uppercase tracking-wide text-ink-soft">
              Test result
            </span>
            <span className="text-ink">No test in the suite detected this change.</span>
            <span className="text-[9px] font-semibold uppercase tracking-wide text-ink-soft">
              Why this matters
            </span>
            <span className="font-medium text-ink">
              The suite passed even after this behaviour was changed.
            </span>
            <span className="text-[9px] font-semibold uppercase tracking-wide text-ink-soft">
              Gap
            </span>
            <span className="text-ink">{cls.suggestion}</span>
          </div>
        </>
      ) : (
        <p className="mt-1 text-ink-soft">
          The suite passed even after this behaviour was changed. No before/after detail was fetched
          for this mutant.
        </p>
      )}
    </div>
  );
}

function BattleMapBlock({
  fn,
  patchFile,
  hoveredMutantId,
  setHoveredMutantId,
  expandedMutantId,
  setExpandedMutantId,
  hoveredLine,
  setHoveredLine,
}: {
  fn: MutationFunctionAttack;
  patchFile: string | null;
  hoveredMutantId: string | null;
  setHoveredMutantId: (id: string | null) => void;
  expandedMutantId: string | null;
  setExpandedMutantId: (id: string | null) => void;
  hoveredLine: number | null;
  setHoveredLine: (n: number | null) => void;
}) {
  const markersByLine = new Map<number, IndexedMarker[]>();
  for (const m of fn.markers) {
    const list = markersByLine.get(m.line) ?? [];
    list.push({ ...m, function: fn.name });
    markersByLine.set(m.line, list);
  }
  const patchedSet = new Set(fn.patchedLines);

  if (!fn.codeAvailable) {
    const total = fn.killed + fn.survived + fn.inconclusive;
    const filled = total > 0 ? Math.round(((fn.killed + fn.inconclusive) / total) * 20) : 0;
    const bar = "█".repeat(filled) + "░".repeat(20 - filled);
    return (
      <div id={`fn-${fn.name}`} className="rounded-lg border border-border bg-surface p-3">
        <div className="font-mono text-sm font-semibold text-ink">{fn.name}()</div>
        <div className="mt-1 font-mono text-xs tracking-tight text-ink-soft">
          {total > 0 ? bar : "NOT MEASURED"}
        </div>
        <div className="mt-1 text-[11px] text-ink-soft">
          {fn.killed} killed · {fn.survived} survived · {fn.inconclusive} inconclusive
        </div>
        <p className="mt-1.5 text-[10px] text-ink-soft">
          Line attribution unavailable — mutants mapped to function.
        </p>
      </div>
    );
  }

  return (
    <div id={`fn-${fn.name}`} className="rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="min-w-0 truncate font-mono text-[11px] text-ink">
          {patchFile ? basename(patchFile) : "?"} ·{" "}
          <span className="font-semibold">{fn.name}()</span>
        </div>
        <div className="shrink-0 font-mono text-[10px] tabular-nums text-ink-soft">
          <span className="text-status-completed">{fn.killed} killed</span>
          {fn.survived > 0 && (
            <span className="ml-2 text-status-failed">{fn.survived} survived</span>
          )}
        </div>
      </div>

      <div className="px-1 py-2">
        {fn.lines.map((row) => {
          const markers = markersByLine.get(row.line) ?? [];
          const rowActive = hoveredLine === row.line;
          const isPatched = patchedSet.has(row.line);
          return (
            <div key={row.line} id={rowId(fn.name, row.line)}>
              <div
                onMouseEnter={() => setHoveredLine(row.line)}
                onMouseLeave={() => setHoveredLine(null)}
                className={`flex items-start gap-1.5 border-l-2 px-1.5 py-0.5 font-mono text-[11px] leading-5 ${
                  isPatched ? "border-l-status-running/60" : "border-l-transparent"
                } ${rowActive ? "bg-surface-muted/60" : ""}`}
              >
                <span className="flex w-8 shrink-0 flex-wrap items-center justify-end gap-0.5 pt-0.5">
                  {markers.map((m) => (
                    <MutantDot
                      key={m.mutantId}
                      marker={m}
                      dimmed={
                        hoveredMutantId !== null && hoveredMutantId !== m.mutantId && !rowActive
                      }
                      hovered={hoveredMutantId === m.mutantId}
                      selected={expandedMutantId === m.mutantId}
                      onHover={(v) => setHoveredMutantId(v ? m.mutantId : null)}
                      onClick={() =>
                        setExpandedMutantId(expandedMutantId === m.mutantId ? null : m.mutantId)
                      }
                    />
                  ))}
                </span>
                <span className="w-8 shrink-0 select-none text-right text-ink-soft/60">
                  {row.line}
                </span>
                <span className="whitespace-pre">
                  {highlightPythonLine(row.text).map((t, i) => (
                    <span
                      key={i}
                      className={
                        t.kind === "keyword"
                          ? "text-status-running"
                          : t.kind === "string"
                            ? "text-status-completed"
                            : t.kind === "comment"
                              ? "italic text-ink-soft"
                              : t.kind === "number"
                                ? "text-status-retry"
                                : "text-ink"
                      }
                    >
                      {t.text}
                    </span>
                  ))}
                </span>
              </div>
              {markers
                .filter((m) => m.mutantId === expandedMutantId)
                .map((m) => (
                  <MutantEvidenceCard key={m.mutantId} marker={m} />
                ))}
            </div>
          );
        })}
      </div>

      {fn.unattributed > 0 && (
        <div className="border-t border-border px-3 py-1.5 text-[10px] text-ink-soft">
          +{fn.unattributed} more mutant{fn.unattributed === 1 ? "" : "s"} attacked this function —
          line detail not fetched to bound A8&apos;s runtime.
        </div>
      )}
    </div>
  );
}

// ================================================================ population strip

function PopulationStrip({
  report,
  mutantIndex,
  expandedMutantId,
  setExpandedMutantId,
  setHoveredMutantId,
  hoveredMutantId,
}: {
  report: MutationValidationReport;
  mutantIndex: Map<string, IndexedMarker>;
  expandedMutantId: string | null;
  setExpandedMutantId: (id: string | null) => void;
  setHoveredMutantId: (id: string | null) => void;
  hoveredMutantId: string | null;
}) {
  const killed = report.killedMutants ?? 0;
  const survived = report.survivedMutants ?? 0;
  const inconclusive = report.inconclusiveMutants ?? 0;
  if (report.mutationStatus !== "scored" || killed + survived + inconclusive === 0) return null;

  const lined: IndexedMarker[] = [];
  for (const fn of report.functions) {
    for (const m of fn.markers) lined.push({ ...m, function: fn.name });
  }
  const unlinedCount = killed + survived + inconclusive - lined.length;

  return (
    <div>
      <div className="mb-1 text-[9px] font-medium uppercase tracking-wider text-ink-soft">
        Mutants spawned
      </div>
      <div className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-surface-muted/20 p-2.5">
        {lined.map((m) => (
          <MutantDot
            key={m.mutantId}
            marker={mutantIndex.get(m.mutantId) ?? m}
            dimmed={hoveredMutantId !== null && hoveredMutantId !== m.mutantId}
            hovered={hoveredMutantId === m.mutantId}
            selected={expandedMutantId === m.mutantId}
            onHover={(v) => setHoveredMutantId(v ? m.mutantId : null)}
            onClick={() => setExpandedMutantId(expandedMutantId === m.mutantId ? null : m.mutantId)}
          />
        ))}
        {unlinedCount > 0 && (
          <span className="ml-1 text-[10px] text-ink-soft">+{unlinedCount} unmapped</span>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[10px] tabular-nums text-ink-soft">
        <span className="text-status-completed">{killed} KILLED</span>
        <span className="text-status-failed">{survived} SURVIVED</span>
        <span className="text-status-retry">{inconclusive} INCONCLUSIVE</span>
      </div>
    </div>
  );
}

// ================================================================ test armor

function TestArmor({ report }: { report: MutationValidationReport }) {
  if (report.functions.length === 0) return null;
  const scroll = (name: string) => {
    document.getElementById(`fn-${name}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  return (
    <div>
      <div className="mb-1 text-[9px] font-medium uppercase tracking-wider text-ink-soft">
        Test armor
      </div>
      <div className="space-y-1.5">
        {report.functions.map((fn) => {
          const total = fn.killed + fn.survived + fn.inconclusive;
          const filled = total > 0 ? Math.round(((fn.killed + fn.inconclusive) / total) * 24) : 0;
          const bar = "█".repeat(filled) + "░".repeat(24 - filled);
          return (
            <button
              key={fn.name}
              type="button"
              onClick={() => scroll(fn.name)}
              className="block w-full rounded-md border border-border bg-surface-muted/20 px-2.5 py-1.5 text-left transition-colors hover:bg-surface-muted/50"
            >
              <div className="font-mono text-[11px] text-ink">{fn.name}()</div>
              <div className="font-mono text-[10px] tracking-tight text-ink-soft">{bar}</div>
              <div className="text-[10px] text-ink-soft">
                {fn.killed} killed · {fn.survived} survived
                {fn.inconclusive > 0 ? ` · ${fn.inconclusive} inconclusive` : ""}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ================================================================ verdict

function verdictSentence(killed: number, survived: number): string {
  if (survived === 0) return "Tests catch every behavioural change attempted here.";
  // Majority rule rather than a fixed percentage cutoff: with small mutant
  // counts a fixed ratio threshold (e.g. 20%) swings on a single mutant.
  // Whether the suite caught *most* of what was thrown at it is the more
  // stable, honestly-derived signal.
  if (survived < killed) {
    return `Tests detect most behavioural changes, but ${survived} mutation${survived === 1 ? "" : "s"} can pass unnoticed.`;
  }
  return `A significant share of behavioural changes go unnoticed by the test suite — ${survived} of ${killed + survived} mutations survived.`;
}

function Verdict({ report }: { report: MutationValidationReport }) {
  if (report.mutationStatus !== "scored") {
    return (
      <div className="rounded-lg border border-status-retry/30 bg-status-retry-bg/15 p-3">
        <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
          Test suite defense
        </div>
        <div className="mt-1 font-mono text-sm font-bold text-status-retry">NOT MEASURED</div>
        <p className="mt-1 text-[11px] text-ink-soft">
          {report.unavailableReason ?? "Mutation testing produced no parseable results."}
        </p>
      </div>
    );
  }
  const killed = report.killedMutants ?? 0;
  const survived = report.survivedMutants ?? 0;
  const total = killed + survived;
  return (
    <div className="rounded-lg border border-border bg-surface-muted/20 p-3">
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
        Test suite defense
      </div>
      <div className="mt-1 font-mono text-sm tabular-nums text-ink">
        <span className="font-bold text-status-completed">
          {killed} / {total}
        </span>{" "}
        mutants killed
        {survived > 0 && (
          <span className="ml-3 font-bold text-status-failed">{survived} SURVIVED</span>
        )}
      </div>
      <p className="mt-1.5 text-[11px] text-ink">{verdictSentence(killed, survived)}</p>
    </div>
  );
}

// ================================================================ not-measured state

function NotMeasuredBattlefield({ report }: { report: MutationValidationReport }) {
  return (
    <div className="rounded-lg border border-status-retry/30 bg-status-retry-bg/10 p-6 text-center">
      <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
        Mutation battlefield
      </div>
      <div className="mt-3 font-mono text-xl text-status-retry">◎</div>
      <div className="mt-2 font-mono text-sm font-bold tracking-wide text-status-retry">
        {report.mutationStatus === "not_run" ? "MUTATION NOT REACHED" : "MUTATION NOT MEASURED"}
      </div>
      <p className="mx-auto mt-1.5 max-w-md text-[11px] text-ink-soft">
        {report.mutationStatus === "not_run"
          ? "An earlier gate stopped the run before mutation testing began."
          : (report.unavailableReason ?? "mutmut produced no parseable mutation results.")}
      </p>
      <p className="mx-auto mt-1 max-w-md text-[11px] text-ink-soft">
        No conclusion can be drawn about test-suite resistance to behavioural changes.
      </p>
      {report.patchFile && (
        <div className="mt-3 border-t border-border/50 pt-3 font-mono text-[10px] text-ink-soft">
          {basename(report.patchFile)}
          {report.functions[0] ? ` · ${report.functions[0].name}()` : ""}
        </div>
      )}
    </div>
  );
}

// ================================================================ technical metadata

function TechnicalMetadata({ report }: { report: MutationValidationReport }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-2.5 py-1.5 text-[10px] font-medium text-ink-soft"
      >
        Technical metadata
        <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-2.5 py-2 text-[10px] text-ink-soft">
          <div className="flex items-center justify-between">
            <span>Correctness (A10 gate)</span>
            <span className="font-mono text-ink">
              {report.correctnessScore !== null ? report.correctnessScore.toFixed(0) : "—"} /{" "}
              {report.correctnessThreshold.toFixed(0)}
            </span>
          </div>
          <div>
            <span>Target test command</span>
            <pre className="mt-1 overflow-x-auto rounded bg-surface-muted/50 p-1.5 font-mono text-[10px] text-ink">
              {report.pytestReexecutionCommand || "—"}
            </pre>
          </div>
          <div>
            <span>Mutmut command</span>
            <pre className="mt-1 overflow-x-auto rounded bg-surface-muted/50 p-1.5 font-mono text-[10px] text-ink">
              {report.reexecutionCommand || "—"}
            </pre>
          </div>
          <div>
            Timeout:{" "}
            <span className="font-mono text-ink">{report.reexecutionTimeoutSeconds ?? "—"}s</span>
          </div>
          {report.retryContext && <RetryDetail context={report.retryContext} />}
        </div>
      )}
    </div>
  );
}

function RetryDetail({ context }: { context: MutationRetryContext }) {
  const rows: { label: string; value: string | null }[] = [
    { label: "Assertion", value: context.assertionMessage },
    { label: "Expected", value: context.expectedValue },
    { label: "Actual", value: context.actualValue },
    { label: "Violated contract", value: context.violatedContract },
    { label: "Retry instruction", value: context.retryInstruction },
  ].filter((r) => r.value);
  if (rows.length === 0) return null;
  return (
    <div className="space-y-1 border-t border-border/60 pt-2">
      {rows.map((r) => (
        <div key={r.label}>
          <span className="uppercase tracking-wider">{r.label}</span>
          <p className="mt-0.5 text-ink">{r.value}</p>
        </div>
      ))}
    </div>
  );
}

// ================================================================ panel

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load mutation validation</span>
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

export function MutationValidationPanel({
  runId,
  status,
}: {
  runId: string;
  status?: AgentStatus;
}) {
  const [report, setReport] = useState<MutationValidationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [hoveredMutantId, setHoveredMutantId] = useState<string | null>(null);
  const [expandedMutantId, setExpandedMutantId] = useState<string | null>(null);
  const [hoveredLine, setHoveredLine] = useState<number | null>(null);
  const [regressionOpen, setRegressionOpen] = useState(false);
  const scrolledFor = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getMutationValidation(runId)
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

  const mutantIndex = useMemo(() => {
    const idx = new Map<string, IndexedMarker>();
    if (report) {
      for (const fn of report.functions) {
        for (const m of fn.markers) idx.set(m.mutantId, { ...m, function: fn.name });
      }
    }
    return idx;
  }, [report]);

  useEffect(() => {
    if (!expandedMutantId || scrolledFor.current === expandedMutantId) return;
    const marker = mutantIndex.get(expandedMutantId);
    if (!marker) return;
    scrolledFor.current = expandedMutantId;
    document
      .getElementById(rowId(marker.function, marker.line))
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [expandedMutantId, mutantIndex]);

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Mutation Validation
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Mutation validation loading…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!report) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Mutation Validation
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A8 is validating the patch now; this panel renders once it publishes."
            : "Mutation validation pending — A8 has not completed yet."}
        </p>
      </section>
    );
  }

  const scored = report.mutationStatus === "scored";

  return (
    <section className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Mutation Validation
        </h3>
        <p className="mt-0.5 text-[11px] text-ink-soft">
          Did the test suite notice when we deliberately broke the patch?
        </p>
      </div>

      <GauntletWire
        report={report}
        regressionOpen={regressionOpen}
        onToggleRegression={() => setRegressionOpen((v) => !v)}
      />
      {regressionOpen && <RegressionDetail report={report} />}

      <SurvivorEvidence report={report} />

      {!scored ? (
        <NotMeasuredBattlefield report={report} />
      ) : report.functions.length === 0 ? (
        <div className="rounded-lg border border-border bg-surface-muted/20 p-3 text-[11px] text-ink-soft">
          Mutation testing produced a score, but no per-mutant attribution — mutmut&apos;s output
          shape for this run carried only aggregate counts.
        </div>
      ) : (
        <div className="space-y-3">
          {report.functions.map((fn) => (
            <BattleMapBlock
              key={fn.name}
              fn={fn}
              patchFile={report.patchFile}
              hoveredMutantId={hoveredMutantId}
              setHoveredMutantId={setHoveredMutantId}
              expandedMutantId={expandedMutantId}
              setExpandedMutantId={setExpandedMutantId}
              hoveredLine={hoveredLine}
              setHoveredLine={setHoveredLine}
            />
          ))}
        </div>
      )}

      <PopulationStrip
        report={report}
        mutantIndex={mutantIndex}
        expandedMutantId={expandedMutantId}
        setExpandedMutantId={setExpandedMutantId}
        hoveredMutantId={hoveredMutantId}
        setHoveredMutantId={setHoveredMutantId}
      />

      <TestArmor report={report} />

      <Verdict report={report} />

      <TechnicalMetadata report={report} />

      <p className="text-[10px] text-ink-soft">
        Source: <code className="font-mono">GET /api/runs/{"{run_id}"}/mutation</code>
      </p>
    </section>
  );
}
