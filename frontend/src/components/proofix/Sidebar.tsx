import {
  LayoutGrid,
  Settings,
  Plus,
  Sun,
  Moon,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Folder,
} from "lucide-react";
import { useEffect, useState } from "react";

export interface SidebarRun {
  id: string;
  name: string;
  status: "running" | "completed" | "failed" | "draft";
  time: string;
}

export interface SidebarRepo {
  name: string;
  runs: SidebarRun[];
}

const PRIMARY_NAV = [{ key: "home", label: "Home", icon: LayoutGrid }] as const;

function ProoFixMark({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <path
        d="M12 2.2 21.8 12 12 21.8 2.2 12Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M8.4 12.2l2.6 2.6 4.6-5.4"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">("dark");

  useEffect(() => {
    const stored = localStorage.getItem("proofix-theme") as "light" | "dark" | null;
    const initial = stored ?? "dark";
    setTheme(initial);
    document.documentElement.classList.toggle("dark", initial === "dark");
  }, []);

  const toggle = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    localStorage.setItem("proofix-theme", next);
    document.documentElement.classList.toggle("dark", next === "dark");
  };

  return { theme, toggle };
}

function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle theme"
      className="relative inline-flex h-7 w-[60px] items-center rounded-full bg-surface-muted hover:brightness-110"
    >
      <span
        className={`absolute top-0.5 flex h-6 w-6 items-center justify-center rounded-full bg-surface text-ink shadow-sm transition-transform duration-[250ms] ease-out ${
          isDark ? "translate-x-[32px]" : "translate-x-0.5"
        }`}
      >
        {isDark ? (
          <Moon className="h-3.5 w-3.5" strokeWidth={1.75} />
        ) : (
          <Sun className="h-3.5 w-3.5" strokeWidth={1.75} />
        )}
      </span>
      <span className="relative z-10 flex h-7 w-1/2 items-center justify-center text-ink-soft">
        <Sun className="h-3 w-3" strokeWidth={1.75} />
      </span>
      <span className="relative z-10 flex h-7 w-1/2 items-center justify-center text-ink-soft">
        <Moon className="h-3 w-3" strokeWidth={1.75} />
      </span>
    </button>
  );
}

function ThemeToggleCompact() {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle theme"
      title={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
    >
      {isDark ? (
        <Moon className="h-3.5 w-3.5" strokeWidth={1.75} />
      ) : (
        <Sun className="h-3.5 w-3.5" strokeWidth={1.75} />
      )}
    </button>
  );
}

function StatusDot({ status }: { status: SidebarRun["status"] }) {
  if (status === "running") {
    return (
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-status-running opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-status-running" />
      </span>
    );
  }
  const color =
    status === "completed"
      ? "bg-status-completed"
      : status === "failed"
        ? "bg-status-failed"
        : "bg-status-draft";
  return <span className={`h-2 w-2 rounded-full ${color}`} />;
}

function RepoNode({
  repo,
  activeRunId,
  selectedRunId,
  onSelectRun,
  defaultOpen,
}: {
  repo: SidebarRepo;
  activeRunId: string | null;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="group flex w-full items-center gap-1 rounded-md px-1.5 py-1 text-[12px] text-ink-soft transition-all duration-150 hover:bg-surface-muted hover:text-ink"
      >
        <ChevronRight
          className={`h-3 w-3 transition-transform duration-[250ms] ease-out ${open ? "rotate-90" : ""}`}
          strokeWidth={2}
        />
        <Folder
          className="h-3 w-3 transition-colors duration-150 group-hover:text-primary/80"
          strokeWidth={1.75}
        />
        <span className="truncate font-mono">{repo.name}</span>
      </button>
      <div
        className={`grid overflow-hidden transition-[grid-template-rows,opacity] duration-[250ms] ease-out ${
          open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        }`}
      >
        <ul className="min-h-0 ml-4 mt-0.5 space-y-0.5 border-l border-border/60 pl-2">
          {repo.runs.length === 0 && (
            <li className="px-1.5 py-1 text-[11px] italic text-ink-soft/70">No runs yet</li>
          )}
          {repo.runs.map((run) => {
            const isSelected = selectedRunId === run.id;
            const isActive = activeRunId === run.id;
            return (
              <li key={run.id}>
                <button
                  type="button"
                  onClick={() => onSelectRun(run.id)}
                  className={`relative flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left text-[12px] transition-all duration-150 ${
                    isSelected
                      ? "bg-surface-muted text-ink"
                      : "text-ink-soft hover:bg-surface-muted/60 hover:text-ink hover:translate-x-[1px]"
                  }`}
                  title={run.time}
                >
                  {isSelected && (
                    <span
                      aria-hidden
                      className="absolute inset-y-1 -left-[9px] w-[2px] rounded-full bg-primary/70"
                    />
                  )}
                  <StatusDot status={run.status} />
                  <span
                    className={`min-w-0 flex-1 truncate ${isActive ? "font-medium text-ink" : ""}`}
                  >
                    {run.name}
                  </span>
                  <span className="shrink-0 text-[10px] text-ink-soft/70">{run.time}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </li>
  );
}

export function Sidebar({
  onNewRun,
  onNavigate,
  currentView,
  repositories,
  activeRunId,
  selectedRunId,
  onSelectRun,
}: {
  onNewRun?: () => void;
  onNavigate?: (key: "home" | "runs" | "settings") => void;
  currentView?: string;
  repositories: SidebarRepo[];
  activeRunId: string | null;
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("proofix-sidebar-collapsed");
    if (stored !== null) setCollapsed(stored === "1");
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("proofix-sidebar-collapsed", next ? "1" : "0");
      return next;
    });
  };

  if (collapsed) {
    return (
      <aside className="hidden h-screen w-[56px] shrink-0 flex-col border-r border-border bg-surface lg:flex sticky top-0">
        <div className="flex flex-col items-center gap-2 px-2 pt-3 pb-2">
          <ProoFixMark className="h-[20px] w-[20px] text-ink" />
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label="Expand sidebar"
            title="Expand sidebar"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
          >
            <ChevronsRight className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
        </div>

        <div className="px-2 pb-2">
          <button
            type="button"
            onClick={onNewRun}
            aria-label="Analyze Repository"
            title="Analyze Repository"
            className="flex h-9 w-full items-center justify-center rounded-lg border border-border bg-surface text-ink transition-colors hover:border-primary/30 hover:bg-surface-muted"
          >
            <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
        </div>

        <nav className="px-2">
          <ul className="space-y-0.5">
            {PRIMARY_NAV.map(({ key, label, icon: Icon }) => {
              const active = currentView === key;
              return (
                <li key={key}>
                  <button
                    type="button"
                    onClick={() => onNavigate?.(key)}
                    aria-label={label}
                    title={label}
                    className={`flex h-9 w-full items-center justify-center rounded-lg transition-colors ${
                      active
                        ? "bg-surface-muted font-medium text-ink"
                        : "text-ink-soft hover:bg-surface-muted hover:text-ink"
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="mt-4 min-h-0 flex-1" />

        <div className="flex flex-col items-center gap-1 border-t border-border px-2 py-3">
          <ThemeToggleCompact />
          <button
            type="button"
            onClick={() => onNavigate?.("settings")}
            aria-label="Settings"
            title="Settings"
            className={`flex h-8 w-8 items-center justify-center rounded-lg transition-colors ${
              currentView === "settings"
                ? "bg-surface-muted text-ink"
                : "text-ink-soft hover:bg-surface-muted hover:text-ink"
            }`}
          >
            <Settings className="h-3.5 w-3.5" strokeWidth={1.75} />
          </button>
        </div>
      </aside>
    );
  }

  return (
    <aside className="hidden h-screen w-[208px] shrink-0 flex-col border-r border-border bg-surface lg:flex sticky top-0">
      <div className="flex items-center justify-between px-3 pt-3 pb-2">
        <div className="flex items-center gap-1.5">
          <ProoFixMark className="h-[20px] w-[20px] text-ink" />
          <div className="text-[13px] font-semibold tracking-tight text-ink">ProoFix</div>
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label="Collapse sidebar"
          title="Collapse sidebar"
          className="flex h-6 w-6 items-center justify-center rounded-md text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
        >
          <ChevronsLeft className="h-3.5 w-3.5" strokeWidth={1.75} />
        </button>
      </div>

      <div className="px-3 pb-2">
        <button
          type="button"
          onClick={onNewRun}
          className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-border bg-surface px-2.5 py-1.5 text-[13px] font-medium text-ink transition-colors hover:border-primary/30 hover:bg-surface-muted"
        >
          <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
          Analyze Repository
        </button>
      </div>

      <nav className="px-2">
        <ul className="space-y-0.5">
          {PRIMARY_NAV.map(({ key, label, icon: Icon }) => {
            const active = currentView === key;
            return (
              <li key={key}>
                <button
                  type="button"
                  onClick={() => onNavigate?.(key)}
                  className={`group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition-colors ${
                    active
                      ? "bg-surface-muted font-medium text-ink"
                      : "text-ink-soft hover:bg-surface-muted hover:text-ink"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
                  {label}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        <div className="px-1.5 pb-1.5 pt-2 text-[10px] font-semibold uppercase tracking-wider text-ink-soft">
          Repositories
        </div>
        <ul className="space-y-0.5">
          {repositories.map((repo, i) => (
            <RepoNode
              key={repo.name}
              repo={repo}
              activeRunId={activeRunId}
              selectedRunId={selectedRunId}
              onSelectRun={onSelectRun}
              defaultOpen={i === 0}
            />
          ))}
        </ul>
      </div>

      <div className="flex flex-col items-start gap-2 border-t border-border px-3 py-3">
        <ThemeToggle />
        <button
          type="button"
          onClick={() => onNavigate?.("settings")}
          className={`flex w-full items-center gap-2.5 rounded-lg px-1 py-1 text-left text-[13px] transition-colors ${
            currentView === "settings" ? "text-ink" : "text-ink-soft hover:text-ink"
          }`}
        >
          <Settings className="h-3.5 w-3.5" strokeWidth={1.75} />
          Settings
        </button>
        <div className="text-[11px] text-ink-soft">v0.4.2 · workspace</div>
      </div>
    </aside>
  );
}
