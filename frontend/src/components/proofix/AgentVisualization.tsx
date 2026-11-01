import { useEffect, useState } from "react";
import { Check, Bug, GitMerge, Skull, ShieldCheck, AlertTriangle, Terminal } from "lucide-react";
import { prefersReducedMotion } from "@/hooks/useCountUp";
import type { LiveAgent } from "./useExecutionRun";
import type {
  AgentVisualizationPayload,
  BlastPayload,
  ContextPayload,
  DepsPayload,
  IntelligencePayload,
  MergePayload,
  MutationPayload,
  PatchPayload,
  PlannerPayload,
  ReproducePayload,
  RepoIntelPayload,
  RootCausePayload,
  StaticPayload,
} from "./visualizationTypes";

/**
 * Per-agent execution visualization. Each agent renders a distinct scene that
 * progresses with `entry.visibleLines / entry.lines.length`. Visuals are
 * intentionally lightweight (no heavy libs) and reuse design tokens.
 *
 * Every concrete viz is purely a renderer over its typed payload — no
 * inline literals, no hardcoded findings/metrics/patches. Payloads are
 * provided by `AgentEntry.visualization` (sourced via runService).
 */
export function AgentVisualization({ entry }: { entry: LiveAgent }) {
  const total = entry.lines.length || 1;
  const progress = Math.min(1, entry.visibleLines / total);
  const done =
    entry.liveStatus === "completed" ||
    entry.liveStatus === "draft" ||
    entry.liveStatus === "failed";

  const payload: AgentVisualizationPayload | undefined = entry.visualization;
  if (!payload) return null;

  switch (payload.kind) {
    case "intelligence":
      return <IntelligenceViz data={payload.data} progress={progress} done={done} />;
    case "context":
      return <ContextViz data={payload.data} progress={progress} done={done} />;
    case "repo-intel":
      return <RepoIntelViz data={payload.data} progress={progress} done={done} />;
    case "deps":
      return <DepsViz data={payload.data} progress={progress} done={done} />;
    case "static":
      return <StaticViz data={payload.data} progress={progress} done={done} />;
    case "reproduce":
      return <ReproduceViz data={payload.data} progress={progress} done={done} />;
    case "root":
      return <RootCauseViz data={payload.data} progress={progress} done={done} />;
    case "blast":
      return <BlastViz data={payload.data} progress={progress} done={done} />;
    case "planner":
      return <PlannerViz data={payload.data} progress={progress} done={done} />;
    case "patch":
      return <PatchViz data={payload.data} />;
    case "mutation":
      return (
        <MutationViz
          data={payload.data}
          progress={progress}
          failed={entry.liveStatus === "failed"}
          done={done}
        />
      );
    case "merge":
      return <MergeViz data={payload.data} progress={progress} done={done} />;
    default:
      return null;
  }
}

function Frame({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-3">
      <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
        {label}
      </div>
      {children}
    </div>
  );
}

/**
 * Smoothly counts a number up to `value` while `active`.
 *
 * Distinct from `@/hooks/useCountUp`, which animates once on mount; this one is
 * gated on the agent's scene having finished, and resets when it has not. Both
 * must honour `prefers-reduced-motion` — a `requestAnimationFrame` loop is not
 * an animation as far as the stylesheet's reduced-motion block is concerned, so
 * this one counted up regardless of the setting (B-F06).
 */
function useCountUp(value: number, active: boolean, duration = 700) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!active) {
      setN(0);
      return;
    }
    if (prefersReducedMotion()) {
      setN(value);
      return;
    }
    const start = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / duration);
      setN(Math.round(p * value));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, active, duration]);
  return n;
}

/* ============================================================
 * A5.5 — Context Engineering
 *
 * Two claims, and the scene exists to make both checkable: the repository was
 * reduced to this much, and nothing secret left the process. The second is why
 * this card matters more than its size suggests — the privacy guard is the only
 * point in the pipeline where secrets are masked before an LLM call, and its
 * status is the evidence.
 * ============================================================ */
function ContextViz({
  data,
  progress,
  done,
}: {
  data: ContextPayload;
  progress: number;
  done: boolean;
}) {
  const functions = useCountUp(data.contextFunctions, done);
  const files = useCountUp(data.contextFiles, done);
  const ranked = useCountUp(data.filesRanked, done);

  if (data.skipped) {
    return (
      <Frame label="Context Package">
        <div className="text-[11px] text-ink-soft">
          No repair target resolved — the patch generator kept its own context path. Nothing was
          reduced and nothing was scanned for secrets.
        </div>
      </Frame>
    );
  }

  // `failed` is not a worse `masked`; it means the guard errored and nothing
  // may be assumed about what reached the model. It gets the alarming tone.
  const guard = {
    clean: {
      tone: "border-status-completed/30 bg-status-completed-bg text-status-completed",
      icon: <ShieldCheck className="h-3 w-3" />,
      text: "No secrets detected in the context sent to the model",
    },
    masked: {
      tone: "border-status-retry/30 bg-status-retry-bg text-status-retry",
      icon: <ShieldCheck className="h-3 w-3" />,
      text: `${data.redactions} value(s) masked before the prompt left the process`,
    },
    failed: {
      tone: "border-status-failed/30 bg-status-failed-bg text-status-failed",
      icon: <AlertTriangle className="h-3 w-3" />,
      text: "The privacy guard errored — no assurance can be given about this context",
    },
  }[data.privacyGuardStatus];

  // Width of the kept portion. Guarded because a zero original would divide by
  // zero, and an unmeasured reduction must not draw a full bar.
  const keptFraction =
    data.originalTokens > 0 ? Math.min(1, data.reducedTokens / data.originalTokens) : 1;
  const shown = Math.min(1, progress / 0.8);

  return (
    <Frame label="Context Package">
      <div className="font-mono text-[11px] text-ink">
        {data.targetFile || "—"}
        {data.targetFunction && <span className="text-ink-soft"> :: {data.targetFunction}</span>}
      </div>

      <div className="mt-3">
        <div className="flex items-baseline justify-between text-[10px] text-ink-soft">
          <span>Prompt context</span>
          <span className="font-mono">
            {data.originalTokens.toLocaleString()} → {data.reducedTokens.toLocaleString()} tokens
          </span>
        </div>
        <div className="mt-1 h-2.5 w-full overflow-hidden rounded-full bg-surface-muted">
          <div
            className="h-full rounded-full bg-primary transition-all duration-700"
            style={{ width: `${keptFraction * shown * 100}%` }}
          />
        </div>
        <div className="mt-1 text-[10px] text-ink-soft">
          {data.tokenReduction === null ? (
            "Reduction not measured"
          ) : (
            <>
              <span className="font-mono text-ink">{Math.round(data.tokenReduction * 100)}%</span>{" "}
              of the original context was left out
            </>
          )}
        </div>
      </div>

      <div
        className={`mt-3 flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[11px] ${guard.tone}`}
      >
        {guard.icon}
        <span className="font-medium">{guard.text}</span>
      </div>

      {data.degraded && (
        <div className="mt-2 rounded-md border border-border bg-surface-muted/60 px-2 py-1.5 text-[10px] text-ink-soft">
          Budget exceeded — the package was trimmed to fit, so it is smaller than the ranking asked
          for.
        </div>
      )}

      <div className="mt-3 grid grid-cols-3 gap-1.5">
        {[
          { label: "Files Ranked", value: ranked },
          { label: "Context Files", value: files },
          { label: "Functions", value: functions },
        ].map((m) => (
          <div
            key={m.label}
            className="rounded-md border border-border bg-surface-muted/60 px-2 py-1.5"
          >
            <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
              {m.label}
            </div>
            <div className="font-mono text-xs font-semibold text-ink">{m.value}</div>
          </div>
        ))}
      </div>
    </Frame>
  );
}

/* ============================================================
 * A0.5 — Repository Indexing
 *
 * The one thing this layer does that no other agent does is *not* work: it
 * reuses an index built by a previous run. So the scene leads with how the
 * index was obtained, and the timing bar shows what had to be rebuilt. A cache
 * hit draws no bar at all, because no phase ran.
 * ============================================================ */
function IntelligenceViz({
  data,
  progress,
  done,
}: {
  data: IntelligencePayload;
  progress: number;
  done: boolean;
}) {
  const { metrics, phases } = data;
  const phaseTotal = phases.reduce((sum, p) => sum + p.ms, 0);

  const nodes = useCountUp(metrics.nodes, done);
  const edges = useCountUp(metrics.edges, done);
  const callables = useCountUp(metrics.callables, done);
  const commits = useCountUp(metrics.commits, done);
  const remembered = useCountUp(metrics.rememberedRepairs, done);

  // Reuse is the good outcome here, so a cache hit reads as success and a full
  // rebuild as ordinary work — neither is a failure.
  const modeTone =
    data.mode === "cache hit"
      ? "border-status-completed/30 bg-status-completed-bg text-status-completed"
      : "border-border bg-surface-muted text-ink-soft";

  return (
    <Frame label="Repository Index">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${modeTone}`}
        >
          {data.mode}
        </span>
        <span className="text-[11px] text-ink-soft">{data.modeDetail}</span>
      </div>

      {phaseTotal > 0 ? (
        <div className="mt-3">
          <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-muted">
            {phases.map((phase, i) => (
              <div
                key={phase.label}
                className="h-full bg-primary transition-all duration-700"
                style={{
                  // One hue, stepped down — these are phases of a single
                  // process, not unrelated categories.
                  opacity: 1 - i * 0.15,
                  width: `${(phase.ms / phaseTotal) * 100 * Math.min(1, progress / 0.8)}%`,
                }}
              />
            ))}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
            {phases.map((phase, i) => (
              <span key={phase.label} className="flex items-center gap-1 text-[10px] text-ink-soft">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-primary"
                  style={{ opacity: 1 - i * 0.15 }}
                />
                {phase.label}
                <span className="font-mono text-ink">{phase.ms}ms</span>
              </span>
            ))}
            <span className="ml-auto font-mono text-[10px] text-ink-soft">
              total {data.totalMs}ms
            </span>
          </div>
        </div>
      ) : (
        <div className="mt-3 rounded-md border border-border bg-surface-muted/60 px-2 py-1.5 text-[11px] text-ink-soft">
          No index phase ran — nothing needed rebuilding.
        </div>
      )}

      <div className="mt-3 grid grid-cols-3 gap-1.5 md:grid-cols-5">
        {[
          { label: "Nodes", value: nodes },
          { label: "Edges", value: edges },
          { label: "Callables", value: callables },
          { label: "Commits", value: commits },
          { label: "Remembered", value: remembered },
        ].map((m) => (
          <div
            key={m.label}
            className="rounded-md border border-border bg-surface-muted/60 px-2 py-1.5"
          >
            <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
              {m.label}
            </div>
            <div className="font-mono text-xs font-semibold text-ink">{m.value}</div>
          </div>
        ))}
      </div>

      {data.capabilities.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {data.capabilities.map((name) => (
            <span
              key={name}
              className="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[10px] text-ink-soft"
            >
              {name}
            </span>
          ))}
        </div>
      )}
    </Frame>
  );
}

/* ============================================================
 * A1 — Semantic Repository Mapper
 * ============================================================ */
function RepoIntelViz({
  data,
  progress,
  done,
}: {
  data: RepoIntelPayload;
  progress: number;
  done: boolean;
}) {
  const repoFiles = data.files;
  const totalNodes = data.graphNodes;

  const scanEnd = 0.45;
  const astEnd = 0.7;
  const fileProg = Math.min(1, progress / scanEnd);
  const astProg = Math.max(0, Math.min(1, (progress - scanEnd) / (astEnd - scanEnd)));
  const graphProg = Math.max(0, Math.min(1, (progress - astEnd) / (1 - astEnd)));

  const filesIndexed = Math.floor(fileProg * repoFiles.length);
  const currentScanning = fileProg < 1 ? filesIndexed : -1;

  const nodesShown = Math.round(graphProg * totalNodes);

  const fileCount = useCountUp(data.metrics.files, done);
  const imports = useCountUp(data.metrics.imports, done);
  const deps = useCountUp(data.metrics.dependencies, done);
  const roles = useCountUp(data.metrics.semanticRoles, done);

  const nodes = Array.from({ length: totalNodes }).map((_, i) => {
    const angle = (i / totalNodes) * Math.PI * 2 - Math.PI / 2;
    const r = 38 + (i % 3) * 7;
    return { x: 90 + Math.cos(angle) * r, y: 60 + Math.sin(angle) * r };
  });

  return (
    <Frame label="Semantic Intent Graph">
      <div className="grid gap-3 md:grid-cols-[180px_1fr]">
        <ul className="space-y-1 font-mono text-[11px]">
          <li className="text-ink-soft">repo/</li>
          {repoFiles.map((f, i) => {
            const state =
              i < filesIndexed ? "indexed" : i === currentScanning ? "scanning" : "waiting";
            const astBadge = i < filesIndexed && astProg > 0;
            return (
              <li key={f.name} className="flex items-center justify-between gap-2 pl-3">
                <span className="flex items-center gap-1.5 truncate">
                  {state === "indexed" ? (
                    <Check className="h-3 w-3 shrink-0 text-status-completed" strokeWidth={3} />
                  ) : state === "scanning" ? (
                    <span className="h-2 w-2 shrink-0 rounded-full bg-status-running animate-soft-pulse" />
                  ) : (
                    <span className="h-2 w-2 shrink-0 rounded-full border border-border" />
                  )}
                  <span
                    className={
                      state === "indexed"
                        ? "text-ink"
                        : state === "scanning"
                          ? "text-status-running"
                          : "text-ink-soft opacity-60"
                    }
                  >
                    {f.name}
                  </span>
                </span>
                {astBadge && (
                  <span className="animate-line-in rounded bg-surface-muted px-1 py-0.5 text-[9px] text-ink-soft">
                    {Math.round(astProg * f.ast)} AST
                  </span>
                )}
              </li>
            );
          })}
          <li className="pl-3 text-[10px] italic text-ink-soft">
            {currentScanning >= 0
              ? `Scanning ${repoFiles[currentScanning].name}…`
              : filesIndexed === repoFiles.length && astProg < 1
                ? "Extracting AST…"
                : graphProg > 0 && graphProg < 1
                  ? "Building Semantic Intent Graph…"
                  : done
                    ? "✓ Indexed"
                    : ""}
          </li>
        </ul>

        <svg viewBox="0 0 180 120" className="animate-graph-in h-[130px] w-full">
          {nodes.slice(0, nodesShown).map((n, i) => (
            <line
              key={`e-${i}`}
              x1={90}
              y1={60}
              x2={n.x}
              y2={n.y}
              stroke="currentColor"
              className={`text-ink-soft transition-opacity duration-500 ${done ? "opacity-50" : "opacity-30"}`}
              strokeWidth={0.8}
            />
          ))}
          {nodes.slice(0, nodesShown).map((n, i) => {
            const next = nodes[(i + 3) % nodesShown];
            if (!next || i + 3 >= nodesShown) return null;
            return (
              <line
                key={`x-${i}`}
                x1={n.x}
                y1={n.y}
                x2={next.x}
                y2={next.y}
                stroke="currentColor"
                className="text-ink-soft/20"
                strokeWidth={0.5}
              />
            );
          })}
          {nodes.slice(0, nodesShown).map((n, i) => (
            <circle
              key={`n-${i}`}
              cx={n.x}
              cy={n.y}
              r={2.8}
              className={`fill-ink-soft ${done ? "" : "animate-line-in"}`}
              style={{ animationDelay: `${i * 60}ms` }}
            />
          ))}
          <circle
            cx={90}
            cy={60}
            r={6}
            className={done ? "fill-status-completed" : "fill-primary animate-soft-pulse"}
          />
        </svg>
      </div>

      <div className="mt-3 grid grid-cols-4 gap-1.5">
        {[
          { label: "Files", value: fileCount },
          { label: "Imports", value: imports },
          { label: "Dependencies", value: deps },
          { label: "Semantic Roles", value: roles },
        ].map((m) => (
          <div
            key={m.label}
            className="rounded-md border border-border bg-surface-muted/60 px-2 py-1.5"
          >
            <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
              {m.label}
            </div>
            <div className="font-mono text-xs font-semibold text-ink">{m.value}</div>
          </div>
        ))}
      </div>
    </Frame>
  );
}

/* ============================================================
 * A2 — Dependency Analyzer
 * ============================================================ */
function DepsViz({ data, progress, done }: { data: DepsPayload; progress: number; done: boolean }) {
  const path = data.path;
  const nodeW = 150;
  const nodeH = 32;
  const gapY = 22;
  const col = 110;
  const top = 8;
  const stepY = nodeH + gapY;
  const ys = path.map((_, i) => top + i * stepY);
  const totalH = top + path.length * stepY;

  const pulseSeg = progress * (path.length - 1);
  const segIdx = Math.min(path.length - 2, Math.floor(pulseSeg));
  const segT = pulseSeg - segIdx;
  const pulseY =
    ys[segIdx] + nodeH / 2 + (ys[segIdx + 1] + nodeH / 2 - (ys[segIdx] + nodeH / 2)) * segT;
  const reachedCount = Math.min(path.length, Math.ceil(progress * path.length));

  const dead = data.unreachable.map((d, i) => ({
    name: d.name,
    y: ys[Math.min(i, ys.length - 1)] + nodeH / 2 + (i === 0 ? 0 : 8),
  }));

  const reachable = useCountUp(data.metrics.reachable, done);
  const deadFindings = useCountUp(data.metrics.deadFindings, done);
  const attack = useCountUp(data.metrics.attackPaths, done);

  return (
    <Frame label="Reachability — Live Trace">
      <svg
        viewBox={`0 0 220 ${totalH + 8}`}
        className="animate-graph-in w-full"
        style={{ height: totalH + 8 }}
      >
        <defs>
          <radialGradient id="dep-pulse">
            <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity="0.9" />
            <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity="0" />
          </radialGradient>
        </defs>

        {path.slice(0, -1).map((_, i) => {
          const reached = i < reachedCount - 1;
          return (
            <line
              key={`l-${i}`}
              x1={col}
              y1={ys[i] + nodeH}
              x2={col}
              y2={ys[i + 1]}
              stroke="currentColor"
              strokeWidth={reached ? 1.4 : 0.9}
              className={`transition-all duration-500 ${reached ? "text-ink-soft" : "text-border"}`}
            />
          );
        })}

        {path.map((p, i) => {
          const reached = i < reachedCount;
          const isHead = i === reachedCount - 1 && !done;
          return (
            <g key={p.name} className="transition-all duration-500">
              <rect
                x={col - nodeW / 2}
                y={ys[i]}
                width={nodeW}
                height={nodeH}
                rx={6}
                className={`transition-all duration-500 ${
                  reached
                    ? "fill-surface-muted stroke-border"
                    : "fill-surface-muted/40 stroke-border"
                } ${isHead ? "animate-soft-pulse" : ""}`}
                strokeWidth={1}
              />
              <text
                x={col}
                y={ys[i] + 13}
                textAnchor="middle"
                className={`fill-current font-mono text-[10px] font-semibold ${
                  reached ? "text-ink" : "text-ink-soft opacity-60"
                }`}
              >
                {p.name}
              </text>
              <text
                x={col}
                y={ys[i] + 24}
                textAnchor="middle"
                className={`fill-current text-[8px] ${
                  reached ? "text-ink-soft" : "text-ink-soft opacity-50"
                }`}
              >
                {p.sub}
              </text>
            </g>
          );
        })}

        {!done && reachedCount > 0 && (
          <>
            <circle cx={col} cy={pulseY} r={10} fill="url(#dep-pulse)" />
            <circle cx={col} cy={pulseY} r={3.5} className="fill-primary animate-soft-pulse" />
          </>
        )}

        {dead.map((d) => (
          <g key={d.name} className="opacity-40 transition-opacity duration-500">
            <line
              x1={col + nodeW / 2}
              y1={d.y}
              x2={col + nodeW / 2 + 14}
              y2={d.y}
              stroke="currentColor"
              strokeDasharray="2 2"
              strokeWidth={0.6}
              className="text-border"
            />
            <circle cx={col + nodeW / 2 + 18} cy={d.y} r={2.2} className="fill-ink-soft/40" />
            <text
              x={col + nodeW / 2 + 24}
              y={d.y + 3}
              className="fill-current font-mono text-[8px] text-ink-soft"
            >
              {d.name}
            </text>
          </g>
        ))}
      </svg>

      <div className="mt-2 grid grid-cols-3 gap-1.5">
        {[
          { label: "Reachable Findings", value: reachable, tone: "text-status-running" },
          { label: "Dead Findings", value: deadFindings, tone: "text-ink-soft" },
          { label: "Attack Paths", value: attack, tone: "text-status-failed" },
        ].map((m) => (
          <div
            key={m.label}
            className="rounded-md border border-border bg-surface-muted/60 px-2 py-1.5"
          >
            <div className="text-[9px] font-medium uppercase tracking-wider text-ink-soft">
              {m.label}
            </div>
            <div className={`font-mono text-sm font-semibold ${m.tone}`}>{m.value}</div>
          </div>
        ))}
      </div>
    </Frame>
  );
}

/* ============================================================
 * A3 — Static Analysis
 * ============================================================ */
function StaticViz({
  data,
  progress,
  done,
}: {
  data: StaticPayload;
  progress: number;
  done: boolean;
}) {
  const findings = data.findings.filter((f) => progress >= f.at);
  const raw = useCountUp(data.metrics.raw, done);
  const dedup = useCountUp(data.metrics.deduped, done);
  const prio = useCountUp(data.metrics.prioritized, done);

  return (
    <Frame label="Scanners">
      <div className="grid gap-3 md:grid-cols-[200px_1fr]">
        <div className="space-y-2">
          {data.scanners.map((s, i) => {
            const p = Math.max(0, Math.min(1, progress * 1.3 - i * 0.12));
            return (
              <div key={s}>
                <div className="flex items-center justify-between text-[11px] text-ink">
                  <span className="flex items-center gap-1.5">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        p >= 1 ? "bg-status-completed" : "bg-status-running animate-soft-pulse"
                      }`}
                    />
                    {s}
                  </span>
                  <span className="font-mono text-ink-soft">{Math.round(p * 100)}%</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-muted">
                  <div
                    className="h-full bg-primary transition-all duration-500"
                    style={{ width: `${p * 100}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        <div className="rounded-md border border-border bg-surface-muted/40 p-2">
          <div className="mb-1.5 flex items-center justify-between text-[10px] uppercase tracking-wider text-ink-soft">
            <span>Findings feed</span>
            <span className="font-mono">{findings.length}</span>
          </div>
          <ul className="space-y-1">
            {findings.map((f) => {
              const tone =
                f.sev === "HIGH"
                  ? "bg-status-failed-bg text-status-failed"
                  : f.sev === "MEDIUM"
                    ? "bg-status-retry-bg text-status-retry"
                    : "bg-status-running-bg text-status-running";
              return (
                <li key={f.text} className="animate-line-in flex items-center gap-2 text-[11px]">
                  <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${tone}`}>
                    {f.sev}
                  </span>
                  <span className="truncate text-ink">{f.text}</span>
                </li>
              );
            })}
            {findings.length === 0 && (
              <li className="text-[11px] italic text-ink-soft">Awaiting first signal…</li>
            )}
          </ul>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-surface-muted/60 px-3 py-2 font-mono text-[11px]">
        <span className="flex items-center gap-1.5">
          <AlertTriangle className="h-3 w-3 text-ink-soft" />
          <span className="text-ink">{raw}</span>
          <span className="text-ink-soft">Findings</span>
        </span>
        <span className="text-ink-soft">→ Dedup →</span>
        <span className="flex items-center gap-1.5">
          <span className="text-ink">{dedup}</span>
          <span className="text-ink-soft">Findings</span>
        </span>
        <span className="text-ink-soft">→ Prioritize →</span>
        <span className="rounded bg-status-completed-bg px-1.5 py-0.5 text-status-completed">
          {prio} actionable
        </span>
      </div>
    </Frame>
  );
}

/* ============================================================
 * A3.5 — Runtime Reproduction
 * ============================================================ */
function ReproduceViz({
  data,
  progress,
  done,
}: {
  data: ReproducePayload;
  progress: number;
  done: boolean;
}) {
  const tests = data.tests;
  const shown = Math.ceil(progress * tests.length);
  const bar = Math.min(100, Math.round(progress * 100));
  const failVisible = shown >= tests.length || done;

  return (
    <Frame label="pytest">
      <div className="rounded-md border border-border bg-[#0b0b0d] p-2.5 font-mono text-[11px] text-neutral-200">
        <div className="flex items-center gap-1.5 text-neutral-400">
          <Terminal className="h-3 w-3" />
          <span>{data.command}</span>
        </div>
        <div className="mt-1 text-neutral-500">Running…</div>
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-neutral-800">
          <div
            className="h-full bg-status-running transition-all duration-[250ms]"
            style={{ width: `${bar}%` }}
          />
        </div>
        <ul className="mt-2 space-y-0.5">
          {tests.slice(0, shown).map((t) => (
            <li key={t.name} className="animate-line-in flex items-center gap-2">
              <span
                className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${
                  t.result === "PASS"
                    ? "bg-emerald-500/15 text-emerald-400"
                    : "bg-rose-500/15 text-rose-400"
                }`}
              >
                {t.result}
              </span>
              <span className="text-neutral-200">{t.name}</span>
            </li>
          ))}
          {!failVisible && shown < tests.length && (
            <li className="text-neutral-500">collecting…</li>
          )}
        </ul>
      </div>

      {failVisible && (
        <div className="animate-line-in mt-2 rounded-md border border-status-failed/30 bg-status-failed-bg p-2.5 font-mono text-[11px]">
          <div className="flex items-center gap-1.5 text-status-failed">
            <Bug className="h-3 w-3" />
            {data.failure.name}
          </div>
          <div className="mt-1 text-status-failed/90">{data.failure.assertion}</div>
          <div className="mt-0.5 grid grid-cols-2 gap-2 text-[10px] text-ink-soft">
            <div>
              <span>Expected </span>
              <span className="rounded bg-status-completed-bg px-1 text-status-completed">
                {data.failure.expected}
              </span>
            </div>
            <div>
              <span>Actual </span>
              <span className="rounded bg-status-failed-bg px-1 text-status-failed">
                {data.failure.actual}
              </span>
            </div>
          </div>
          <div className="mt-2 border-t border-status-failed/20 pt-1.5">
            <div className="text-[10px] uppercase tracking-wider text-ink-soft">Stack</div>
            <ol className="mt-1 space-y-0.5 text-[10px] text-ink">
              {data.failure.stack.map((f, i) => (
                <li
                  key={f}
                  className="animate-line-in flex items-center gap-1.5"
                  style={{ animationDelay: `${i * 100}ms` }}
                >
                  <span className="text-ink-soft">
                    {i === data.failure.stack.length - 1 ? "└" : "├"}
                  </span>
                  <span>{f}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      {done && (
        <div className="animate-line-in mt-2 flex items-center gap-1.5 rounded-md border border-status-completed/30 bg-status-completed-bg px-2 py-1.5 text-[11px] font-semibold text-status-completed">
          <Check className="h-3 w-3" strokeWidth={3} />
          {data.successMessage}
        </div>
      )}
    </Frame>
  );
}

/* ============================================================
 * A4 — Root Cause Investigation
 * ============================================================ */
function RootCauseViz({
  data,
  progress,
  done,
}: {
  data: RootCausePayload;
  progress: number;
  done: boolean;
}) {
  const lines = data.lines;
  const inspectEnd = 0.6;
  const inspectProg = Math.min(1, progress / inspectEnd);
  const activeLine = Math.min(lines.length - 1, Math.floor(inspectProg * lines.length));
  const evidenceProg = Math.max(0, (progress - inspectEnd) / (1 - inspectEnd));
  const evidenceShown = Math.ceil(evidenceProg * data.evidence.length);
  const bugFound = activeLine >= lines.length - 1 && inspectProg >= 0.95;

  return (
    <Frame label="Root Cause — Inspection">
      <div className="rounded-md border border-border bg-surface-muted/60 p-2.5 font-mono text-[11px] leading-relaxed">
        {lines.map((l, i) => {
          const isActive = i === activeLine;
          const visited = i < activeLine;
          const isBug = i === lines.length - 1 && bugFound;
          return (
            <div
              key={i}
              className={`flex items-center gap-2 rounded px-1 transition-all duration-[250ms] ${
                isActive
                  ? "bg-status-running-bg text-ink"
                  : isBug
                    ? "bg-status-failed-bg text-status-failed"
                    : visited
                      ? "text-ink opacity-80"
                      : "text-ink-soft opacity-40 blur-[1.5px]"
              }`}
            >
              <span className="w-3 text-[9px] text-ink-soft">{i + 1}</span>
              <span className="flex-1">{l.code}</span>
              {isActive && (
                <span className="animate-line-in inline-flex items-center gap-1 rounded bg-primary/15 px-1.5 py-0.5 text-[9px] text-primary">
                  ▸ {l.probe}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {bugFound && (
        <div className="animate-line-in mt-2 flex items-center gap-1.5 rounded-md border border-status-failed/30 bg-status-failed-bg px-2 py-1 text-[11px] font-semibold text-status-failed">
          <Bug className="h-3 w-3" /> {data.bugMessage}
        </div>
      )}

      <div className="mt-2 grid gap-1.5 sm:grid-cols-3">
        {data.evidence.map((e, i) => {
          const visible = i < evidenceShown || done;
          return (
            <div
              key={e.n}
              className={`rounded-md border border-border bg-surface px-2 py-1.5 transition-all duration-[250ms] ${
                visible ? "animate-line-in opacity-100" : "opacity-0"
              }`}
            >
              <div className="flex items-center justify-between text-[9px] uppercase tracking-wider text-ink-soft">
                <span>Evidence #{e.n}</span>
                <span className="font-mono text-status-completed">{e.conf}%</span>
              </div>
              <div className="text-[11px] font-medium text-ink">{e.title}</div>
              <div className="font-mono text-[9px] text-ink-soft">{e.detail}</div>
            </div>
          );
        })}
      </div>
    </Frame>
  );
}

/* ============================================================
 * A5 — Blast Radius (ripple waves)
 * ============================================================ */
function BlastViz({
  data,
  progress,
  done,
}: {
  data: BlastPayload;
  progress: number;
  done: boolean;
}) {
  const W = 320;
  const H = 220;
  const cx = W / 2;
  const cy = H / 2;
  const R = 78;

  const modules = data.modules;
  const placed = modules.map((m, i) => {
    const a = -Math.PI / 2 + (i / modules.length) * Math.PI * 2;
    const x = cx + Math.cos(a) * R;
    const y = cy + Math.sin(a) * R;
    const cos = Math.cos(a);
    const sin = Math.sin(a);
    const anchor: "start" | "middle" | "end" =
      cos > 0.35 ? "start" : cos < -0.35 ? "end" : "middle";
    const tx = x + cos * 10;
    const ty = y + sin * 10 + (sin > 0.5 ? 9 : sin < -0.5 ? -2 : 3.5);
    return { ...m, x, y, tx, ty, anchor };
  });

  const affected = placed.filter((m) => progress >= m.hitAt).length;
  const affectedCount = useCountUp(modules.length, done || affected === modules.length);

  return (
    <Frame label="Impact Propagation">
      <svg viewBox={`0 0 ${W} ${H}`} className="animate-graph-in w-full" style={{ height: H }}>
        <style>{`@keyframes blast-ripple { 0%{r:10;opacity:0.85} 100%{r:${R + 12};opacity:0} }`}</style>

        {!done &&
          [0, 1, 2].map((i) => (
            <circle
              key={i}
              cx={cx}
              cy={cy}
              r={10}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.1}
              className="text-status-failed"
              style={{
                animation: `blast-ripple 2.4s ${i * 0.8}s ease-out infinite`,
              }}
            />
          ))}

        {placed.map((m) => {
          const reached = progress >= m.hitAt;
          return (
            <line
              key={`l-${m.name}`}
              x1={cx}
              y1={cy}
              x2={m.x}
              y2={m.y}
              stroke="currentColor"
              strokeWidth={reached ? 1 : 0.5}
              strokeDasharray={reached ? "0" : "2 2"}
              className={`transition-all duration-500 ${
                reached ? "text-ink-soft/60" : "text-border"
              }`}
            />
          );
        })}

        {placed.map((m) => {
          const reached = progress >= m.hitAt;
          return (
            <g key={m.name} className="transition-all duration-500">
              <circle
                cx={m.x}
                cy={m.y}
                r={reached ? 5 : 3}
                className={`transition-all duration-500 ${
                  reached
                    ? "fill-surface-muted stroke-ink-soft/40"
                    : "fill-surface-muted stroke-border"
                } ${reached && !done ? "animate-soft-pulse" : ""}`}
                strokeWidth={0.8}
              />
              <text
                x={m.tx}
                y={m.ty}
                textAnchor={m.anchor}
                className={`fill-current font-mono text-[9px] ${
                  reached ? "text-ink" : "text-ink-soft opacity-60"
                }`}
              >
                {m.name}
              </text>
            </g>
          );
        })}

        <circle cx={cx} cy={cy} r={8} className="fill-status-failed animate-soft-pulse" />
        <text
          x={cx}
          y={cy + 22}
          textAnchor="middle"
          className="fill-current font-mono text-[10px] font-semibold text-status-failed"
        >
          {data.source}
        </text>
      </svg>

      <div className="mt-2 flex items-center justify-between rounded-md border border-border bg-surface-muted/60 px-3 py-1.5 font-mono text-[11px]">
        <span className="text-ink-soft">Affected modules</span>
        <span className="font-semibold text-status-failed">
          {done ? affectedCount : affected} / {modules.length}
        </span>
      </div>
    </Frame>
  );
}

/* ============================================================
 * A6 — Repair Planner (DAG draws itself)
 * ============================================================ */
function PlannerViz({
  data,
  progress,
  done,
}: {
  data: PlannerPayload;
  progress: number;
  done: boolean;
}) {
  const nodesShown = Math.ceil(progress * data.nodes.length);
  const edgesShown = Math.ceil(progress * data.edges.length);
  const rectW = 84;
  const rectH = 22;

  return (
    <Frame label="Repair DAG">
      <svg viewBox="0 0 260 210" className="animate-graph-in w-full" style={{ height: 220 }}>
        <style>{`@keyframes dag-draw { to { stroke-dashoffset: 0; } }`}</style>
        {data.edges.slice(0, edgesShown).map(([a, b], i) => {
          const A = data.nodes[a];
          const B = data.nodes[b];
          return (
            <line
              key={i}
              x1={A.x}
              y1={A.y + rectH / 2}
              x2={B.x}
              y2={B.y - rectH / 2}
              stroke="currentColor"
              strokeWidth={1}
              className="text-ink-soft/60"
              style={{
                strokeDasharray: 120,
                strokeDashoffset: 120,
                animation: "dag-draw 0.6s ease-out forwards",
              }}
            />
          );
        })}
        {data.nodes.slice(0, nodesShown).map((n, i) => (
          <g
            key={n.id}
            className="animate-line-in transition-all duration-500"
            style={{ animationDelay: `${i * 90}ms` }}
          >
            <rect
              x={n.x - rectW / 2}
              y={n.y - rectH / 2}
              width={rectW}
              height={rectH}
              rx={5}
              className={`${
                done
                  ? "fill-status-completed-bg stroke-status-completed/40"
                  : "fill-surface stroke-border"
              }`}
              strokeWidth={1}
            />
            <text
              x={n.x}
              y={n.y + 3.5}
              textAnchor="middle"
              className="fill-current font-mono text-[9px] font-medium text-ink"
            >
              {n.label}
            </text>
          </g>
        ))}
      </svg>
    </Frame>
  );
}

/* ============================================================
 * A7 — Patch Generator
 * ============================================================
 * The real diff and its full per-file board live in `PatchPanel`
 * (`Workspace.tsx`, backed by `GET /runs/{id}/patch`) — this compact card
 * shows only generation provenance A7 itself recorded, with no animation:
 * the patch is a persisted artifact by the time this renders, not something
 * being typed live. Nothing here is invented — a `null` field is omitted
 * rather than guessed.
 */
function PatchViz({ data }: { data: PatchPayload }) {
  if (data.files.length === 0) return null;

  return (
    <Frame label="Patch Generator">
      <ul className="space-y-1.5">
        {data.files.map((f) => (
          <li
            key={f.file}
            className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-surface-muted/40 px-2 py-1.5 font-mono text-[10px]"
          >
            <span className="text-ink">{f.file}</span>
            {f.isTarget && (
              <span className="rounded bg-primary/15 px-1 text-[9px] font-semibold uppercase tracking-wider text-primary">
                target
              </span>
            )}
            {f.targetFunction && <span className="text-ink-soft">{f.targetFunction}()</span>}
            <span className="ml-auto flex items-center gap-1.5 text-ink-soft">
              {f.generationSource === "stub" ? (
                <span className="text-status-retry">stub mode</span>
              ) : f.generationSource === "llm" ? (
                <span>LLM</span>
              ) : null}
              {f.method === "ast_validated_write" && (
                <span className="text-status-completed">✓ AST validated</span>
              )}
              {!!f.retryNumber && <span>retry {f.retryNumber}</span>}
            </span>
          </li>
        ))}
      </ul>
    </Frame>
  );
}

/* ============================================================
 * A8 — Mutation Validation
 * ============================================================ */
function MutationViz({
  data,
  failed,
  done,
}: {
  data: MutationPayload;
  progress: number;
  failed: boolean;
  done: boolean;
}) {
  // A8 reports an aggregate, so this renders the aggregate. The previous
  // version listed eight mutants and derived a percentage from them; none of
  // those mutants existed, and the percentage measured nothing.
  const scored = data.score !== null;
  const scorePct = scored ? Math.round(data.score! * 100) : 0;
  const animatedScore = useCountUp(scorePct, scored, 400);

  // `survived` is `null` when A8 never scored mutation — distinct from
  // `false`, which means it scored and nothing survived. Rendering "none
  // survived" for an unmeasured run claims a check that never happened.
  const mutationMeasured = data.survived !== null;

  const checks: { label: string; state: "measured" | "unmeasured" | "failed"; detail: string }[] = [
    {
      label: "Test suite",
      state: data.pytestPassed ? "measured" : "failed",
      detail: data.pytestPassed ? "passed" : "did not pass",
    },
    {
      label: "Surviving mutants",
      state: !mutationMeasured ? "unmeasured" : data.survived ? "failed" : "measured",
      detail: !mutationMeasured
        ? "not measured"
        : data.survived
          ? `${data.survivedMutants ?? "one or more"} survived`
          : "none survived",
    },
  ];

  return (
    <Frame label="Mutation Testing">
      <ul className="space-y-1 font-mono text-[11px]">
        {checks.map((c) => (
          <li
            key={c.label}
            className={`animate-line-in flex items-center justify-between rounded px-2 py-1 ${
              c.state === "failed"
                ? "bg-status-failed-bg/60 ring-1 ring-status-failed/30"
                : "opacity-70"
            }`}
          >
            <span className="flex items-center gap-2 truncate">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  c.state === "measured"
                    ? "bg-status-completed"
                    : c.state === "failed"
                      ? "bg-status-failed"
                      : "bg-ink-soft"
                }`}
              />
              <span className="text-ink">{c.label}</span>
            </span>
            <span
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                c.state === "measured"
                  ? "bg-status-completed-bg text-status-completed"
                  : c.state === "failed"
                    ? "bg-status-failed-bg text-status-failed"
                    : "bg-surface-muted text-ink-soft"
              }`}
            >
              {c.state === "measured" ? (
                <Check className="h-2.5 w-2.5" strokeWidth={3} />
              ) : c.state === "failed" ? (
                <Skull className="h-2.5 w-2.5" />
              ) : null}
              {c.detail}
            </span>
          </li>
        ))}
      </ul>

      <div className="mt-3">
        <div className="flex items-center justify-between text-[10px] text-ink-soft">
          <span>Mutation Score</span>
          {/* "not scored" is a distinct outcome from a score of zero. */}
          <span
            className={`font-mono ${scored ? (failed ? "text-status-failed" : "text-status-completed") : "text-ink-soft"}`}
          >
            {scored ? `${animatedScore}%` : "not scored"}
          </span>
        </div>
        {scored && (
          <div className="relative mt-1 h-2 overflow-hidden rounded-full bg-surface-muted">
            <div
              className={`h-full transition-all duration-500 ${
                failed ? "bg-status-failed" : "bg-status-completed"
              }`}
              style={{ width: `${animatedScore}%` }}
            />
          </div>
        )}
      </div>

      {data.correctness !== null && (
        <div className="mt-2 flex items-center justify-between text-[10px] text-ink-soft">
          <span>Correctness (A10 gate)</span>
          <span
            className={`font-mono ${
              data.correctness >= data.correctnessThreshold
                ? "text-status-completed"
                : "text-status-failed"
            }`}
          >
            {data.correctness.toFixed(0)} / {data.correctnessThreshold.toFixed(0)}
          </span>
        </div>
      )}

      {done && failed && (
        <div className="animate-line-in mt-2 rounded-md border border-status-failed/30 bg-status-failed-bg px-2 py-1.5 text-[11px] text-status-failed">
          <span className="font-semibold">Validation failed</span>
          <span className="text-status-failed/80"> — {data.failureMessage}</span>
        </div>
      )}
    </Frame>
  );
}

/* ============================================================
 * A10 — Mergeability Router
 * ============================================================ */
function MergeViz({
  data,
  progress,
  done,
}: {
  data: MergePayload;
  progress: number;
  done: boolean;
}) {
  const metrics = data.metrics;
  // Independent count-ups so each metric resolves to its own number.
  // We still rely on a fixed-arity tuple for typing but only use as many
  // as the payload supplies.
  const v0 = useCountUp(metrics[0]?.value ?? 0, progress > 0.05);
  const v1 = useCountUp(metrics[1]?.value ?? 0, progress > 0.18);
  const v2 = useCountUp(metrics[2]?.value ?? 0, progress > 0.36);
  const v3 = useCountUp(metrics[3]?.value ?? 0, progress > 0.54);
  const animated = [v0, v1, v2, v3];

  // The gauge renders the backend's own composite (`_trust_score`, the same
  // number shown as "Trust Score" elsewhere on the page) rather than
  // recomputing one from `metrics` here — a second, disagreeing trust
  // formula in the frontend is exactly the contradiction this product exists
  // to avoid.
  const weightedMeasured = data.compositeScore !== null;
  const composite = data.compositeScore ?? 0;
  const compositeAnim = useCountUp(composite, weightedMeasured && progress > 0.72, 600);

  const allDone = progress >= 0.85;

  const stage = allDone ? (progress < 0.92 ? 1 : progress < 0.97 ? 2 : 3) : 0;

  const size = 96;
  const stroke = 8;
  const r = (size - stroke) / 2;
  const C = 2 * Math.PI * r;
  const gaugeOffset = weightedMeasured ? C - (compositeAnim / 100) * C : C;
  const gaugeColor = !weightedMeasured
    ? "stroke-ink-soft"
    : compositeAnim >= 85
      ? "stroke-status-completed"
      : compositeAnim >= 70
        ? "stroke-status-retry"
        : "stroke-status-failed";

  return (
    <Frame label="Trust Evaluation">
      <div className="grid items-center gap-4 sm:grid-cols-[1fr_auto]">
        <div className="space-y-2">
          {metrics.map((m, i) => {
            const v = animated[i] ?? 0;
            const reveal = progress > 0.05 + i * 0.16;
            const tone = !m.measured
              ? "text-ink-soft"
              : m.ok
                ? "text-status-completed"
                : "text-status-retry";
            const display = !m.measured
              ? "Not measured"
              : m.scopeLabel && m.value !== null && v >= m.value - 1
                ? m.scopeLabel
                : `${v}%`;
            return (
              <div
                key={m.label}
                className={`flex items-center justify-between gap-3 rounded-md border border-border bg-surface-muted/40 px-2.5 py-1.5 transition-opacity duration-500 ${
                  reveal ? "opacity-100" : "opacity-40"
                }`}
              >
                <span className="text-[11px] font-medium text-ink">{m.label}</span>
                <span className={`font-mono text-[13px] font-semibold ${tone}`}>{display}</span>
              </div>
            );
          })}
        </div>

        <div className="flex flex-col items-center justify-center">
          <div className="relative" style={{ width: size, height: size }}>
            <svg width={size} height={size} className="animate-graph-in -rotate-90">
              <circle
                cx={size / 2}
                cy={size / 2}
                r={r}
                stroke="currentColor"
                strokeWidth={stroke}
                fill="none"
                className="text-surface-muted"
              />
              <circle
                cx={size / 2}
                cy={size / 2}
                r={r}
                strokeWidth={stroke}
                fill="none"
                strokeLinecap="round"
                strokeDasharray={C}
                strokeDashoffset={gaugeOffset}
                className={`${gaugeColor} transition-all duration-500 ease-out`}
              />
            </svg>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span
                className={`font-mono font-semibold text-ink ${weightedMeasured ? "text-[18px]" : "text-[11px]"}`}
              >
                {weightedMeasured ? compositeAnim : "—"}
              </span>
              <span className="text-[8px] uppercase tracking-wider text-ink-soft">
                {weightedMeasured ? "Trust" : "Not measured"}
              </span>
            </div>
          </div>
          <span className="mt-1 text-[9px] uppercase tracking-wider text-ink-soft">Composite</span>
        </div>
      </div>

      {allDone && (
        <div className="mt-3 flex items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-[11px]">
          <span
            className={`flex items-center gap-1.5 transition-opacity duration-500 ${
              stage >= 1 ? "opacity-100" : "opacity-40"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full bg-primary ${
                stage === 1 ? "animate-soft-pulse" : ""
              }`}
            />
            <span className="text-ink-soft">Evaluating Trust</span>
          </span>
          <span className="text-ink-soft">↓</span>
          <span
            className={`transition-opacity duration-500 ${
              stage >= 2 ? "opacity-100" : "opacity-40"
            }`}
          >
            <span className="text-ink">Decision Generated</span>
          </span>
          <span className="text-ink-soft">↓</span>
          <span
            className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 font-semibold text-primary transition-all duration-500 ${
              stage >= 3 ? "opacity-100" : "opacity-40"
            }`}
            style={{
              background: "hsl(var(--primary) / 0.10)",
              boxShadow:
                stage >= 3
                  ? "0 0 18px hsl(var(--primary) / 0.35), inset 0 0 0 1px hsl(var(--primary) / 0.35)"
                  : "inset 0 0 0 1px hsl(var(--primary) / 0.20)",
            }}
          >
            <GitMerge className="h-3.5 w-3.5" />
            {data.decisionLabel}
          </span>
        </div>
      )}

      {done && <div className="mt-2 text-[10px] text-ink-soft">{data.reviewNote}</div>}
    </Frame>
  );
}

/* Evidence handoff — the previous agent's output becoming the next agent's input */
export function EvidenceHandoff({
  label,
  active,
  toLabel,
  live,
}: {
  label: string;
  active: boolean;
  toLabel?: string;
  live?: boolean;
}) {
  if (!active) return null;
  return (
    <div
      className="relative ml-[11px] flex flex-col items-center gap-1 py-1.5 motion-reduce:[&_*]:!animate-none"
      aria-hidden
    >
      {/* Vertical pipe with a flowing pulse when the next agent is live */}
      <div className="relative h-6 w-px overflow-hidden bg-border animate-handoff-line-grow">
        {live && (
          <span className="absolute left-1/2 top-0 h-2 w-px -translate-x-1/2 bg-primary/70 animate-handoff-flow" />
        )}
      </div>
      {/* From → To chip: previous output visually becoming next input */}
      <div className="animate-line-in flex items-center gap-1.5 rounded-full border border-border/70 bg-surface-muted/60 px-2 py-[2px] font-mono text-[9px] uppercase tracking-wider text-ink-soft/80">
        <ShieldCheck className="h-2.5 w-2.5 text-status-completed/70" />
        <span>{label}</span>
        {toLabel && (
          <>
            <span className="text-ink-soft/40">→</span>
            <span className="text-ink-soft/70">{toLabel}</span>
          </>
        )}
      </div>
      <div className="h-6 w-px bg-border animate-handoff-line-grow" />
    </div>
  );
}
