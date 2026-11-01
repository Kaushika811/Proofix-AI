import type { AgentStatus } from "./data";

const STATUS_LABEL: Record<AgentStatus, string> = {
  completed: "Completed",
  running: "Running",
  retry: "Retrying",
  failed: "Failed",
  draft: "Draft PR",
  skipped: "Skipped",
  blocked: "Blocked",
};

const STATUS_CLASSES: Record<AgentStatus, string> = {
  completed: "bg-status-completed-bg text-status-completed",
  running: "bg-status-running-bg text-status-running",
  retry: "bg-status-retry-bg text-status-retry",
  failed: "bg-status-failed-bg text-status-failed",
  draft: "bg-status-draft-bg text-status-draft",
  skipped: "bg-surface-muted text-ink-soft",
  blocked: "bg-status-blocked-bg text-status-blocked",
};

export function StatusBadge({
  status,
  pulse = false,
  label,
}: {
  status: AgentStatus;
  pulse?: boolean;
  label?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_CLASSES[status]}`}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full bg-current ${pulse ? "animate-soft-pulse" : ""}`}
      />
      {label ?? STATUS_LABEL[status]}
    </span>
  );
}
