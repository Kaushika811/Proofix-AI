/**
 * A5 — Blast Radius Impact Ledger.
 *
 * Everything on this panel comes from `GET /api/runs/{runId}/blast`
 * (`services/ui_projection.py::build_blast_impact`, sourced from A5's own
 * `BlastGraphResult`, joined read-only against A1's SIG and A3's findings).
 * No hop count, direction, score or capability is computed client-side beyond
 * deterministic grouping (by hop, by directory) of what the backend sent.
 *
 * Distinct from A2's Dependency Risk panel: A2 answers whether a third-party
 * advisory *reaches into* this repository. This panel answers what could be
 * affected if the file A4 is investigating *changes* — the opposite
 * direction, over the repository's own files. It reads as the continuation of
 * the A4 board: A4 ends at "we believe this is real and it lives here"; A5
 * opens at "here is what changing there could touch".
 *
 * Vocabulary this panel is careful never to blur, because the backend only
 * ever knows reachability, never certainty:
 *   "changing"            the resolved origin
 *   "could be affected"   hop 1 — a direct import edge exists
 *   "reachable"           hop 2-3 — transitive, confidence decays with distance
 *   "priority"            not "risk" — `priorityScore` cannot distinguish "no
 *                         churn measured" from "measured zero churn"; the
 *                         caveat the backend sends is always shown, not
 *                         summarised away
 * Nothing here is ever labelled "affected" or "definitely affected".
 */
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowRightLeft,
  ChevronDown,
  ChevronRight,
  Pin,
} from "lucide-react";
import type { BlastImpact, BlastScopeFile } from "./blastTypes";
import { ImpactGraph } from "./ImpactGraph";
import { getBlastImpact } from "@/lib/runService";
import type { AgentStatus } from "./data";

const ROLE_LABEL: Record<string, string> = {
  "auth-boundary": "Auth Boundary",
  "data-access": "Data Access",
  "public-api": "Public API",
  "config-surface": "Config Surface",
  "test-only": "Test Only",
  "internal-util": "Internal Utility",
};

const SOURCE_LABEL: Record<string, string> = {
  stack_trace: "resolved from the reproduced stack trace",
  root_cause: "resolved from A4's root-cause citation",
  sig_lookup: "resolved by matching the semantic graph",
  import_mapping: "resolved from a failing test's import chain",
  fallback: "resolved by path fallback — lowest-confidence route",
};

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function directoryOf(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx === -1 ? "(root)" : path.slice(0, idx);
}

function fileName(path: string): string {
  const idx = path.lastIndexOf("/");
  return idx === -1 ? path : path.slice(idx + 1);
}

function originPathOf(impact: BlastImpact): string | null {
  return impact.origin?.resolvedPath ?? impact.origin?.normalizedPath ?? impact.origins[0] ?? null;
}

function rankByPriorityThenConfidence(a: BlastScopeFile, b: BlastScopeFile): number {
  const priority = (b.priorityScore ?? -1) - (a.priorityScore ?? -1);
  if (priority !== 0) return priority;
  const confidence = (b.propagationConfidence ?? -1) - (a.propagationConfidence ?? -1);
  if (confidence !== 0) return confidence;
  return (a.hopCount ?? 99) - (b.hopCount ?? 99);
}

function topFile(files: BlastScopeFile[]): BlastScopeFile | null {
  if (files.length === 0) return null;
  return [...files].sort(rankByPriorityThenConfidence)[0] ?? null;
}

function buildPathChain(
  file: BlastScopeFile,
  scopeByPath: Map<string, BlastScopeFile>,
  originPath: string | null,
): string[] {
  const chain: string[] = [];
  let cursor: BlastScopeFile | undefined = file;

  const visited = new Set<string>();
  while (cursor && !visited.has(cursor.path)) {
    visited.add(cursor.path);
    chain.unshift(cursor.path);
    if (!cursor.reachedVia) break;
    cursor = scopeByPath.get(cursor.reachedVia);
  }

  if (originPath && chain[0] !== originPath) chain.unshift(originPath);
  return chain;
}

function directionSummary(file: BlastScopeFile): string {
  const up = file.directions.includes("backward");
  const down = file.directions.includes("forward");
  if (up && down) return "reached from both upstream and downstream lanes";
  if (up) return "reached in the upstream lane";
  if (down) return "reached in the downstream lane";
  return "direction not measured";
}

function containmentSummary(file: BlastScopeFile): string {
  if (file.hopCount === null) return "containment not measured";
  if (file.hopCount >= 3) return "past the containment line";
  return "inside the containment line";
}

type FocusPreset = {
  id: string;
  label: string;
  detail: string;
  file: BlastScopeFile;
};

// ------------------------------------------------------------------ chrome

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load the blast-radius impact</span>
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

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
      {children}
    </div>
  );
}

function Flow() {
  return (
    <div className="flex justify-center" aria-hidden>
      <ArrowDown className="h-3.5 w-3.5 text-ink-soft/60" />
    </div>
  );
}

function OverviewTile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface-muted/20 p-3">
      <div className="text-[10px] uppercase tracking-[0.22em] text-ink-soft">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-ink">{value}</div>
      <p className="mt-1 text-[10px] text-ink-soft">{detail}</p>
    </div>
  );
}

function CorridorOverview({
  directCount,
  transitiveCount,
  beyondContainmentCount,
  flaggedCount,
  denseMode,
}: {
  directCount: number;
  transitiveCount: number;
  beyondContainmentCount: number;
  flaggedCount: number;
  denseMode: boolean;
}) {
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
      <OverviewTile
        label="Immediate Reach"
        value={String(directCount)}
        detail="Hop 1 files directly connected to the origin."
      />
      <OverviewTile
        label="Outer Spread"
        value={String(transitiveCount)}
        detail="Hop 2-3 files that extend consequence farther from the origin."
      />
      <OverviewTile
        label="Outer scope"
        value={String(beyondContainmentCount)}
        detail="Files recorded at three or more traversal hops."
      />
      <OverviewTile
        label="Correlated Warnings"
        value={String(flaggedCount)}
        detail={
          denseMode
            ? "A3 findings inside a dense corridor; use the picks below to inspect the sharpest branches."
            : "A3 findings inside the current corridor; these correlate with the propagation path."
        }
      />
    </div>
  );
}

function FocusPresets({
  options,
  selectedPath,
  onSelect,
}: {
  options: FocusPreset[];
  selectedPath: string | null;
  onSelect: (file: BlastScopeFile) => void;
}) {
  if (options.length === 0) return null;

  return (
    <div
      role="group"
      aria-label="Corridor focus picks"
      className="rounded-xl border border-border bg-surface-muted/20 p-3"
    >
      <SectionLabel>Guided picks</SectionLabel>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const active = selectedPath === option.file.path;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => onSelect(option.file)}
              className="rounded-full border px-3 py-1.5 text-left transition-colors hover:bg-surface"
              style={{
                borderColor: active ? "var(--color-status-running)" : "var(--color-border)",
                backgroundColor: active ? "var(--color-status-running-bg)" : "var(--color-surface)",
              }}
            >
              <span className="block text-[10px] font-semibold uppercase tracking-[0.18em] text-ink-soft">
                {option.label}
              </span>
              <span className="mt-0.5 block font-mono text-[11px] text-ink">
                {fileName(option.file.path)}
              </span>
              <span className="mt-0.5 block text-[10px] text-ink-soft">{option.detail}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ origin

function OriginCard({ impact }: { impact: BlastImpact }) {
  const origin = impact.origin;
  if (!origin) {
    return (
      <div
        role="group"
        aria-label="Change origin"
        className="rounded-xl border border-border bg-surface-muted/30 p-3 text-[11px] text-ink-soft"
      >
        No origin — A5 ran but resolved no file to traverse from (no semantic graph, or no citations
        to anchor to).
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label="Change origin"
      className="rounded-xl border border-border bg-surface-muted/30 p-3"
    >
      <div className="text-[10px] uppercase tracking-wider text-ink-soft">Changing</div>
      <p className="mt-0.5 font-mono text-sm font-semibold text-ink">
        {origin.resolvedPath ?? origin.normalizedPath}
      </p>
      <div className="mt-2 flex flex-wrap gap-3 text-[10px] text-ink-soft">
        <span>
          {origin.source
            ? (SOURCE_LABEL[origin.source] ?? origin.source)
            : "Resolution source not measured"}
        </span>
        {origin.confidence !== null && (
          <span>
            confidence <span className="font-mono text-ink">{pct(origin.confidence)}</span>
          </span>
        )}
        {origin.runtimeConfirmed === true && (
          <span className="text-status-completed">runtime-confirmed by A3.5</span>
        )}
        {origin.pinned === true && (
          <span className="flex items-center gap-1 text-status-retry">
            <Pin className="h-3 w-3" aria-hidden />
            pinned into auto-patch scope
          </span>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------ direct impact

function DirectionBadges({ file }: { file: BlastScopeFile }) {
  const up = file.directions.includes("backward");
  const down = file.directions.includes("forward");
  return (
    <span className="inline-flex items-center gap-1 text-[9px] uppercase tracking-wider text-ink-soft">
      {up && down ? (
        <ArrowRightLeft className="h-3 w-3" aria-label="reached both as caller and dependency" />
      ) : up ? (
        <ArrowLeft className="h-3 w-3" aria-label="calls the origin" />
      ) : down ? (
        <ArrowRight className="h-3 w-3" aria-label="called by the origin" />
      ) : null}
    </span>
  );
}

function ScopeFileRow({ file, onSelect }: { file: BlastScopeFile; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="flex w-full items-start gap-1.5 border-t border-border/60 py-1.5 text-left first:border-t-0"
    >
      <DirectionBadges file={file} />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-baseline gap-x-1.5">
          <span className="truncate font-mono text-[11px] text-ink">{file.path}</span>
          {file.role && (
            <span className="text-[9px] uppercase tracking-wider text-ink-soft">
              {ROLE_LABEL[file.role] ?? file.role}
            </span>
          )}
          {file.hasStaticFinding && (
            <span className="rounded bg-status-retry-bg px-1 text-[9px] font-medium text-status-retry">
              A3 flagged
            </span>
          )}
        </span>
        {file.edgeBasis === "name_contains" && (
          <span className="mt-0.5 block text-[9px] text-status-retry">
            name-matched only — could be a false edge
          </span>
        )}
      </span>
      <span className="shrink-0 font-mono text-[10px] text-ink-soft">
        {file.propagationConfidence !== null ? pct(file.propagationConfidence) : "—"}
      </span>
    </button>
  );
}

function DirectImpact({
  files,
  onSelect,
}: {
  files: BlastScopeFile[];
  onSelect: (file: BlastScopeFile) => void;
}) {
  const upstream = files.filter((f) => f.directions.includes("backward"));
  const downstream = files.filter(
    (f) => f.directions.includes("forward") && !f.directions.includes("backward"),
  );

  return (
    <div
      role="group"
      aria-label="Direct impact"
      className="rounded-lg border border-border bg-surface-muted/30 p-2.5"
    >
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink">
          Could be affected — direct (hop 1)
        </span>
        <span className="font-mono text-[10px] text-ink-soft">{files.length}</span>
      </div>
      {files.length === 0 ? (
        <p className="py-1.5 text-[10px] text-ink-soft">
          No direct impact — Not measured, or nothing reaches this file within one hop.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="mb-0.5 flex items-center gap-1 text-[9px] uppercase tracking-wider text-ink-soft">
              <ArrowLeft className="h-3 w-3" aria-hidden />
              upstream · calls the origin
            </div>
            {upstream.length ? (
              <ul>
                {upstream.map((f) => (
                  <li key={f.path}>
                    <ScopeFileRow file={f} onSelect={() => onSelect(f)} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="py-1 text-[10px] text-ink-soft">None found</p>
            )}
          </div>
          <div>
            <div className="mb-0.5 flex items-center gap-1 text-[9px] uppercase tracking-wider text-ink-soft">
              <ArrowRight className="h-3 w-3" aria-hidden />
              downstream · the origin calls it
            </div>
            {downstream.length ? (
              <ul>
                {downstream.map((f) => (
                  <li key={f.path}>
                    <ScopeFileRow file={f} onSelect={() => onSelect(f)} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="py-1 text-[10px] text-ink-soft">None found</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------- transitive

function TransitiveImpact({
  files,
  onSelect,
}: {
  files: BlastScopeFile[];
  onSelect: (file: BlastScopeFile) => void;
}) {
  const [open, setOpen] = useState(false);
  const byDirectory = useMemo(() => {
    const groups = new Map<string, BlastScopeFile[]>();
    for (const f of files) {
      const key = directoryOf(f.path);
      const list = groups.get(key) ?? [];
      list.push(f);
      groups.set(key, list);
    }
    return [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [files]);

  return (
    <div
      role="group"
      aria-label="Transitive impact"
      className="rounded-lg border border-border bg-surface-muted/30"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        disabled={files.length === 0}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left disabled:cursor-default"
      >
        {files.length > 0 &&
          (open ? (
            <ChevronDown className="h-3 w-3 text-ink-soft" />
          ) : (
            <ChevronRight className="h-3 w-3 text-ink-soft" />
          ))}
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink">
          Reachable — transitive (hop 2–3)
        </span>
        <span className="ml-auto font-mono text-[10px] text-ink-soft">{files.length}</span>
      </button>
      {files.length === 0 && (
        <p className="px-2.5 pb-1.5 text-[10px] text-ink-soft">
          Transitive impact — Not measured, or nothing reachable beyond hop 1.
        </p>
      )}
      {open && (
        <div className="space-y-2 px-2.5 pb-2">
          {byDirectory.map(([dir, group]) => (
            <div key={dir}>
              <div className="text-[9px] uppercase tracking-wider text-ink-soft">
                {dir} <span className="font-mono">({group.length})</span>
              </div>
              <ul>
                {group.map((f) => (
                  <li key={f.path}>
                    <ScopeFileRow file={f} onSelect={() => onSelect(f)} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------ capabilities

function CapabilityRollup({ capabilities }: { capabilities: BlastImpact["capabilities"] }) {
  if (capabilities === null) {
    return (
      <div className="text-[11px] text-ink-soft">
        Potentially affected areas — Not measured. A0.5&apos;s repository index did not run for this
        run.
      </div>
    );
  }
  if (capabilities.length === 0) {
    return (
      <div className="text-[11px] text-ink-soft">
        No capability signal matched the impact scope.
      </div>
    );
  }

  const max = Math.max(...capabilities.map((c) => c.filesInScope.length), 1);

  return (
    <div role="group" aria-label="Potentially affected areas" className="space-y-1.5">
      {capabilities.map((c) => (
        <div key={c.slug}>
          <div className="flex items-baseline justify-between text-[11px]">
            <span className="font-medium text-ink">{c.name}</span>
            <span className="font-mono text-[10px] text-ink-soft">
              {c.filesInScope.length}
              {c.totalFilesInCapability !== null
                ? ` of ${c.totalFilesInCapability} files`
                : " files"}
            </span>
          </div>
          <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-surface-muted">
            <div
              className="h-full rounded-full bg-status-running"
              style={{ width: `${Math.round((c.filesInScope.length / max) * 100)}%` }}
            />
          </div>
          <p className="mt-0.5 text-[9px] text-ink-soft">
            {c.confidence !== null ? `${pct(c.confidence)} confidence — ` : ""}
            {c.why}
          </p>
        </div>
      ))}
    </div>
  );
}

// --------------------------------------------------------- patch authority

function PatchAuthorityStrip({ impact }: { impact: BlastImpact }) {
  return (
    <div
      role="group"
      aria-label="Patch authority"
      className="rounded-lg border border-border bg-surface-muted/30 p-2.5"
    >
      <div className="flex flex-wrap gap-4 text-[11px]">
        <span>
          Auto-patchable{" "}
          <span className="font-mono font-semibold text-ink">{impact.autoPatchScope.length}</span>
        </span>
        <span>
          Human review{" "}
          <span className="font-mono font-semibold text-ink">
            {impact.humanReviewRequired.length}
          </span>
        </span>
      </div>
      {impact.patchAuthorityOverlap.length > 0 && (
        <p className="mt-1.5 flex items-start gap-1.5 text-[10px] text-ink-soft">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-status-retry" aria-hidden />
          {impact.patchAuthorityOverlap.join(", ")}{" "}
          {impact.patchAuthorityOverlap.length === 1 ? "is" : "are"} in both lists at once: the
          resolved target was pinned into auto-patch scope while its own propagation confidence
          independently placed it in human review. Both facts are real.
        </p>
      )}
    </div>
  );
}

function PropagationReadout({
  impact,
  focus,
}: {
  impact: BlastImpact;
  focus: BlastScopeFile | null;
}) {
  const scopeByPath = useMemo(() => {
    const index = new Map<string, BlastScopeFile>();
    for (const file of impact.scope) index.set(file.path, file);
    return index;
  }, [impact.scope]);

  const originPath = originPathOf(impact);
  if (!focus) {
    return (
      <div
        role="group"
        aria-label="Current propagation path"
        className="rounded-xl border border-border bg-surface-muted/20 p-3"
      >
        <SectionLabel>Current propagation path</SectionLabel>
        <p className="text-[11px] text-ink-soft">
          Select a file in the corridor or ledger to inspect the exact path A5 used to reach it.
        </p>
      </div>
    );
  }

  const chain = buildPathChain(focus, scopeByPath, originPath);

  return (
    <div
      role="group"
      aria-label="Current propagation path"
      className="rounded-xl border border-border bg-surface-muted/20 p-3"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <SectionLabel>Current propagation path</SectionLabel>
        <div className="font-mono text-[10px] text-ink-soft">
          hop {focus.hopCount ?? "?"} ·{" "}
          {focus.propagationConfidence !== null
            ? pct(focus.propagationConfidence)
            : "confidence not measured"}
        </div>
      </div>

      <p className="text-[11px] text-ink-soft">
        <span className="font-mono text-ink">{focus.path}</span> was {directionSummary(focus)} and
        sits {containmentSummary(focus)}.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {chain.map((path, index) => (
          <div key={`${path}:${index}`} className="flex items-center gap-2">
            <span className="rounded-lg border border-border bg-surface px-2.5 py-1 font-mono text-[11px] text-ink">
              {fileName(path)}
            </span>
            {index < chain.length - 1 && (
              <ArrowRight className="h-3 w-3 text-ink-soft" aria-hidden />
            )}
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-[10px]">
        <span className="rounded-full border border-border bg-surface px-2 py-1 text-ink-soft">
          {focus.edgeBasis === "name_contains"
            ? "Edge basis: name-matched only"
            : focus.edgeBasis === "resolved_suffix"
              ? "Edge basis: precise import match"
              : "Edge basis: origin"}
        </span>
        {focus.hasStaticFinding && (
          <span className="rounded-full border border-status-retry/40 bg-status-retry-bg px-2 py-1 text-status-retry">
            A3 independently flagged this file
          </span>
        )}
        {focus.autoPatchable && (
          <span className="rounded-full border border-status-running/40 bg-status-running-bg px-2 py-1 text-status-running">
            inside auto-patch scope
          </span>
        )}
        {focus.humanReviewRequired && (
          <span className="rounded-full border border-status-blocked/40 bg-status-blocked-bg px-2 py-1 text-status-blocked">
            human review required
          </span>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------- inspector

function Inspector({ file, onClose }: { file: BlastScopeFile; onClose: () => void }) {
  return (
    <div
      role="group"
      aria-label={`Details for ${file.path}`}
      className="rounded-lg border border-border bg-surface-muted/30 p-3"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="font-mono text-[11px] font-semibold text-ink">{file.path}</p>
        <button
          type="button"
          onClick={onClose}
          className="text-[10px] text-ink-soft hover:text-ink"
        >
          Close
        </button>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
        <dt className="text-ink-soft">Hop distance</dt>
        <dd className="font-mono text-ink">{file.hopCount ?? "Not measured"}</dd>
        <dt className="text-ink-soft">Reached via</dt>
        <dd className="font-mono text-ink">{file.reachedVia ?? "origin"}</dd>
        <dt className="text-ink-soft">Edge basis</dt>
        <dd className="text-ink">
          {file.edgeBasis === "resolved_suffix"
            ? "precise match (import resolves to this file's path)"
            : file.edgeBasis === "name_contains"
              ? "name-matched only — could be a false edge"
              : "n/a (origin)"}
        </dd>
        <dt className="text-ink-soft">Propagation confidence</dt>
        <dd className="font-mono text-ink">
          {file.propagationConfidence !== null ? pct(file.propagationConfidence) : "Not measured"}
        </dd>
        <dt className="text-ink-soft">Role (A1)</dt>
        <dd className="text-ink">
          {file.role ? (ROLE_LABEL[file.role] ?? file.role) : "Not measured"}
        </dd>
        <dt className="text-ink-soft">Criticality (A1)</dt>
        <dd className="font-mono text-ink">
          {file.criticality !== null ? file.criticality.toFixed(2) : "Not measured"}
        </dd>
        <dt className="text-ink-soft">Flagged by A3</dt>
        <dd className="text-ink">{file.hasStaticFinding ? "Yes" : "No"}</dd>
      </dl>
    </div>
  );
}

// ------------------------------------------------------------------ ledger

type SortKey = "hop" | "priority" | "path";

function ScopeLedger({
  scope,
  caveat,
  onSelect,
}: {
  scope: BlastScopeFile[];
  caveat: string;
  onSelect: (file: BlastScopeFile) => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("hop");
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  const rows = useMemo(() => {
    const filtered = onlyFlagged ? scope.filter((f) => f.hasStaticFinding) : scope;
    const sorted = [...filtered];
    if (sortKey === "hop") sorted.sort((a, b) => (a.hopCount ?? 99) - (b.hopCount ?? 99));
    if (sortKey === "priority")
      sorted.sort((a, b) => (b.priorityScore ?? -1) - (a.priorityScore ?? -1));
    if (sortKey === "path") sorted.sort((a, b) => a.path.localeCompare(b.path));
    return sorted;
  }, [scope, sortKey, onlyFlagged]);

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <SectionLabel>Scope ledger ({rows.length})</SectionLabel>
        <div className="ml-auto flex items-center gap-2 text-[10px]">
          <label className="flex items-center gap-1 text-ink-soft">
            <input
              type="checkbox"
              checked={onlyFlagged}
              onChange={(e) => setOnlyFlagged(e.target.checked)}
            />
            A3-flagged only
          </label>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            aria-label="Sort scope ledger"
            className="rounded border border-border bg-surface px-1 py-0.5 text-ink"
          >
            <option value="hop">Sort: hop</option>
            <option value="priority">Sort: priority</option>
            <option value="path">Sort: path</option>
          </select>
        </div>
      </div>
      <div className="max-h-[320px] overflow-auto rounded-lg border border-border">
        <table className="w-full border-collapse text-left text-[11px]">
          <thead className="sticky top-0 bg-surface">
            <tr className="border-b border-border text-[9px] uppercase tracking-wider text-ink-soft">
              <th className="px-2 py-1.5 font-medium">Path</th>
              <th className="px-2 py-1.5 font-medium">Hop</th>
              <th className="px-2 py-1.5 font-medium">Direction</th>
              <th className="px-2 py-1.5 font-medium">Role</th>
              <th className="px-2 py-1.5 font-medium">Propagation</th>
              <th className="px-2 py-1.5 font-medium">Priority</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr
                key={f.path}
                onClick={() => onSelect(f)}
                className="cursor-pointer border-b border-border/60 transition-colors hover:bg-surface-muted"
              >
                <td className="px-2 py-1 font-mono text-ink">
                  {f.path}
                  {f.hasStaticFinding && <span className="ml-1 text-status-retry">●</span>}
                </td>
                <td className="px-2 py-1 font-mono text-ink-soft">{f.hopCount ?? "—"}</td>
                <td className="px-2 py-1 text-ink-soft">{f.directions.join(" + ") || "—"}</td>
                <td className="px-2 py-1 text-ink-soft">
                  {f.role ? (ROLE_LABEL[f.role] ?? f.role) : "Not measured"}
                </td>
                <td className="px-2 py-1 font-mono text-ink-soft">
                  {f.propagationConfidence !== null ? pct(f.propagationConfidence) : "—"}
                </td>
                <td className="px-2 py-1 font-mono text-ink-soft">
                  {f.priorityScore !== null ? f.priorityScore.toFixed(2) : "Not measured"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-1 text-[10px] text-ink-soft">{caveat}</p>
    </div>
  );
}

// ------------------------------------------------------------------- panel

export function BlastRadiusPanel({ runId, status }: { runId: string; status?: AgentStatus }) {
  const [impact, setImpact] = useState<BlastImpact | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState<BlastScopeFile | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getBlastImpact(runId)
      .then((i) => {
        if (!cancelled) setImpact(i);
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

  useEffect(() => {
    if (!impact) {
      setSelected(null);
      return;
    }
    if (selected && !impact.scope.some((file) => file.path === selected.path)) {
      setSelected(null);
    }
  }, [impact, selected]);

  const direct = useMemo(() => (impact?.scope ?? []).filter((f) => f.hopCount === 1), [impact]);
  const transitive = useMemo(
    () => (impact?.scope ?? []).filter((f) => (f.hopCount ?? 0) >= 2),
    [impact],
  );
  const denseMode = useMemo(
    () => (impact?.scope ?? []).filter((f) => (f.hopCount ?? 0) >= 1).length > 12,
    [impact],
  );
  const beyondContainmentCount = useMemo(
    () => (impact?.scope ?? []).filter((f) => (f.hopCount ?? 0) >= 3).length,
    [impact],
  );
  const flaggedCount = useMemo(
    () => (impact?.scope ?? []).filter((f) => f.hasStaticFinding).length,
    [impact],
  );
  const focusPresets = useMemo(() => {
    if (!impact) return [];

    const candidates = (impact.scope ?? []).filter((file) => (file.hopCount ?? 0) >= 1);
    const options: FocusPreset[] = [];
    const seen = new Set<string>();
    const add = (id: string, label: string, detail: string, file: BlastScopeFile | null) => {
      if (!file || seen.has(file.path)) return;
      seen.add(file.path);
      options.push({ id, label, detail, file });
    };

    add(
      "most-exposed",
      "Most exposed",
      "Highest priority branch in the current scope.",
      topFile(candidates),
    );
    add(
      "upstream",
      "Upstream anchor",
      "Best entry point into possible cause surface.",
      topFile(candidates.filter((file) => file.directions.includes("backward"))),
    );
    add(
      "downstream",
      "Downstream fallout",
      "Best entry point into possible consumer fallout.",
      topFile(candidates.filter((file) => file.directions.includes("forward"))),
    );
    add(
      "farthest",
      "Farthest reach",
      "Longest propagation chain in the measured corridor.",
      topFile(
        [...candidates].sort((a, b) => {
          const hop = (b.hopCount ?? -1) - (a.hopCount ?? -1);
          return hop !== 0 ? hop : rankByPriorityThenConfidence(a, b);
        }),
      ),
    );

    return options;
  }, [impact]);
  const defaultFocus = useMemo(() => focusPresets[0]?.file ?? null, [focusPresets]);
  const focusFile = selected ?? defaultFocus;

  if (loading) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Blast Radius
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Loading impact analysis…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!impact) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Blast Radius
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A5 is traversing the impact scope now; this panel renders once it publishes."
            : "Pending — A5 has not completed for this run yet."}
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-3 rounded-2xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
            Blast Radius
          </h3>
          <p className="mt-0.5 text-[11px] text-ink-soft">
            If this change is real, what parts of the repository could potentially be affected — not
            what a third-party advisory reaches (that is A2), but what changing this file could
            reach.
          </p>
        </div>
        <div className="text-right">
          <div className="text-[9px] uppercase tracking-wider text-ink-soft">Scope</div>
          <div className="font-mono text-sm font-semibold text-ink">
            {impact.scope.length} file{impact.scope.length === 1 ? "" : "s"} · {impact.maxHop} hop
            {impact.maxHop === 1 ? "" : "s"}
          </div>
        </div>
      </div>

      <OriginCard impact={impact} />

      {impact.scope.length > 0 && (
        <>
          <ImpactGraph
            key={runId}
            impact={impact}
            onSelect={setSelected}
            selectedPath={focusFile?.path ?? null}
          />

          <details className="rounded-xl border border-border p-3">
            <summary className="cursor-pointer text-xs text-ink-soft">
              Inspect scope, provenance & patch eligibility
            </summary>
            <CorridorOverview
              directCount={direct.length}
              transitiveCount={transitive.length}
              beyondContainmentCount={beyondContainmentCount}
              flaggedCount={flaggedCount}
              denseMode={denseMode}
            />

            <FocusPresets
              options={focusPresets}
              selectedPath={focusFile?.path ?? null}
              onSelect={setSelected}
            />

            <PropagationReadout impact={impact} focus={focusFile} />

            <Flow />
            <DirectImpact files={direct} onSelect={setSelected} />
            <TransitiveImpact files={transitive} onSelect={setSelected} />

            <Flow />
            <div>
              <SectionLabel>Potentially affected areas</SectionLabel>
              <CapabilityRollup capabilities={impact.capabilities} />
            </div>

            <Flow />
            <div>
              <SectionLabel>Patch authority</SectionLabel>
              <PatchAuthorityStrip impact={impact} />
            </div>

            {selected && <Inspector file={selected} onClose={() => setSelected(null)} />}

            <ScopeLedger
              scope={impact.scope}
              caveat={impact.riskMeasurementCaveat}
              onSelect={setSelected}
            />
          </details>
        </>
      )}

      <p className="text-[10px] text-ink-soft">
        Explain: every file here traces to a real traversal step over A1&apos;s import graph, from
        the file A4 is investigating. Reachability is not proof of breakage — the outer hops are
        potential impact, decayed by distance. Source:{" "}
        <code className="font-mono">GET /api/runs/{"{run_id}"}/blast</code>
      </p>
    </section>
  );
}
