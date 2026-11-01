/**
 * A10 — the merge circuit.
 *
 * `a10_routing.gate_checks` evaluates ten gates in series and stops at the
 * first one that fires; every gate after it is returned with `checked: false`.
 * That is not "passed" and not "failed" — A10 never established which. A grid
 * of ten green/red chips cannot say that, so this draws the short circuit
 * itself: current flows from the evidence rail until a gate trips, and the
 * run past the break is dead line.
 *
 * Three visual states, never two:
 *
 *   passed          filled node, solid live rail        ✓
 *   first blocker   ringed node on the break            ✕
 *   not evaluated   hollow dashed node on dead rail     ┄
 *
 * The count of not-evaluated gates is stated as a number next to the break,
 * because it is the fact a reviewer most needs and the one a checklist hides:
 * closing the known blocker does not mean the run merges — there may be more
 * blockers behind it that A10 never reached.
 *
 * Every fact drawn traces to a `HardGate` from
 * `GET /api/runs/{runId}/decision`. The short rail labels and the next-action
 * sentences below are the only client-side additions, and both are keyed on
 * `gate.code` — neither invents a gate, a state, or a score.
 *
 * The gate count is fixed at ten by `a10_routing.gate_checks` regardless of
 * repository size, so the circuit itself never grows or shrinks with it —
 * only three responsive tiers of the same ten nodes, by container width:
 *
 *   `@sm` and below   two rows of five, same order (gate 5 → gate 6 wraps
 *                     top-row-end to bottom-row-start), icon + number only.
 *   `@sm`..`@lg`      one row, icon + number — the "compressed" label a
 *                     medium width calls for.
 *   `@lg` and above   one row, icon + number + the short word — never
 *                     rotated; there is finally room to set it flat.
 *
 * A gate's full name is always available without a width change: a native
 * `<title>` on every node hovers it, and clicking any node opens the same
 * detail card below regardless of tier.
 */
import { useState } from "react";
import type { HardGate } from "./mergeabilityTypes";

/** Rail captions — one word, sits directly under the node, never rotated.
 *  The full `gate.label` is A10's own wording and is shown verbatim in the
 *  detail card and as a hover title; these exist purely to fit the axis. */
export const SHORT_LABEL: Record<string, string> = {
  validation_exhausted: "Retry",
  patch_retry_required: "Patch",
  target_test_failed: "Target",
  regression_failed: "Regress",
  security_rejected: "Security",
  phantoms_detected: "Phantom",
  correctness_low: "Correctness",
  security_low: "Security",
  axes_measured: "Axes",
  reproduction_confirmed: "Repro",
};

/**
 * The noun a blocker headline names — deliberately not `gate.label`. Every
 * gate's `label` from `a10_routing.gate_checks` is phrased as its *passing*
 * condition ("All four axes measured", "Security re-scan accepted the
 * patch"). Echoing that sentence when the gate is the one that fired reads
 * as a contradiction — "ALL FOUR AXES MEASURED — BLOCKED HERE" says the
 * opposite of what happened. `GATE_TOPIC` names the subject instead; the
 * pass-condition sentence still appears, correctly scoped, in the detail
 * card and the "why it stopped" copy below it.
 */
export const GATE_TOPIC: Record<string, string> = {
  validation_exhausted: "Retry Budget",
  patch_retry_required: "Patch Retry",
  target_test_failed: "Target Test",
  regression_failed: "Regression Tests",
  security_rejected: "Security Rescan",
  phantoms_detected: "Phantom Changes",
  correctness_low: "Correctness Score",
  security_low: "Security Score",
  axes_measured: "Axes Measurement",
  reproduction_confirmed: "Reproduction",
};

/** `①`…`⑩` — never more than ten, since `a10_routing.gate_checks` is a fixed
 *  ten-gate chain regardless of repository size. */
function circledDigit(index: number): string {
  return String.fromCodePoint(0x2460 + index);
}

/**
 * Hover/title text for a gate — topic and state on one line, the real
 * pass-condition wording labeled "Required" on the next. Never fuses
 * `gate.label` directly against the state word: `label` is always phrased
 * as what *passing* looks like, and "All four axes measured — blocked here"
 * on one line says the opposite of what happened.
 */
function gateTooltip(gate: HardGate, index: number, state: GateState): string {
  const topic = GATE_TOPIC[gate.code] ?? gate.label;
  return `Gate ${index + 1} · ${topic} — ${STATE_TEXT[state]}\nRequired: ${gate.label}`;
}

/** What closing this blocker requires. Guidance, not a finding — it never
 *  asserts anything about the run that the gate detail did not already say.
 *  Rendered in the outcome zone, next to the verdict it explains. */
export const NEXT_ACTION: Record<string, string> = {
  validation_exhausted:
    "The retry budget is spent, so the pipeline will not attempt another patch. Review the accumulated validation failures and fix by hand.",
  patch_retry_required:
    "Re-run A7 → A8 with the retry brief. The reproduced test has to pass before routing continues.",
  target_test_failed:
    "The reproduced test still fails against the patch. Re-run A7 → A8 with the retry brief.",
  regression_failed:
    "Inspect the tests A8's regression phase newly failed, then re-patch — the fix broke something that worked before.",
  security_rejected: "Resolve the findings A9 introduced after the patch, then re-run A9 → A10.",
  phantoms_detected:
    "Reconcile the PR description with the diff. A10's MCI check found claims the diff does not support.",
  correctness_low:
    "Raise correctness above its threshold with a stronger patch, or merge manually after review.",
  security_low: "Raise the security score above its threshold, or merge manually after review.",
  axes_measured:
    "These scores were never produced. Check whether the tools that feed them ran at all, then re-run.",
  reproduction_confirmed:
    "The bug was never reproduced, so no runtime evidence backs this patch. Fix the reproduction environment and re-run.",
};

export interface CircuitShape {
  /** Index of the first gate that fired, or `-1` when the chain ran clean. */
  blockerIndex: number;
  blocker: HardGate | null;
  /** Gates A10 never reached — distinct from gates that failed. */
  notEvaluated: number;
}

export function deriveCircuit(gates: HardGate[]): CircuitShape {
  const blockerIndex = gates.findIndex((g) => g.checked && g.passed === false);
  return {
    blockerIndex,
    blocker: blockerIndex === -1 ? null : gates[blockerIndex],
    notEvaluated: gates.filter((g) => !g.checked).length,
  };
}

type GateState = "passed" | "blocker" | "not_evaluated";

function stateOf(gate: HardGate): GateState {
  if (!gate.checked) return "not_evaluated";
  return gate.passed ? "passed" : "blocker";
}

const STATE_TEXT: Record<GateState, string> = {
  passed: "passed",
  blocker: "blocked here",
  not_evaluated: "not evaluated",
};

const STATE_TONE: Record<GateState, string> = {
  passed: "text-status-completed",
  blocker: "text-status-failed",
  not_evaluated: "text-ink-soft",
};

// ------------------------------------------------------------------ geometry

const VIEW_W = 680;
const VIEW_H = 190;
const RAIL_Y = 62;
const RAIL_START = 18;
const RAIL_END = 662;
const FIRST_X = 40;
const LAST_X = 640;

function nodeX(index: number, count: number): number {
  if (count <= 1) return FIRST_X;
  return FIRST_X + (index * (LAST_X - FIRST_X)) / (count - 1);
}

// -------------------------------------------------------------------- marks

/**
 * The three state marks, as bare SVG fragments centred on `(cx, cy)`. Shared
 * between the wide rail's inline nodes and the narrow ladder's standalone
 * icons so a repo that renders one gate or ten never draws the state
 * language two different ways.
 */
function GateMark({ state, cx, cy }: { state: GateState; cx: number; cy: number }) {
  if (state === "passed") {
    return (
      <>
        <circle cx={cx} cy={cy} r={9} fill="currentColor" />
        <path
          d={`M ${cx - 4} ${cy} l 2.8 2.9 l 5.2 -5.8`}
          fill="none"
          stroke="var(--color-surface)"
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </>
    );
  }
  if (state === "blocker") {
    return (
      <>
        <circle
          cx={cx}
          cy={cy}
          r={11}
          fill="var(--color-surface)"
          stroke="currentColor"
          strokeWidth={2.2}
        />
        <path
          d={`M ${cx - 3.6} ${cy - 3.6} l 7.2 7.2 M ${cx + 3.6} ${cy - 3.6} l -7.2 7.2`}
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
        />
      </>
    );
  }
  return (
    <circle
      cx={cx}
      cy={cy}
      r={6.5}
      fill="var(--color-surface)"
      stroke="currentColor"
      strokeWidth={1.2}
      strokeDasharray="2 2.5"
      opacity={0.75}
    />
  );
}

/** Standalone 20px icon for the ladder rows — same marks, own viewport. */
function GateGlyph({ state }: { state: GateState }) {
  return (
    <svg
      viewBox="0 0 20 20"
      width={20}
      height={20}
      aria-hidden
      className={`shrink-0 ${STATE_TONE[state]}`}
    >
      <GateMark state={state} cx={10} cy={10} />
    </svg>
  );
}

// -------------------------------------------------------------------- nodes

/**
 * One node on the wide rail. The label never rotates: a circled number sits
 * directly under the mark at every width, and the word beside it only
 * appears once there is real room for it (`@lg` and up) — at `@sm`..`@lg`
 * the number alone is the "compressed" label the medium tier calls for.
 * The full gate name is always one hover or one click away, via `<title>`
 * and the persistent detail card below.
 */
function GateNode({
  gate,
  x,
  index,
  selected,
  onSelect,
}: {
  gate: HardGate;
  x: number;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const state = stateOf(gate);
  const short = SHORT_LABEL[gate.code] ?? gate.label;
  const tone = STATE_TONE[state];

  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`Gate ${index + 1}: ${gate.label} — ${STATE_TEXT[state]}`}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`cursor-pointer outline-none ${tone}`}
    >
      <title>{gateTooltip(gate, index, state)}</title>

      {/* Generous invisible hit area — the visible node is 9px. */}
      <rect x={x - 24} y={RAIL_Y - 18} width={48} height={56} fill="transparent" />

      {selected && (
        <circle
          cx={x}
          cy={RAIL_Y}
          r={14}
          fill="none"
          stroke="currentColor"
          strokeWidth={1}
          opacity={0.45}
        />
      )}

      <GateMark state={state} cx={x} cy={RAIL_Y} />

      <text
        x={x}
        y={RAIL_Y + 20}
        textAnchor="middle"
        className={`font-mono text-[9px] ${state === "not_evaluated" ? "fill-ink-soft opacity-70" : "fill-ink"}`}
      >
        {circledDigit(index)}
      </text>
      <text
        x={x}
        y={RAIL_Y + 31}
        textAnchor="middle"
        className={`hidden @lg:inline font-sans text-[8.5px] ${
          state === "not_evaluated" ? "fill-ink-soft opacity-70" : "fill-ink"
        }`}
      >
        {short}
      </text>
    </g>
  );
}

// ------------------------------------------------------------- narrow grid

/** The rail segment immediately before position `i` in a left-to-right
 *  sequence is live only if the gate before it actually passed — a blocker
 *  or a not-evaluated gate at `i - 1` both mean current stopped by then. */
function segmentLiveBefore(gates: HardGate[], i: number): boolean {
  return stateOf(gates[i - 1]) === "passed";
}

/** One node for the narrow grid — icon and circled number only, no word:
 *  at this width there is room for two rows of five, not for prose. The
 *  full name is still one hover or tap away via `title` and the shared
 *  detail card. */
function GridNode({
  gate,
  index,
  selected,
  onSelect,
}: {
  gate: HardGate;
  index: number;
  selected: boolean;
  onSelect: (i: number) => void;
}) {
  const state = stateOf(gate);
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`Gate ${index + 1}: ${gate.label} — ${STATE_TEXT[state]}`}
      title={gateTooltip(gate, index, state)}
      onClick={() => onSelect(index)}
      className={`flex shrink-0 flex-col items-center gap-0.5 rounded-md p-1 outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        selected ? "bg-surface-muted" : ""
      }`}
    >
      <GateGlyph state={state} />
      <span
        className={`font-mono text-[8.5px] ${state === "not_evaluated" ? "text-ink-soft" : "text-ink"}`}
      >
        {circledDigit(index)}
      </span>
    </button>
  );
}

/** One row of (up to) five gates, connected left to right by short rail
 *  segments carrying the same three states as the wide rail's line. */
function GridRow({
  gates,
  rowOffset,
  selected,
  onSelect,
}: {
  gates: HardGate[];
  rowOffset: number;
  selected: number | null;
  onSelect: (i: number) => void;
}) {
  return (
    <div className="flex items-center">
      {gates.map((gate, i) => (
        <div key={gate.code} className="flex flex-1 items-center">
          {i > 0 && (
            <span
              aria-hidden
              className={
                segmentLiveBefore(gates, i)
                  ? "h-0.5 flex-1 rounded-full bg-status-completed"
                  : "h-0 flex-1 border-t border-dashed border-ink-soft/40"
              }
            />
          )}
          <GridNode
            gate={gate}
            index={rowOffset + i}
            selected={selected === rowOffset + i}
            onSelect={onSelect}
          />
        </div>
      ))}
    </div>
  );
}

/** The short connector between the two rows — live only if the row above
 *  ran clean through its last gate, meaning current continues downward
 *  rather than having already stopped somewhere in row one. */
function RowConnector({ live }: { live: boolean }) {
  return (
    <div className="flex pl-[15px]" aria-hidden>
      <span
        className={
          live
            ? "h-3 w-0.5 rounded-full bg-status-completed"
            : "h-3 w-0 border-l border-dashed border-ink-soft/40"
        }
      />
    </div>
  );
}

/**
 * Narrow-container fallback for the rail: the same ten gates, same order,
 * wrapped into two rows of five instead of shrunk onto one — gate 5 to gate
 * 6 reads top-row-end to bottom-row-start, not a break in sequence. Every
 * mark, state rule, and click target is shared with the wide rail via
 * `GateGlyph`/`stateOf`; only the layout differs.
 */
function TwoRowGrid({
  gates,
  selected,
  onSelect,
}: {
  gates: HardGate[];
  selected: number | null;
  onSelect: (i: number) => void;
}) {
  const half = Math.ceil(gates.length / 2);
  const row1 = gates.slice(0, half);
  const row2 = gates.slice(half);
  const connectorLive = row1.length > 0 && stateOf(row1[row1.length - 1]) === "passed";

  return (
    <div className="@sm:hidden">
      <GridRow gates={row1} rowOffset={0} selected={selected} onSelect={onSelect} />
      {row2.length > 0 && (
        <>
          <RowConnector live={connectorLive} />
          <GridRow gates={row2} rowOffset={half} selected={selected} onSelect={onSelect} />
        </>
      )}
    </div>
  );
}

// -------------------------------------------------------------------- panel

export function MergeCircuit({ gates }: { gates: HardGate[] }) {
  const { blockerIndex, blocker, notEvaluated } = deriveCircuit(gates);
  const [selected, setSelected] = useState<number | null>(null);

  if (gates.length === 0) {
    return (
      <p className="rounded-lg border border-border bg-surface-muted/30 p-3 text-[11px] text-ink-soft">
        A10 published no gate trace for this run.
      </p>
    );
  }

  const breakX = blockerIndex === -1 ? RAIL_END : nodeX(blockerIndex, gates.length);
  const shown = selected === null ? (blockerIndex === -1 ? null : blockerIndex) : selected;
  const shownGate = shown === null ? null : gates[shown];

  return (
    // `@container`: below `@sm` ten nodes on one line have no room to
    // breathe, so `TwoRowGrid` wraps them into two rows of five instead. At
    // `@sm` and up the single-line rail takes over, compressing its word
    // labels away below `@lg`. All three read the same ten gates in the
    // same order and share `selected`, so switching mid-session (a panel
    // resized from a docked sidebar to full width) loses nothing.
    <div className="@container">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-ink-soft">
          Merge circuit
        </span>
        <span className="text-[10px] text-ink-soft">
          Ten gates in series, evaluated left to right — A10 stops at the first that trips.
        </span>
      </div>

      <div className="rounded-lg border border-border bg-surface-muted/25 p-2">
        <TwoRowGrid gates={gates} selected={shown} onSelect={setSelected} />

        <div className="hidden @sm:block">
          <svg
            viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
            className="h-auto w-full"
            role="img"
            aria-label={
              blocker
                ? `Routing stopped at gate ${blockerIndex + 1} of ${gates.length}. ${notEvaluated} gates were never evaluated.`
                : `All ${gates.length} hard gates cleared.`
            }
          >
            {/* Dead rail: drawn first, full width, so the live rail overlays it. */}
            <line
              x1={RAIL_START}
              y1={RAIL_Y}
              x2={RAIL_END}
              y2={RAIL_Y}
              className="stroke-ink-soft"
              strokeWidth={1.5}
              strokeDasharray="3 4"
              opacity={0.4}
            />

            {/* Live rail: current reaches exactly as far as the first blocker. */}
            <line
              x1={RAIL_START}
              y1={RAIL_Y}
              x2={breakX}
              y2={RAIL_Y}
              pathLength={1}
              className="animate-circuit-draw stroke-status-completed"
              strokeWidth={2.5}
              strokeLinecap="round"
            />

            {/* Source cap. */}
            <circle cx={RAIL_START} cy={RAIL_Y} r={3} className="fill-status-completed" />
            <text x={RAIL_START} y={RAIL_Y - 20} className="fill-ink-soft font-sans text-[9px]">
              Evidence
            </text>

            {gates.map((gate, i) => (
              <GateNode
                key={gate.code}
                gate={gate}
                index={i}
                x={nodeX(i, gates.length)}
                selected={shown === i}
                onSelect={() => setSelected(i)}
              />
            ))}

            {/* The break: a hard stop marker and the count behind it. */}
            {blocker && (
              <g className="text-status-failed">
                <line
                  x1={breakX}
                  y1={RAIL_Y - 26}
                  x2={breakX}
                  y2={RAIL_Y + 26}
                  stroke="currentColor"
                  strokeWidth={1}
                  opacity={0.35}
                />
                <text
                  x={Math.min(breakX + 14, VIEW_W - 8)}
                  y={RAIL_Y - 30}
                  className="fill-status-failed font-sans text-[10px] font-semibold"
                >
                  Routing stopped
                </text>
              </g>
            )}

            {notEvaluated > 0 && (
              <text
                x={RAIL_END}
                y={RAIL_Y - 20}
                textAnchor="end"
                className="fill-ink-soft font-sans text-[9.5px]"
              >
                {notEvaluated} never evaluated
              </text>
            )}

            {blockerIndex === -1 && (
              <text
                x={RAIL_END}
                y={RAIL_Y - 20}
                textAnchor="end"
                className="fill-status-completed font-sans text-[9.5px] font-medium"
              >
                All {gates.length} cleared
              </text>
            )}
          </svg>
        </div>
      </div>

      {/* Legend — three states, stated as three, in the same marks used above. */}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-ink-soft">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-status-completed" aria-hidden />
          Passed
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full border-2 border-status-failed"
            aria-hidden
          />
          First blocker
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full border border-dashed border-ink-soft"
            aria-hidden
          />
          Not evaluated — A10 never established pass or fail
        </span>
      </div>

      {/* Detail for the selected gate; defaults to the blocker. Leads with
          the topic and the state as two separate facts, then the gate's own
          pass-condition wording labeled "Required" — never fused into one
          sentence, which is what read as a contradiction when the state was
          "blocked" and the wording described what passing looks like. */}
      {shownGate && (
        <div className="mt-2 rounded-lg border border-border bg-surface-muted/30 p-3">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="font-mono text-[10px] text-ink-soft">
              Gate {(shown ?? 0) + 1}/{gates.length}
            </span>
            <span className="text-[11.5px] font-semibold text-ink">
              {GATE_TOPIC[shownGate.code] ?? shownGate.label}
            </span>
            <span
              className={`text-[10px] font-medium uppercase tracking-wide ${STATE_TONE[stateOf(shownGate)]}`}
            >
              {STATE_TEXT[stateOf(shownGate)]}
            </span>
          </div>
          <p className="mt-1 text-[10.5px] text-ink-soft">Required: {shownGate.label}</p>
          {shownGate.detail && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-status-failed">
              {shownGate.detail}
            </p>
          )}
          {stateOf(shownGate) === "not_evaluated" && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-ink-soft">
              Gate {blockerIndex + 1} ended the trace before this one ran. This gate has not passed
              — its result is unknown.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
