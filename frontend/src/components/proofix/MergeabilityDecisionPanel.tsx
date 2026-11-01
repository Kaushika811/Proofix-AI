/**
 * A10 — Mergeability Decision.
 *
 * Everything here comes from `GET /api/runs/{runId}/decision`
 * (`services/ui_projection.py::build_mergeability_decision`, sourced from
 * A10's own `PRRoutingDecision` and `VerificationBundle`). A10 does not
 * re-verify correctness or security — the `correctness` and `security` axes
 * below are A8's and A9's own scores, passed through unchanged. A10 verifies
 * exactly one thing itself (the MCI phantom check) and computes two axes of
 * its own (fidelity, scope safety); this panel labels that distinction
 * rather than presenting all four axes as A10's independent findings.
 *
 * Design: an engineering decision instrument, not a scorecard. Fixed
 * information hierarchy, independent of repository size — the panel looks
 * the same whether the run touched 5 files or 100,000:
 *
 *   1. Merge outcome     — the taken outlet, alternatives greyed, never hidden.
 *   2. First blocker     — one line: `GATE n · TOPIC — BLOCKED HERE`, or that
 *      every gate cleared. Names the gate's *topic*, never its A10 `label` —
 *      every `label` is phrased as the gate's passing condition ("All four
 *      axes measured"), and printing that verbatim next to "BLOCKED HERE"
 *      for the one gate that fires on it reads as a contradiction.
 *   3. Merge circuit (`MergeCircuit`) — the ten-gate short-circuit chain,
 *      drawn as current that stops at the first gate that trips. Mirrors
 *      `a10_routing.gate_checks` exactly: same order, same wording. A gate
 *      marked `checked: false` is drawn as "not evaluated" — a third state,
 *      never collapsed into passed and never into failed. Ten nodes, always
 *      — nothing here scales with repository size; only the responsive tier
 *      (one row, two rows, compressed labels) does.
 *   4. Blocker → evidence — connects the blocker to the specific evidence
 *      that decided it. Axis-shaped gates get a real per-axis breakdown from
 *      `decision.axes`; every other gate shows its own `detail` sentence.
 *      Never a guess at evidence the payload doesn't carry.
 *   5. Evidence axes     — each score against its threshold as a physical
 *      mark, so the verdict is read off the bar, not computed from the
 *      number. An unmeasured axis is hatched with no bar length at all:
 *      `null` must never come to resemble `0`, which is the exact bug
 *      `services/measurement.py` exists to prevent — a zero-length bar
 *      re-introduces it in pixels.
 *   6. Why it stopped    — one or two sentences: what wasn't established,
 *      and that later gates were never evaluated — never claimed to pass or
 *      fail.
 *   7. Next action        — one actionable sentence, naming the actual
 *      missing evidence for axis gates rather than a generic "check your
 *      tools".
 *   8. Repository evidence — a fixed-width strip of real counts (files
 *      analyzed, changed, modules, dependency edges), re-read from A1/A5/A7's
 *      own state. This is the one place repository size could blow the panel
 *      up, so it never lists a file inline — only aggregate chips, with the
 *      file/module lists behind explicit "View affected files" /
 *      "View proof bundle" disclosures a reviewer opens on purpose. Once
 *      opened, files are grouped by directory, filterable, and paginated a
 *      fixed number of modules at a time — a 100,000-file repository still
 *      renders a bounded number of DOM nodes.
 *   9. Optional detail   — PR link, phantom check, description, routing
 *      modifiers, and the trust mean. Real facts, but none of them is the
 *      routing decision, so none of them outranks it visually.
 *
 * `routingModifiers` (citation review, reproduction confidence, the stricter
 * security auto-merge bar) is shown only when `hardGatesClear` is true; a
 * hard-blocked run never reached those facts, so nothing is guessed in their
 * place.
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, GitPullRequest } from "lucide-react";
import type {
  MergeabilityAxis,
  MergeabilityDecision,
  PRType,
  RepositoryEvidence,
} from "./mergeabilityTypes";
import { GATE_TOPIC, MergeCircuit, NEXT_ACTION, deriveCircuit } from "./MergeCircuit";
import { getMergeabilityDecision } from "@/lib/runService";
import type { AgentStatus } from "./data";

/** "correctness" / "correctness and security" / "correctness, security, and fidelity" */
function joinWithAnd(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(", ")}, and ${items[items.length - 1]}`;
}

/**
 * The gates whose blocking condition is one or more of the four evidence
 * axes — the only gates `BlockerEvidenceConnector` can honestly expand into
 * a per-axis breakdown, because `decision.axes` is the only structured
 * evidence this endpoint carries. Every other gate's evidence is its own
 * `detail` sentence, already real, already A10's own wording.
 */
const AXIS_GATE_CODES = new Set(["axes_measured", "correctness_low", "security_low"]);

function axesForGate(code: string, axes: MergeabilityAxis[]): MergeabilityAxis[] {
  if (code === "axes_measured") return axes;
  if (code === "correctness_low") return axes.filter((a) => a.name === "correctness");
  if (code === "security_low") return axes.filter((a) => a.name === "security");
  return [];
}

const VERDICT_LABEL: Record<PRType, string> = {
  auto_mergeable: "Auto-Mergeable",
  diff_only: "Diff Only",
  draft: "Draft PR",
};

const VERDICT_CAPTION: Record<PRType, string> = {
  auto_mergeable: "Every hard gate cleared and both axis thresholds were met.",
  diff_only: "Technically valid, but not eligible for auto-merge — manual review required.",
  draft: "A hard gate blocked this run — manual verification required before merge.",
};

const VERDICT_STYLE: Record<PRType, string> = {
  auto_mergeable: "border-status-completed/30 bg-status-completed-bg/40 text-status-completed",
  diff_only: "border-status-retry/30 bg-status-retry-bg/40 text-status-retry",
  draft: "border-status-failed/30 bg-status-failed-bg/40 text-status-failed",
};

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load mergeability decision</span>
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

// ------------------------------------------------------- zone 2: evidence axes

/**
 * One axis against its threshold. The threshold is drawn as a physical tick
 * on the track, so "does this clear the bar" is a spatial judgement rather
 * than arithmetic on a number.
 *
 * An unmeasured axis gets a hatched track and no fill and no marker. It is
 * deliberately not a short bar: a zero-length bar and a zero score look
 * identical, and conflating them is the precise failure
 * `services/measurement.py` was written to end.
 */
function AxisMeter({ axis }: { axis: MergeabilityAxis }) {
  const measured = axis.value !== null;
  const tone =
    axis.meetsLowThreshold === null
      ? "text-ink-soft"
      : axis.meetsLowThreshold
        ? "text-status-completed"
        : "text-status-failed";
  const fillTone =
    axis.meetsLowThreshold === null
      ? "bg-ink-soft"
      : axis.meetsLowThreshold
        ? "bg-status-completed"
        : "bg-status-failed";

  return (
    <div className="rounded-lg border border-border bg-surface-muted/30 px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2 text-[11px]">
        <span className="font-medium text-ink">{axis.label}</span>
        <span className={`font-mono font-semibold ${tone}`}>
          {measured ? axis.value!.toFixed(0) : "not measured"}
        </span>
      </div>

      <div
        className="relative mt-2 h-2.5 overflow-hidden rounded-full bg-surface"
        role="img"
        aria-label={
          measured
            ? `${axis.label} ${axis.value!.toFixed(0)}, threshold ${axis.lowThreshold.toFixed(0)}`
            : `${axis.label} not measured`
        }
      >
        {measured ? (
          <div
            className={`h-full rounded-full ${fillTone}`}
            style={{ width: `${Math.max(0, Math.min(100, axis.value!))}%` }}
          />
        ) : (
          <div
            className="h-full w-full opacity-45"
            style={{
              backgroundImage:
                "repeating-linear-gradient(-45deg, var(--color-ink-soft) 0 1.5px, transparent 1.5px 5px)",
            }}
          />
        )}

        {/* Threshold ticks sit above the fill — the bar is read against them. */}
        <span
          className="absolute inset-y-0 w-px bg-ink"
          style={{ left: `${axis.lowThreshold}%` }}
          aria-hidden
        />
        {axis.autoMergeThreshold !== undefined && (
          <span
            className="absolute inset-y-0 w-px bg-ink opacity-45"
            style={{ left: `${axis.autoMergeThreshold}%` }}
            aria-hidden
          />
        )}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-ink-soft">
        <span>
          ≥ {axis.lowThreshold.toFixed(0)}{" "}
          {axis.meetsLowThreshold === null ? "— not measured" : axis.meetsLowThreshold ? "✓" : "✕"}
        </span>
        {axis.autoMergeThreshold !== undefined && (
          <span>
            ≥ {axis.autoMergeThreshold.toFixed(0)} for auto-merge{" "}
            {axis.meetsAutoMergeThreshold === null
              ? "— not measured"
              : axis.meetsAutoMergeThreshold
                ? "✓"
                : "✕"}
          </span>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------- zone 1: merge outcome

const OUTLETS: PRType[] = ["auto_mergeable", "diff_only", "draft"];

/**
 * The taken outlet against the two not taken, as one row of chips — grey,
 * never absent, so what this run did not earn is as visible as what it did.
 * This is the top of the hierarchy: nothing below outranks it, including the
 * trust mean.
 */
function MergeOutcome({ decision }: { decision: MergeabilityDecision }) {
  return (
    <div className="space-y-2">
      <div
        role="status"
        className={`rounded-xl border px-4 py-3 ${VERDICT_STYLE[decision.prType]}`}
      >
        <div className="text-sm font-semibold uppercase tracking-wide">
          {VERDICT_LABEL[decision.prType]}
        </div>
        <div className="mt-0.5 text-[11px] opacity-90">{VERDICT_CAPTION[decision.prType]}</div>
      </div>

      <div className="flex flex-wrap gap-1.5" role="list" aria-label="Routing outcomes">
        {OUTLETS.map((outlet) => {
          const taken = outlet === decision.prType;
          return (
            <span
              key={outlet}
              role="listitem"
              className={
                taken
                  ? `rounded-full border px-2.5 py-1 text-[10px] font-semibold ${VERDICT_STYLE[decision.prType]}`
                  : "rounded-full border border-border px-2.5 py-1 text-[10px] text-ink-soft opacity-60"
              }
            >
              {VERDICT_LABEL[outlet]}
              {taken && " — taken"}
            </span>
          );
        })}
      </div>
    </div>
  );
}

// -------------------------------------------------------- zone 2: first blocker

/**
 * One line, exactly the shape a reviewer scanning fast needs:
 * `GATE n · TOPIC — BLOCKED HERE`, plus how many later gates the trace never
 * reached. This sits above the circuit on purpose — it is the answer; the
 * circuit below is the evidence for it.
 *
 * The headline names the gate's *topic* (`GATE_TOPIC`), never its `label` —
 * `a10_routing.gate_checks` phrases every `label` as the gate's *passing*
 * condition ("All four axes measured"), and printing that sentence next to
 * "BLOCKED HERE" for the one gate that fires on it reads as a contradiction.
 * For that specific gate, the real axis measurement state is spelled out
 * underneath instead, straight from `decision.axes` — never a repeat of the
 * passing-condition sentence standing in for what actually happened.
 */
function BlockerHeadline({ decision }: { decision: MergeabilityDecision }) {
  const { blocker, blockerIndex, notEvaluated } = deriveCircuit(decision.hardGates);

  if (!blocker) {
    return (
      <div className="rounded-lg border border-status-completed/30 bg-status-completed-bg/30 px-3 py-2 text-[11px] text-status-completed">
        All {decision.hardGates.length} gates cleared — no blocker.
      </div>
    );
  }

  const topic = GATE_TOPIC[blocker.code] ?? blocker.label;
  const axes = axesForGate(blocker.code, decision.axes);
  const measuredCount = axes.filter((a) => a.measured).length;
  const unmeasuredLabels = axes.filter((a) => !a.measured).map((a) => a.label.toLowerCase());

  return (
    <div className="rounded-lg border border-status-failed/30 bg-status-failed-bg/30 px-3 py-2">
      <div className="font-mono text-[11.5px] font-semibold text-status-failed">
        GATE {blockerIndex + 1} · {topic.toUpperCase()} — BLOCKED HERE
      </div>
      {blocker.code === "axes_measured" && (
        <div className="mt-0.5 font-mono text-[10.5px] text-ink">
          {measuredCount} / {axes.length} axes measured
          {unmeasuredLabels.length > 0 && <> · {joinWithAnd(unmeasuredLabels)} unavailable</>}
        </div>
      )}
      <div className="mt-0.5 text-[10.5px] text-ink-soft">
        1 known blocker · {notEvaluated} later {notEvaluated === 1 ? "gate" : "gates"} unverified.
      </div>
    </div>
  );
}

// ------------------------------------ zone: blocker → evidence connection

/**
 * Connects the blocker to the specific evidence that decided it, instead of
 * making the reviewer infer the link between the circuit and the axes below
 * it. Axis-shaped gates (`AXIS_GATE_CODES`) get a real per-axis breakdown
 * straight from `decision.axes` — measured value and threshold verdict when
 * present, "NOT MEASURED" when `null`, never a fabricated pass or fail.
 * Every other gate's evidence is its own `detail` sentence — A10's real
 * wording, not a synthesized substructure the payload doesn't have.
 */
function BlockerEvidenceConnector({ decision }: { decision: MergeabilityDecision }) {
  const { blocker, blockerIndex } = deriveCircuit(decision.hardGates);
  if (!blocker) return null;

  if (!AXIS_GATE_CODES.has(blocker.code)) {
    if (!blocker.detail) return null;
    return (
      <div className="flex items-start gap-2 rounded-lg border border-status-failed/20 bg-status-failed-bg/10 px-3 py-2 font-mono text-[11px]">
        <span className="shrink-0 text-status-failed">GATE {blockerIndex + 1} ✕</span>
        <span className="text-ink">{blocker.detail}</span>
      </div>
    );
  }

  const axes = axesForGate(blocker.code, decision.axes);

  return (
    <div className="rounded-lg border border-status-failed/20 bg-status-failed-bg/10 p-3">
      <div className="font-mono text-[11px] font-semibold text-status-failed">
        GATE {blockerIndex + 1} ✕
      </div>
      <ul className="mt-1 space-y-0.5 pl-3 font-mono text-[11px]">
        {axes.map((axis, i) => (
          <li key={axis.name} className={axis.measured ? "text-ink" : "text-status-retry"}>
            <span className="text-ink-soft">{i === axes.length - 1 ? "└──" : "├──"}</span>{" "}
            {axis.label} —{" "}
            {axis.measured
              ? `${axis.value!.toFixed(0)} (${axis.meetsLowThreshold ? "meets" : "below"} threshold)`
              : "NOT MEASURED"}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ----------------------------------------------------- zone 5: why it stopped

/**
 * Short, decision-oriented — what wasn't established and why that stops
 * routing, not a restatement of every fact already visible in the circuit
 * and the connector above it. `axes_measured` gets copy built from the real
 * unmeasured axis names; every other gate uses A10's own one-sentence
 * `detail`, which is already this terse.
 */
function WhyItStopped({ decision }: { decision: MergeabilityDecision }) {
  const { blocker, notEvaluated } = deriveCircuit(decision.hardGates);
  if (!blocker) return null;

  let headline: string;
  if (blocker.code === "axes_measured") {
    const unmeasured = decision.axes.filter((a) => !a.measured).map((a) => a.label.toLowerCase());
    headline =
      unmeasured.length > 0
        ? `${joinWithAnd(unmeasured)} ${unmeasured.length === 1 ? "was" : "were"} not measured. A10 cannot establish merge readiness without ${unmeasured.length === 1 ? "that input" : "those inputs"}.`
        : (blocker.detail ?? decision.reviewNote ?? "");
  } else {
    headline = blocker.detail ?? decision.reviewNote ?? "";
  }

  if (!headline) return null;

  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
        Why it stopped
      </div>
      <div className="rounded-lg border border-border bg-surface-muted/30 p-3">
        <p className="text-[11.5px] leading-relaxed text-ink">{headline}</p>
        {notEvaluated > 0 && (
          <p className="mt-1 text-[11px] text-ink-soft">
            {notEvaluated} later {notEvaluated === 1 ? "gate was" : "gates were"} never evaluated —
            not claimed to pass, not claimed to fail.
          </p>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------- zone: next action

/**
 * One actionable sentence. `axes_measured` names exactly which score(s) to
 * produce, built from the real unmeasured axes rather than a generic "check
 * your tools" — every other gate keeps `MergeCircuit`'s static
 * `NEXT_ACTION`, which is already written as an instruction.
 */
function NextAction({ decision }: { decision: MergeabilityDecision }) {
  const { blocker } = deriveCircuit(decision.hardGates);
  if (!blocker) return null;

  let action: string | undefined = NEXT_ACTION[blocker.code];
  if (blocker.code === "axes_measured") {
    const unmeasured = decision.axes.filter((a) => !a.measured).map((a) => a.label.toLowerCase());
    if (unmeasured.length > 0) {
      action = `Run the tools that produce the ${joinWithAnd(unmeasured)} score${unmeasured.length > 1 ? "s" : ""}, then re-run A10.`;
    }
  }

  if (!action) return null;

  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
        Next action
      </div>
      <div className="rounded-lg border border-border bg-surface-muted/30 p-3">
        <p className="text-[11.5px] leading-relaxed text-ink">{action}</p>
      </div>
    </div>
  );
}

// ------------------------------------------------ zone 6: repository evidence

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full border border-border bg-surface px-2.5 py-1 font-mono text-[10.5px] text-ink">
      {children}
    </span>
  );
}

function moduleOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i === -1 ? "." : path.slice(0, i);
}

const MODULE_PAGE = 20;
const FILES_PER_MODULE_PAGE = 50;
/** A module opens by default only if it's small enough that opening it
 *  doesn't itself dump a wall of text — the same "don't render everything
 *  at once" rule applied one level down. */
const AUTO_EXPAND_BELOW = 8;

/** One module's files, capped and collapsible — the unit that keeps a
 *  100,000-file repository's drill-down from ever rendering all of them at
 *  once, however many modules the search/filter above leaves standing. */
function ModuleGroup({
  module,
  files,
  changedSet,
}: {
  module: string;
  files: string[];
  changedSet: Set<string>;
}) {
  const [expanded, setExpanded] = useState(files.length <= AUTO_EXPAND_BELOW);
  const [filesShown, setFilesShown] = useState(FILES_PER_MODULE_PAGE);
  const visible = files.slice(0, filesShown);
  const remaining = files.length - visible.length;

  return (
    <div className="rounded-md border border-border bg-surface p-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="truncate font-mono text-[10.5px] text-ink">{module}/</span>
        <span className="shrink-0 text-[9.5px] text-ink-soft">
          {files.length} file{files.length === 1 ? "" : "s"}
        </span>
      </button>
      {expanded && (
        <ul className="mt-1 space-y-0.5 pl-3 font-mono text-[10px]">
          {visible.map((f) => (
            <li key={f} className={changedSet.has(f) ? "text-ink" : "text-ink-soft"}>
              {changedSet.has(f) ? "● " : "○ "}
              {f.startsWith(`${module}/`) ? f.slice(module.length + 1) : f}
            </li>
          ))}
          {remaining > 0 && (
            <li>
              <button
                type="button"
                onClick={() => setFilesShown((v) => v + FILES_PER_MODULE_PAGE)}
                className="text-[10px] font-medium text-ink-soft underline decoration-dotted hover:text-ink"
              >
                Show {Math.min(FILES_PER_MODULE_PAGE, remaining)} more ({remaining} remaining)
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

/**
 * The large-repository drill-down: real changed + blast-scope files, grouped
 * by directory, filterable, and rendered a fixed number of modules at a
 * time — a 4-file repo and a 100,000-file repo hit the same code path, the
 * second one just has more "show more" to click through. Never a
 * virtualization library pulled in for what's fundamentally a filtered,
 * paginated list of strings.
 */
function AffectedFilesDrilldown({ evidence }: { evidence: RepositoryEvidence }) {
  const [query, setQuery] = useState("");
  const [visibleModules, setVisibleModules] = useState(MODULE_PAGE);

  const changedSet = useMemo(() => new Set(evidence.changedFiles), [evidence.changedFiles]);
  const allFiles = useMemo(
    () => Array.from(new Set([...evidence.changedFiles, ...evidence.blastScopeFiles])).sort(),
    [evidence.changedFiles, evidence.blastScopeFiles],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? allFiles.filter((f) => f.toLowerCase().includes(q)) : allFiles;
  }, [allFiles, query]);

  const grouped = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const f of filtered) {
      const mod = moduleOf(f);
      (map.get(mod) ?? map.set(mod, []).get(mod)!).push(f);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  if (allFiles.length === 0) {
    return <p className="text-[11px] text-ink-soft">No changed or blast-scope files to show.</p>;
  }

  const shownModules = grouped.slice(0, visibleModules);
  const remainingModules = grouped.length - shownModules.length;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setVisibleModules(MODULE_PAGE);
          }}
          placeholder="Filter files or modules…"
          aria-label="Filter affected files"
          className="w-full rounded-md border border-border bg-surface px-2 py-1 text-[11px] text-ink placeholder:text-ink-soft focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <span className="shrink-0 text-[10px] text-ink-soft">
          ● changed &nbsp; ○ blast scope only
        </span>
      </div>

      {grouped.length === 0 ? (
        <p className="text-[11px] text-ink-soft">No files match &ldquo;{query}&rdquo;.</p>
      ) : (
        <div className="max-h-64 space-y-1.5 overflow-y-auto pr-0.5">
          {shownModules.map(([mod, files]) => (
            <ModuleGroup key={mod} module={mod} files={files} changedSet={changedSet} />
          ))}
          {remainingModules > 0 && (
            <button
              type="button"
              onClick={() => setVisibleModules((v) => v + MODULE_PAGE)}
              className="w-full rounded-md border border-border py-1.5 text-[10.5px] font-medium text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
            >
              Show {Math.min(MODULE_PAGE, remainingModules)} more modules ({remainingModules}{" "}
              remaining)
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The one place repository size could otherwise blow the panel up. This
 * never lists a file inline — a run over 5 files and a run over 100,000
 * render the same four chips, and the same two disclosure buttons, at the
 * same size. The lists behind them only render once a reviewer opens them,
 * and even then are grouped, filtered, and paginated rather than dumped.
 */
function RepositoryEvidenceStrip({ decision }: { decision: MergeabilityDecision }) {
  const evidence = decision.repositoryEvidence;
  const [showFiles, setShowFiles] = useState(false);
  const [showProof, setShowProof] = useState(false);

  const heading = (
    <div className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
      Repository evidence
    </div>
  );

  if (!evidence) {
    return (
      <div className="space-y-2">
        {heading}
        <p className="rounded-lg border border-border bg-surface-muted/30 p-3 text-[11px] text-ink-soft">
          No repository evidence yet — A1, A5, and A7 have not produced anything to summarize.
        </p>
      </div>
    );
  }

  const changedCount = evidence.changedFiles.length;
  const moduleCount = evidence.affectedModules.length;
  const affectedFiles = Array.from(
    new Set([...evidence.changedFiles, ...evidence.blastScopeFiles]),
  ).sort();
  const proof = decision.proofBundle;

  return (
    <div className="space-y-2">
      {heading}

      <div
        className="flex flex-wrap items-center gap-1.5"
        role="list"
        aria-label="Repository evidence counts"
      >
        <Chip>
          {evidence.filesAnalyzed === null
            ? "files analyzed — not measured"
            : `${evidence.filesAnalyzed.toLocaleString()} files analyzed`}
        </Chip>
        <Chip>{`${changedCount.toLocaleString()} changed`}</Chip>
        <Chip>{`${moduleCount.toLocaleString()} module${moduleCount === 1 ? "" : "s"} affected`}</Chip>
        <Chip>
          {evidence.dependencyEdgeCount === null
            ? "dependency paths — not measured"
            : `${evidence.dependencyEdgeCount.toLocaleString()} dependency path${
                evidence.dependencyEdgeCount === 1 ? "" : "s"
              }`}
        </Chip>
      </div>

      <div className="flex flex-wrap gap-2 text-[10.5px]">
        <button
          type="button"
          onClick={() => setShowFiles((v) => !v)}
          aria-expanded={showFiles}
          disabled={affectedFiles.length === 0}
          className="rounded-md border border-border px-2 py-1 font-medium text-ink transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          {showFiles ? "Hide" : "View"} affected files
        </button>
        <button
          type="button"
          onClick={() => setShowProof((v) => !v)}
          aria-expanded={showProof}
          disabled={!proof || proof.steps.length === 0}
          className="rounded-md border border-border px-2 py-1 font-medium text-ink transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          {showProof ? "Hide" : "View"} proof bundle
        </button>
      </div>

      {showFiles && <AffectedFilesDrilldown evidence={evidence} />}

      {showProof && proof && proof.steps.length > 0 && (
        <ul className="max-h-56 space-y-1.5 overflow-y-auto rounded-lg border border-border bg-surface-muted/30 p-2.5">
          {proof.steps.map((s) => (
            <li key={s.name} className="rounded-md bg-surface p-2 font-mono text-[10px] text-ink">
              <div className="text-ink-soft">{s.name}</div>
              <div className="break-all">{s.command}</div>
              <div className="text-ink-soft">expects: {s.expectedResult}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ------------------------------------------------------------ routing modifiers

function RoutingModifiers({ decision }: { decision: MergeabilityDecision }) {
  const mods = decision.routingModifiers;
  if (!mods.hardGatesClear) {
    return (
      <p className="rounded-lg border border-border bg-surface-muted/30 p-3 text-[11px] text-ink-soft">
        Not applicable — a hard gate blocked this run before these facts were evaluated.
      </p>
    );
  }
  return (
    <ul className="space-y-1 rounded-lg border border-border bg-surface-muted/30 p-2.5 text-[11px] text-ink">
      <li>
        Citation review needed:{" "}
        <span className="font-mono">{mods.citationReviewNeeded ? "yes" : "no"}</span>
      </li>
      <li>
        Reproduction confidence:{" "}
        <span className="font-mono">{mods.reproductionConfidence ?? "—"}</span>
      </li>
      <li>
        Security meets auto-merge threshold:{" "}
        <span className="font-mono">
          {mods.securityMeetsAutoMergeThreshold === null
            ? "not measured"
            : mods.securityMeetsAutoMergeThreshold
              ? "yes"
              : "no"}
        </span>
      </li>
    </ul>
  );
}

// --------------------------------------------------------- progressive disclosure

function Details({ decision }: { decision: MergeabilityDecision }) {
  const [open, setOpen] = useState(false);
  const proof = decision.proofBundle;
  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-3 py-2 text-[11px] font-medium text-ink"
      >
        PR &amp; proof-bundle detail
        <ChevronDown
          className={`h-3.5 w-3.5 text-ink-soft transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-3 py-2 text-[11px] text-ink-soft">
          <div className="flex items-center gap-2">
            <GitPullRequest className="h-3.5 w-3.5 shrink-0" aria-hidden />
            {decision.prUrl ? (
              <a
                href={decision.prUrl}
                target="_blank"
                rel="noreferrer"
                className="truncate font-mono text-ink underline"
              >
                {decision.prUrl}
              </a>
            ) : (
              <span>Not published — no PR target configured, or publishing failed.</span>
            )}
          </div>
          <div>
            Phantom changes detected:{" "}
            <span className="font-mono text-ink">
              {decision.phantomChangesDetected ? "yes" : "no"}
            </span>
          </div>
          {decision.descriptionWhy && (
            <div>
              <span className="text-[10px] uppercase tracking-wider text-ink-soft">
                Description — why
              </span>
              <p className="mt-0.5 text-ink">{decision.descriptionWhy}</p>
            </div>
          )}
          {decision.descriptionWhat && (
            <div>
              <span className="text-[10px] uppercase tracking-wider text-ink-soft">
                Description — what
              </span>
              <p className="mt-0.5 text-ink">{decision.descriptionWhat}</p>
            </div>
          )}
          {proof && (
            // The step-by-step list lives under Repository Evidence's own
            // "View proof bundle" disclosure — this only confirms the bundle
            // exists and names it, so the two zones don't show the same list
            // twice.
            <div>
              <span className="text-[10px] uppercase tracking-wider text-ink-soft">
                Proof bundle
              </span>
              <div className="mt-0.5 font-mono text-[10px] text-ink">{proof.bundleHash ?? "—"}</div>
            </div>
          )}
          <div className="border-t border-border pt-3">
            <span className="text-[10px] uppercase tracking-wider text-ink-soft">
              Routing modifiers
            </span>
            <div className="mt-1.5">
              <RoutingModifiers decision={decision} />
            </div>
          </div>
          <div className="border-t border-border pt-3">
            <div className="flex items-center justify-between">
              <span className="text-[10px] uppercase tracking-wider text-ink-soft">
                Trust (measured axes only)
              </span>
              <span className="font-mono text-ink">
                {decision.trust === null ? "—" : decision.trust.toFixed(2)}
              </span>
            </div>
            <p className="mt-1 text-[10px] text-ink-soft">
              Descriptive mean of the axes that were measured — not itself the routing gate. The
              decision comes from the per-axis thresholds and the merge circuit above.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- panel

export function MergeabilityDecisionPanel({
  runId,
  status,
}: {
  runId: string;
  status?: AgentStatus;
}) {
  const [decision, setDecision] = useState<MergeabilityDecision | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getMergeabilityDecision(runId)
      .then((d) => {
        if (!cancelled) setDecision(d);
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

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Mergeability Decision
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Mergeability decision loading…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!decision) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Mergeability Decision
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A10 is scoring and routing now; this panel renders once it publishes."
            : "Mergeability decision pending — A10 has not completed yet."}
        </p>
      </section>
    );
  }

  return (
    // `@container`: every zone below reflows against this panel's own width,
    // not the viewport's. The panel sits in a workspace column that can be
    // as narrow as a stacked mobile card or as wide as a three-column desktop
    // layout — a viewport breakpoint would fire at the wrong moment for
    // either, since it knows nothing about the column the panel actually got.
    <section className="@container space-y-4 rounded-2xl border border-border bg-surface p-4">
      <div>
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Mergeability Decision
        </h3>
        <p className="mt-0.5 text-[11px] text-ink-soft">
          A10 does not re-verify correctness or security — the axes below for those two are
          A8&apos;s and A9&apos;s own scores. A10 checks the PR description against the diff for
          phantom claims, computes fidelity and scope safety itself, and routes through the same ten
          hard gates shown below before deciding.
        </p>
      </div>

      {/* 1. Merge outcome — the routing decision itself, ranked above every
          score on the panel including trust. */}
      <MergeOutcome decision={decision} />

      {/* 2. First blocker — the one-line answer the circuit below proves. */}
      <BlockerHeadline decision={decision} />

      {/* 3. Merge circuit — fixed at ten gates regardless of repository size. */}
      <MergeCircuit gates={decision.hardGates} />

      {/* 4. Blocker → evidence connection — names the specific evidence that
          decided it, so the link to the axes below isn't left implicit. */}
      <BlockerEvidenceConnector decision={decision} />

      {/* 5. Evidence axes. */}
      <div>
        <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Evidence axes
        </div>
        <div className="grid grid-cols-1 gap-2 @sm:grid-cols-2">
          {decision.axes.map((a) => (
            <AxisMeter key={a.name} axis={a} />
          ))}
        </div>
      </div>

      {/* 6. Why it stopped. */}
      <WhyItStopped decision={decision} />

      {/* 7. Next action. */}
      <NextAction decision={decision} />

      {/* 8. Repository evidence — aggregate chips only; file/module lists are
          opt-in. This is the zone that has to stay flat as repository size
          grows, so nothing above it may render per-file. */}
      <RepositoryEvidenceStrip decision={decision} />

      {/* 9. Optional detail — PR link, phantom check, description, routing
          modifiers, and the trust mean, behind one disclosure. */}
      <Details decision={decision} />

      <p className="text-[10px] text-ink-soft">
        Explain: A10 reads A8&apos;s and A9&apos;s scores, verifies its own phantom check, and
        routes — it does not re-run tests or re-scan for vulnerabilities. Source:{" "}
        <code className="font-mono">GET /api/runs/{"{run_id}"}/decision</code>
      </p>
    </section>
  );
}
