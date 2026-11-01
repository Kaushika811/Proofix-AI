/**
 * A0.7 — Environment Preflight Board.
 *
 * Everything on this card comes from `state.environment` (A0.7's own report,
 * forwarded verbatim onto the workspace header) or from `environmentProbeError`
 * (set when the probe itself crashed). Nothing here is estimated, animated, or
 * templated from a percentage — a check either ran and says what it found, or
 * it did not run and says so. See `backend/services/environment_probe.py` for
 * what the backend actually checks.
 *
 * Visual model: a four-stage execution gate — MANIFEST → LANGUAGE → TEST
 * RUNNER → TEST COLLECTION — mirroring the probe's own short-circuit cascade
 * (`probe_environment`). The gate renders as one continuous circuit when every
 * stage held, or visibly severed at the first stage that didn't; every stage
 * after the break renders dashed, dimmed, and captioned NOT ATTEMPTED — never
 * as a zero or a blank, because the probe genuinely never reached it. That
 * distinction is the reason this agent exists: before it, a run that couldn't
 * install its own dependencies still walked seven downstream stages and came
 * back with a composite score that read as "we analysed your repo and it
 * scored badly", when the truth was "we never ran your test suite". Where the
 * circuit breaks *is* the diagnosis.
 */
import { Check, X, AlertTriangle, Copy, Minus } from "lucide-react";
import { useState } from "react";
import type { WorkspaceHeaderModel } from "@/mocks";

type EnvironmentReport = NonNullable<WorkspaceHeaderModel["environment"]>;

/** The seven states a user can actually be shown — never collapsed together. */
type BoardState =
  | "READY"
  | "NOT_PREPARED"
  | "UNSUPPORTED"
  | "NO_MANIFEST"
  | "NO_TEST_RUNNER"
  | "NO_TESTS"
  | "ERROR";

const STATE_LABEL: Record<BoardState, string> = {
  READY: "READY",
  NOT_PREPARED: "NOT PREPARED",
  UNSUPPORTED: "UNSUPPORTED",
  NO_MANIFEST: "NO MANIFEST",
  NO_TEST_RUNNER: "NO TEST RUNNER",
  NO_TESTS: "NO TESTS",
  ERROR: "ERROR",
};

/** The one-line verdict people scan for first. Composed from the stage names
 * below, never a separate fact — there is nothing here the circuit itself
 * doesn't already show. */
const DECISION_TITLE: Record<BoardState, string> = {
  READY: "EXECUTION READY",
  NO_TESTS: "READY — NO TESTS FOUND",
  NOT_PREPARED: "BLOCKED AT TEST RUNNER",
  NO_TEST_RUNNER: "BLOCKED AT TEST RUNNER",
  UNSUPPORTED: "BLOCKED AT LANGUAGE",
  NO_MANIFEST: "BLOCKED AT MANIFEST",
  ERROR: "PRECHECK ERROR",
};

const DECISION_SUBTEXT: Record<BoardState, string> = {
  READY: "Reproduction can proceed.",
  NO_TESTS: "Reproduction cannot establish a failing test.",
  NOT_PREPARED: "Repair pipeline blocked until the environment is prepared.",
  NO_MANIFEST: "Repair pipeline blocked until the environment is prepared.",
  NO_TEST_RUNNER: "Repair pipeline blocked until the environment is prepared.",
  UNSUPPORTED: "Repository is outside the supported repair environment.",
  ERROR: "The environment could not be determined — the repair pipeline halted pending diagnosis.",
};

/** Index of the stage that failed, or `null` when the circuit is intact. Set
 * per state rather than derived from field presence, so the break always
 * lands where the backend actually classified the failure — even on a stage
 * it couldn't caption (see the "never invents" case below: a NOT_PREPARED
 * report with `test_runner: null` still breaks visibly at the runner stage,
 * it just can't say more than "not measured" about it). */
const BREAK_INDEX: Record<BoardState, number | null> = {
  READY: null,
  NO_TESTS: null,
  NO_MANIFEST: 0,
  UNSUPPORTED: 1,
  NO_TEST_RUNNER: 2,
  NOT_PREPARED: 2,
  ERROR: null,
};

const STAGE_LABELS = ["MANIFEST", "LANGUAGE", "TEST RUNNER", "TEST COLLECTION"] as const;
const STAGE_NUMERALS = ["①", "②", "③", "④"] as const;

function boardState(
  env: EnvironmentReport | null | undefined,
  probeError: boolean,
): BoardState | null {
  if (probeError) return "ERROR";
  if (!env || !env.status) return null;
  if (env.status === "ready") {
    return env.tests_collected === 0 ? "NO_TESTS" : "READY";
  }
  if (env.status === "not_prepared") return "NOT_PREPARED";
  if (env.status === "unsupported") return "UNSUPPORTED";
  if (env.status === "no_manifest") return "NO_MANIFEST";
  if (env.status === "no_test_runner") return "NO_TEST_RUNNER";
  // A status string the frontend does not recognise is itself a fact worth
  // surfacing honestly rather than silently defaulting to a known state.
  return "ERROR";
}

type LinkTone = "ok" | "unknown" | "broken" | "skipped" | "warn";

interface Link {
  label: string;
  numeral: string;
  value: string;
  tone: LinkTone;
}

function capitalize(s: string): string {
  return s.length ? s[0].toUpperCase() + s.slice(1) : s;
}

/** Builds the four gate stages from the report plus the declared break index.
 * A stage strictly after the break is always captioned NOT ATTEMPTED — its
 * real field value (however it happens to be encoded, `null` or otherwise)
 * is never shown, because showing it would imply the probe looked. */
function buildLinks(env: EnvironmentReport | null, state: BoardState): Link[] {
  const breakIndex = BREAK_INDEX[state];
  const manifest = env?.manifests?.[0]?.path ?? null;
  const manifestCount = env?.manifests?.length ?? 0;

  const computed: (string | null)[] = [
    manifest != null
      ? manifestCount > 1
        ? `${manifest} (+${manifestCount - 1})`
        : manifest
      : state === "NO_MANIFEST"
        ? "Unavailable"
        : null,
    env?.language ? capitalize(env.language) : null,
    env?.test_runner ?? (state === "NO_TEST_RUNNER" ? "No runner found" : null),
    env?.tests_collected == null
      ? null
      : env.tests_collected === 0
        ? "None detected"
        : `${env.tests_collected} collected`,
  ];

  return STAGE_LABELS.map((label, i) => {
    let tone: LinkTone;
    if (breakIndex !== null && i > breakIndex) tone = "skipped";
    else if (breakIndex !== null && i === breakIndex) tone = "broken";
    else if (i === 3 && state === "NO_TESTS") tone = "warn";
    else tone = computed[i] != null ? "ok" : "unknown";

    const value = tone === "skipped" ? "NOT ATTEMPTED" : (computed[i] ?? "Not measured");
    return { label, numeral: STAGE_NUMERALS[i], value, tone };
  });
}

const LINK_ICON: Record<LinkTone, typeof Check> = {
  ok: Check,
  unknown: Minus,
  broken: X,
  skipped: Minus,
  warn: AlertTriangle,
};

const LINK_NODE_TONE: Record<LinkTone, string> = {
  ok: "border-status-completed/50 bg-status-completed-bg text-status-completed",
  unknown: "border-border bg-surface-muted text-ink-soft/60",
  broken: "border-status-failed/60 bg-status-failed-bg text-status-failed",
  skipped: "border-border/50 bg-transparent text-ink-soft/35",
  warn: "border-status-retry/50 bg-status-retry-bg text-status-retry",
};

/** Whether the segment leading *into* this stage (from the previous one)
 * continues the circuit. Only "skipped" and "broken" interrupt it — an
 * "unknown" stage is one the probe still passed through, it just has nothing
 * to caption. */
function carriesCurrent(tone: LinkTone): boolean {
  return tone === "ok" || tone === "unknown" || tone === "warn";
}

function ChainNode({ link }: { link: Link }) {
  const Icon = LINK_ICON[link.tone];
  const dim = link.tone === "skipped";
  return (
    <div className="flex min-w-0 flex-1 flex-col items-center gap-1 px-1 text-center sm:w-full">
      <span
        className={`flex items-center gap-1 text-[9px] font-semibold tracking-wider ${
          dim ? "text-ink-soft/35" : "text-ink-soft"
        }`}
      >
        <span aria-hidden>{link.numeral}</span>
        {link.label}
      </span>
      <span
        className={`flex h-6 w-6 items-center justify-center rounded-full border ${LINK_NODE_TONE[link.tone]}`}
      >
        <Icon className="h-3.5 w-3.5" strokeWidth={2.5} />
      </span>
      <span
        className={`max-w-full truncate font-mono text-[11px] ${
          link.tone === "skipped"
            ? "text-ink-soft/35"
            : link.tone === "broken"
              ? "font-semibold text-status-failed"
              : link.tone === "warn"
                ? "font-semibold text-status-retry"
                : link.tone === "unknown"
                  ? "text-ink-soft/60"
                  : "text-ink"
        }`}
        title={link.value}
      >
        {link.value}
      </span>
    </div>
  );
}

/** The segment between two stages. `from` is the tone of the stage this
 * connector leaves; that alone decides whether it carries current, is the cut
 * itself, or is dead wire — direction-agnostic so the same logic drives both
 * the horizontal (desktop) and vertical (narrow-screen) orientation. */
function Connector({ from }: { from: LinkTone }) {
  if (from === "broken") {
    return (
      <div
        className="flex h-6 w-full flex-none items-center justify-center gap-0.5 sm:h-0.5 sm:w-6 sm:flex-row sm:gap-1"
        aria-hidden
      >
        <span className="h-2 w-0.5 flex-none bg-status-failed/60 sm:h-0.5 sm:w-2" />
        <span className="flex-none text-[11px] leading-none font-bold text-status-failed">╳</span>
        <span className="h-2 w-0.5 flex-none border-l-2 border-dashed border-border/50 sm:h-0.5 sm:w-2 sm:border-t-2 sm:border-l-0" />
      </div>
    );
  }
  return (
    <div
      className={`h-5 w-0.5 flex-none sm:h-0.5 sm:w-6 ${
        carriesCurrent(from)
          ? "bg-status-completed/50"
          : "border-l-2 border-dashed border-border/40 sm:border-t-2 sm:border-l-0"
      }`}
      aria-hidden
    />
  );
}

function CopyCommandButton({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(command);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard access can be denied by the browser; the command is
          // still visible and selectable as text, so this is not fatal.
        }
      }}
      className="inline-flex flex-none items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-1 text-[10px]
        font-medium text-ink-soft transition-colors hover:bg-surface-muted focus-visible:outline focus-visible:outline-2
        focus-visible:outline-offset-2 focus-visible:outline-primary"
      aria-label="Copy suggested setup command"
    >
      <Copy className="h-3 w-3" />
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

export function EnvironmentPreflightBoard({
  environment,
  probeError,
}: {
  environment: WorkspaceHeaderModel["environment"];
  probeError?: boolean;
}) {
  const state = boardState(environment, !!probeError);
  if (!state) return null;

  const env = environment ?? null;
  const blocking = state !== "READY" && state !== "NO_TESTS" && state !== "ERROR";
  const missing = env?.missing_imports ?? [];
  const links = state === "ERROR" ? [] : buildLinks(env, state);
  const decisionTone =
    state === "READY"
      ? "text-status-completed"
      : state === "NO_TESTS"
        ? "text-status-retry"
        : "text-status-failed";
  const DecisionIcon = state === "READY" ? Check : AlertTriangle;

  // No own header, border, or card background here: this renders inside
  // AgentCard's "Live view" box, which already sits under AgentCard's own
  // agent-name / status-badge / purpose header — repeating either would be
  // the exact duplicated chrome this pass exists to remove.
  return (
    <div role="status" aria-label={`Environment preflight: ${STATE_LABEL[state]}`}>
      {/* The gate: four probe stages, severed at the point of failure. Every
          stage after a break renders dashed and dimmed — reached by nothing,
          captioned NOT ATTEMPTED rather than left to imply a zero. */}
      {links.length > 0 && (
        <div className="flex flex-col items-stretch sm:flex-row sm:items-start">
          {links.map((link, i) => (
            <div key={link.label} className="flex flex-col items-stretch sm:flex-1 sm:flex-row">
              <ChainNode link={link} />
              {i < links.length - 1 && <Connector from={link.tone} />}
            </div>
          ))}
        </div>
      )}

      {/* The verdict — one line, sourced entirely from where the gate above
          landed. No second decision statement anywhere else on the card. */}
      <div className={`mt-3 flex items-center gap-1.5 ${decisionTone}`}>
        <DecisionIcon className="h-3.5 w-3.5 flex-none" strokeWidth={2.5} />
        <span className="text-[11px] font-semibold tracking-wide">{DECISION_TITLE[state]}</span>
        <span className="text-ink-soft/40">·</span>
        <span className="text-[11px] text-ink-soft">{DECISION_SUBTEXT[state]}</span>
      </div>

      {/* Diagnosis — rendered only when there is something to diagnose. */}
      {state === "ERROR" && (
        <div className="mt-3 rounded-md border border-status-failed/30 bg-status-failed-bg px-3 py-2.5 text-[12px] text-status-failed">
          The environment precheck did not reach a verdict — it errored before it could classify
          this repository. See the run's error log for detail.
        </div>
      )}

      {blocking && env?.reason && (
        <div className="mt-3 rounded-md border border-status-failed/30 bg-status-failed-bg px-3 py-2.5">
          <div className="text-[10px] font-semibold tracking-wider text-status-failed uppercase">
            Environment blocked
          </div>
          <div className="mt-1 text-[12px] text-status-failed/90">{env.reason}</div>
          {missing.length > 0 && (
            <div className="mt-1.5 font-mono text-[10px] text-status-failed/75">
              Missing: {missing.join(", ")}
            </div>
          )}
          {env.suggested_command && (
            <div className="mt-2.5 border-t border-status-failed/20 pt-2.5">
              <div className="text-[10px] font-semibold tracking-wider text-status-failed uppercase">
                Suggested command
              </div>
              <div className="mt-1 flex items-center gap-2">
                <code className="flex min-w-0 flex-1 items-center gap-1.5 truncate rounded bg-surface px-2 py-1 font-mono text-[11px] text-ink">
                  <span className="flex-none text-ink-soft/50" aria-hidden>
                    $
                  </span>
                  <span className="truncate">{env.suggested_command}</span>
                </code>
                <CopyCommandButton command={env.suggested_command} />
              </div>
              <div className="mt-1.5 text-[10px] text-status-failed/70">
                ProoFix does not execute this command automatically.
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
