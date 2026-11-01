/**
 * Compact strip of the agents the run has already passed through.
 *
 * The execution journal shows one agent at a time so the active stage owns the
 * viewport. That only works if the stages behind it stay reachable — this rail
 * is that affordance: a dense, glanceable record of what has run, where each
 * chip re-opens its agent on the stage.
 */
import { Check, Minus, X, RotateCcw, Ban } from "lucide-react";
import type { LiveAgent } from "./useExecutionRun";
import type { AgentStatus } from "./data";

interface Props {
  agents: LiveAgent[];
  /** Index the pipeline is currently executing. */
  activeIndex: number;
  /** Index currently on the stage — differs from `activeIndex` while reviewing. */
  focusIndex: number;
  onFocus: (index: number) => void;
  /** Run has settled; every stage is revealed rather than only those reached. */
  done: boolean;
}

/** Chip glyph per outcome. Running has no glyph — the pulsing dot carries it. */
function StatusGlyph({ status }: { status: AgentStatus }) {
  if (status === "failed") return <X className="h-3 w-3" strokeWidth={2.5} />;
  if (status === "blocked") return <Ban className="h-3 w-3" strokeWidth={2.5} />;
  if (status === "skipped") return <Minus className="h-3 w-3" strokeWidth={2.5} />;
  if (status === "retry") return <RotateCcw className="h-3 w-3" strokeWidth={2.5} />;
  if (status === "running")
    return (
      <span className="h-1.5 w-1.5 rounded-full bg-status-running animate-soft-pulse" aria-hidden />
    );
  return <Check className="h-3 w-3" strokeWidth={2.5} />;
}

const TONE: Record<AgentStatus, string> = {
  completed: "border-status-completed/40 text-status-completed bg-status-completed-bg",
  draft: "border-status-draft/40 text-status-draft bg-status-draft-bg",
  running: "border-status-running/50 text-status-running bg-status-running-bg",
  retry: "border-status-retry/40 text-status-retry bg-status-retry-bg",
  failed: "border-status-failed/40 text-status-failed bg-status-failed-bg",
  skipped: "border-border text-ink-soft bg-surface-muted",
  blocked: "border-status-blocked/40 text-status-blocked bg-status-blocked-bg",
};

export function AgentRail({ agents, activeIndex, focusIndex, onFocus, done }: Props) {
  // Mid-run the rail is the history, so it stops at the active stage; nothing
  // ahead has happened yet and showing it would spoil the sequence. Once the
  // run settles every stage is history and the whole pipeline is browsable.
  const visible = done ? agents : agents.slice(0, activeIndex + 1);
  if (visible.length === 0) return null;

  return (
    <nav
      aria-label="Pipeline stages"
      className="flex flex-wrap gap-1.5 rounded-xl border border-border bg-surface-muted/60 p-2"
    >
      {visible.map((entry, i) => {
        const onStage = i === focusIndex;
        return (
          <button
            key={entry.id}
            type="button"
            onClick={() => onFocus(i)}
            aria-current={onStage ? "true" : undefined}
            title={entry.agent}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs font-medium
              transition-[background-color,border-color,box-shadow,transform] duration-150 ease-out
              hover:-translate-y-px hover:shadow-sm
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
              ${TONE[entry.liveStatus]}
              ${onStage ? "ring-2 ring-ring/60" : ""}`}
          >
            <StatusGlyph status={entry.liveStatus} />
            <span className="max-w-[13ch] truncate">{entry.agent}</span>
          </button>
        );
      })}
    </nav>
  );
}
