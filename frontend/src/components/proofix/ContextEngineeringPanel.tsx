/**
 * A5.5 — Context Flow.
 *
 * Everything here comes from `GET /api/runs/{runId}/context`
 * (`ContextPackage`, A5.5's own artifact — stored under the run's `context`
 * key, never on `RunStateModel` itself). No count, ribbon width, glyph or
 * status is invented: every number below is a direct field read, and the
 * derived facts (which files are "in context", ribbon thickness, the relative
 * bar widths) are deterministic set-membership and ratio computations over
 * fields the backend already sent — the same kind of client-side aggregation
 * A1 already does when it groups files by role.
 *
 * Distinct from A5 (blast radius: what changing a file could affect) and A6
 * (the repair plan: what will be changed and in what order). A5.5 answers
 * neither — it answers "what code did ProoFix decide the repair actually
 * needs to see, and did it pass the privacy check before A7 got it."
 *
 * Design: one picture, not several. The hero is a left-to-right flow of the
 * real files — repository → relevance → privacy gate → A7 — with each stage's
 * own count folded into that stage's header rather than restated in a second
 * funnel below it. Under it sit what survived, then two diagnostic rows
 * (privacy total, adoption) that open the ledger and the reason. Everything
 * else — why a file ranked, extracted source, constraints, timing — is behind
 * a click. There is no "Scanning → Extracting → Redacting" animation: A5.5
 * emits exactly one started/completed event pair, and animating intermediate
 * stages that do not exist would fake agent activity.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BookOpen,
  Braces,
  ChevronDown,
  ChevronRight,
  Circle,
  Files,
  Filter,
  FlaskConical,
  Flame,
  FolderCode,
  Link2,
  ShieldAlert,
  ShieldCheck,
  ShieldX,
  Shuffle,
  Target,
  TrendingDown,
  UserCheck,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { ContextPackageModel, ExtractedSymbol, RankedContextFile } from "@/mocks/runReport";
import { getRunContext } from "@/lib/runService";
import type { AgentStatus } from "./data";

// -------------------------------------------------------------- signal glyphs

/** Every signal key the ranker actually writes (`services/context_ranker.py`
 * `.add(...)` call sites), mapped to a static icon and label. Purely
 * presentational — no signal is invented, and a key not in this map still
 * renders (with a generic icon) rather than being silently dropped. */
const SIGNAL_ICON: Record<string, LucideIcon> = {
  target_resolution: Target,
  runtime_stack: Flame,
  reproduction_evidence: Flame,
  root_cause_confidence: FolderCode,
  static_finding: AlertTriangle,
  sig_distance: Link2,
  import_distance: Link2,
  call_graph_distance: Link2,
  dependency_centrality: Link2,
  call_fan_in: Link2,
  call_fan_out: Link2,
  graph_related: Link2,
  git_churn: Activity,
  history_churn: Activity,
  recent_modification: Activity,
  mutation_relevance: Activity,
  prior_repair: Wrench,
  co_change: Shuffle,
  ownership: UserCheck,
  documentation: BookOpen,
  graph_validated: FlaskConical,
  shared_utility_penalty: TrendingDown,
};

const SIGNAL_LABEL: Record<string, string> = {
  target_resolution: "Resolved patch target",
  runtime_stack: "Runtime stack frame",
  reproduction_evidence: "Reproduction evidence",
  root_cause_confidence: "Root-cause citation",
  static_finding: "Static finding",
  sig_distance: "Blast propagation",
  import_distance: "Import distance",
  call_graph_distance: "Call-graph edge",
  dependency_centrality: "Dependency centrality",
  call_fan_in: "Inbound calls",
  call_fan_out: "Outbound calls",
  graph_related: "Knowledge-graph proximity",
  git_churn: "Bug-fix churn",
  history_churn: "Historical churn",
  recent_modification: "Modified earlier this run",
  mutation_relevance: "Validation-failure relevance",
  prior_repair: "Prior successful repair",
  co_change: "Co-change history",
  ownership: "Ownership signal",
  documentation: "Documented",
  graph_validated: "Test coverage",
  shared_utility_penalty: "Shared-utility penalty",
};

function signalLabel(key: string): string {
  return SIGNAL_LABEL[key] ?? key.replace(/_/g, " ");
}

function signalIcon(key: string): LucideIcon {
  return SIGNAL_ICON[key] ?? Circle;
}

/** Nonzero signals only — a zero contributes nothing and must not draw as if it did. */
function nonzeroSignals(signals: Record<string, number>): [string, number][] {
  return Object.entries(signals).filter(([, v]) => v !== 0);
}

/** One signal key per distinct icon for the compact glyph row — several
 * signal keys share one icon (e.g. every dependency-distance signal is a
 * link), and showing the same glyph six times in a row would be noise, not
 * information. The first key to claim an icon lends it its tooltip label. */
function distinctSignalKeys(signals: Record<string, number>): string[] {
  const seenIcons = new Set<LucideIcon>();
  const keys: string[] = [];
  for (const [key] of nonzeroSignals(signals)) {
    const icon = signalIcon(key);
    if (seenIcons.has(icon)) continue;
    seenIcons.add(icon);
    keys.push(key);
  }
  return keys;
}

// ------------------------------------------------------------------ helpers

function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function ratio(numerator: number, denominator: number): number {
  if (denominator <= 0) return 0;
  return Math.max(0, Math.min(1, numerator / denominator));
}

/** Which files A5.5's extraction actually kept, derived the same way
 * `context_package.py` itself decides it: the target file counts once any of
 * its extraction lists is non-empty; a supporting file counts once it
 * contributes at least one symbol to `related_utilities`. Not a new fact —
 * a restatement of fields already in the payload. Plain function, not a
 * hook: called only once the package is known to exist. */
function inContextFiles(pkg: ContextPackageModel): Set<string> {
  const set = new Set<string>();
  const targetHasExtraction =
    pkg.relevant_imports.length > 0 ||
    pkg.relevant_classes.length > 0 ||
    pkg.relevant_functions.length > 0 ||
    pkg.constants.length > 0;
  if (targetHasExtraction && pkg.target_file) set.add(pkg.target_file);
  for (const symbol of pkg.related_utilities) {
    if (symbol.file) set.add(symbol.file);
  }
  return set;
}

/** The real in-context files, target first — a presentation order over the
 * genuine membership set, not new data. */
function selectedFiles(pkg: ContextPackageModel, inContextSet: Set<string>): string[] {
  return [...inContextSet].sort((a, b) => {
    if (a === pkg.target_file) return -1;
    if (b === pkg.target_file) return 1;
    return a.localeCompare(b);
  });
}

/** The three symbol categories the funnel's "selected context" is grouped
 * into, derived from `symbol.kind` — the backend's own label
 * (`context_extractor._label_function` / `_label_class` / `_label_constant`),
 * never guessed from the name. `config_object` is deliberately "other": the
 * backend applies that kind to both a config-shaped class and a
 * config-shaped constant, so it is genuinely ambiguous between the two and
 * must not be forced into either. */
type SymbolCategory = "function" | "class" | "other";

const FUNCTION_KINDS = new Set([
  "target_function",
  "dependent_function",
  "caller_function",
  "validation_helper",
  "utility",
]);
const CLASS_KINDS = new Set(["class_definition", "dataclass", "typed_model"]);

function symbolCategory(kind: string): SymbolCategory {
  if (CLASS_KINDS.has(kind)) return "class";
  if (FUNCTION_KINDS.has(kind)) return "function";
  return "other";
}

const CATEGORY_GLYPH: Record<SymbolCategory, string> = {
  function: "ƒ",
  class: "◇",
  other: "•",
};

const CATEGORY_LABEL: Record<SymbolCategory, string> = {
  function: "Function",
  class: "Class",
  other: "Other",
};

interface SelectedSymbol {
  name: string;
  category: SymbolCategory;
  isCallable: boolean;
}

/** Real selected symbols the repair context carries — `relevant_functions`
 * (the target file) plus `related_utilities` (supporting files),
 * deduplicated by name. `category`/`isCallable` come from the backend's own
 * `symbol.kind`, never guessed, so a constant is never labelled with a
 * misleading `()` and a class is never counted as a function. */
function selectedContextSymbols(pkg: ContextPackageModel): SelectedSymbol[] {
  const seen = new Set<string>();
  const out: SelectedSymbol[] = [];
  for (const symbol of [
    ...pkg.relevant_functions,
    ...pkg.relevant_classes,
    ...pkg.constants,
    ...pkg.related_utilities,
  ]) {
    if (!symbol.name || seen.has(symbol.name)) continue;
    seen.add(symbol.name);
    const category = symbolCategory(symbol.kind);
    out.push({ name: symbol.name, category, isCallable: category === "function" });
  }
  return out;
}

/** Extracted symbols grouped by the file they actually came from — every
 * symbol in these four lists already carries its own `.file`. */
function symbolsByFileOf(pkg: ContextPackageModel): Map<string, ExtractedSymbol[]> {
  const map = new Map<string, ExtractedSymbol[]>();
  for (const symbol of [
    ...pkg.relevant_classes,
    ...pkg.relevant_functions,
    ...pkg.constants,
    ...pkg.related_utilities,
  ]) {
    if (!symbol.file) continue;
    const list = map.get(symbol.file) ?? [];
    list.push(symbol);
    map.set(symbol.file, list);
  }
  return map;
}

// ------------------------------------------------------------------ chrome

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-status-failed/30 bg-status-failed-bg/40 px-3 py-2 text-xs text-ink"
    >
      <span className="font-medium text-status-failed">Could not load the context package</span>
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

/**
 * A compact row of real names — selected files or selected functions —
 * with a real overflow count rather than an unbounded list, so a large
 * package never grows the card past a glance. Chips, not a sentence: the
 * point of this row is that the names are visible without reading prose.
 */
function ChipRow({ items, max = 6 }: { items: string[]; max?: number }) {
  if (items.length === 0) return null;
  const shown = items.slice(0, max);
  const overflow = items.length - shown.length;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {shown.map((label) => (
        <span
          key={label}
          className="max-w-[220px] truncate rounded-md border border-border bg-surface-muted/50 px-1.5 py-0.5 font-mono text-[10px] text-ink-soft"
          title={label}
        >
          {label}
        </span>
      ))}
      {overflow > 0 && (
        <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[10px] text-ink-soft/70">
          +{overflow} more
        </span>
      )}
    </div>
  );
}

/**
 * Selected symbols, typed — each chip carries its category glyph (ƒ / ◇ / •)
 * rather than every symbol looking identical regardless of whether it is a
 * function, a class, or a constant. The category comes from the backend's
 * own `symbol.kind`; nothing here infers a type from the name.
 */
function SymbolChipRow({ symbols, max = 8 }: { symbols: SelectedSymbol[]; max?: number }) {
  if (symbols.length === 0) return null;
  const shown = symbols.slice(0, max);
  const overflow = symbols.length - shown.length;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {shown.map((s) => (
        <span
          key={s.name}
          className="inline-flex max-w-[220px] items-center gap-1 truncate rounded-md border border-border bg-surface-muted/50 px-1.5 py-0.5 font-mono text-[10px] text-ink-soft"
          title={`${CATEGORY_LABEL[s.category]}: ${s.name}`}
        >
          <span aria-hidden className="text-ink-soft/70">
            {CATEGORY_GLYPH[s.category]}
          </span>
          <span className="truncate">
            {s.name}
            {s.isCallable ? "()" : ""}
          </span>
        </span>
      ))}
      {overflow > 0 && (
        <span className="rounded-md border border-border/60 px-1.5 py-0.5 text-[10px] text-ink-soft/70">
          +{overflow} more
        </span>
      )}
    </div>
  );
}

/**
 * The actual output of A5.5's reduction: which files survived, and what was
 * pulled from them. This is the funnel's final band made concrete — instead
 * of ending on a bare count, the panel shows the real filenames (full
 * repository-relative paths, not basenames) and the real, typed symbol list.
 */
function SelectedContext({
  fileNames,
  symbols,
  lines,
}: {
  fileNames: string[];
  symbols: SelectedSymbol[];
  lines: number;
}) {
  if (fileNames.length === 0 && symbols.length === 0) return null;

  const counts: Record<SymbolCategory, number> = { function: 0, class: 0, other: 0 };
  for (const s of symbols) counts[s.category] += 1;
  const categoryOrder: SymbolCategory[] = ["function", "class", "other"];

  return (
    <div className="rounded border border-border px-2.5 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
          Context payload
        </span>
        <span className="font-mono text-[9px] uppercase tracking-wider text-ink-soft">
          <span className="text-ink">
            {fileNames.length} selected file{fileNames.length === 1 ? "" : "s"}
          </span>
          {lines > 0 && <span> · {lines} lines</span>}
        </span>
      </div>
      <ChipRow items={fileNames} max={8} />

      {symbols.length > 0 && (
        <>
          <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-ink-soft">
            {categoryOrder
              .filter((c) => counts[c] > 0)
              .map((c) => (
                <span key={c} className="flex items-center gap-1">
                  <span aria-hidden>{CATEGORY_GLYPH[c]}</span>
                  {counts[c]} {CATEGORY_LABEL[c]}
                  {counts[c] === 1 ? "" : "s"}
                </span>
              ))}
          </div>
          <SymbolChipRow symbols={symbols} />
        </>
      )}
    </div>
  );
}

/**
 * The divider between the panel's three blocks — flow, then what survived,
 * then the diagnostic rows. A drawn caret rather than a bare glyph, kept
 * small: it marks sequence, it is not itself a stage. Purely decorative
 * (`aria-hidden`): the blocks it joins are the real content.
 */
function FlowConnector() {
  return (
    <div className="flex justify-center py-0.5" aria-hidden>
      <svg width="10" height="8" viewBox="0 0 16 18" className="flow-connector text-ink-soft/25">
        <line x1="8" y1="0" x2="8" y2="11" stroke="currentColor" strokeWidth="1.5" />
        <path
          d="M3 10 L8 16 L13 10"
          stroke="currentColor"
          strokeWidth="1.5"
          fill="none"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

// ------------------------------------------------------------- stage summary

/**
 * The per-stage counts, as one compact horizontal strip:
 * `14 candidates → 3 extracted → 6 functions`. This used to be the panel's
 * hero — three stacked full-width bars — but it and the Context Flow above it
 * were two competing pictures of the same reduction, and the flow is the one
 * that carries file identity, privacy outcome and destination. So the bars
 * stay (each fill is still the real `count / candidates` ratio, and the shape
 * still narrows left to right) but at strip scale, as supporting numbers
 * under the flow rather than beside it.
 *
 * The label/value text sits on its own layer above the fill (not inside it),
 * so a heavily reduced row — fill near the clamped minimum — never clips or
 * shrinks its own numbers; every row reads at the same size.
 */
const MIN_FILL_PCT = 6;

function fillPct(count: number, base: number): number {
  if (base <= 0) return 100;
  const ratio = Math.max(0, Math.min(1, count / base));
  return MIN_FILL_PCT + (100 - MIN_FILL_PCT) * ratio;
}

function FunnelBarRow({
  widthPct,
  trackClass,
  fillClass,
  icon: Icon,
  label,
  value,
  sublabel,
  onClick,
  expanded,
}: {
  widthPct: number;
  trackClass: string;
  fillClass: string;
  icon: LucideIcon;
  label: string;
  value: string;
  sublabel?: string;
  onClick?: () => void;
  expanded?: boolean;
}) {
  const Wrapper = onClick ? "button" : "div";
  return (
    <Wrapper
      type={onClick ? "button" : undefined}
      onClick={onClick}
      aria-expanded={onClick ? expanded : undefined}
      className={`funnel-row relative block h-6 min-w-0 flex-1 overflow-hidden rounded border text-left ${trackClass} ${
        onClick ? "cursor-pointer" : ""
      }`}
    >
      <div
        aria-hidden
        className={`funnel-fill absolute inset-y-0 left-0 transition-[width] duration-500 ${fillClass}`}
        style={{ width: `${widthPct}%` }}
      />
      <div className="relative flex h-full items-center gap-1 px-1.5">
        <Icon className="h-3 w-3 shrink-0 text-ink-soft/70" aria-hidden />
        <span className="shrink-0 font-mono text-[10px] font-semibold text-ink">{value}</span>
        <span className="truncate text-[8px] uppercase tracking-wider text-ink-soft/60">
          {label}
        </span>
        {sublabel && (
          <span className="ml-auto shrink-0 truncate pl-1 text-[8px] text-ink-soft/60">
            {sublabel}
          </span>
        )}
        {onClick &&
          (expanded ? (
            <ChevronDown className="ml-auto h-2.5 w-2.5 shrink-0 text-ink-soft" aria-hidden />
          ) : (
            <ChevronRight className="ml-auto h-2.5 w-2.5 shrink-0 text-ink-soft" aria-hidden />
          ))}
      </div>
    </Wrapper>
  );
}

// ------------------------------------------------------------------ privacy

const PRIVACY_ICON: Record<ContextPackageModel["privacy_guard_status"], LucideIcon> = {
  clean: ShieldCheck,
  masked: ShieldAlert,
  failed: ShieldX,
};

const PRIVACY_COLOR: Record<ContextPackageModel["privacy_guard_status"], string> = {
  clean: "#16a34a",
  masked: "#d97706",
  failed: "#dc2626",
};

/** Per-file privacy outcome for the Context Flow ribbons. The backend gives
 * an aggregate `privacy_guard_status` plus a `redactions` list keyed by
 * file — there is no per-file guard verdict, so a file is only ever "masked"
 * because it has a real entry in `redactions`, and every file reads
 * "failed" together when the guard itself failed (no per-file recovery is
 * invented). Reuses `PRIVACY_ICON`/`PRIVACY_COLOR` exactly. */
function filePrivacy(
  file: string,
  pkg: ContextPackageModel,
  redactedFiles: Set<string>,
): ContextPackageModel["privacy_guard_status"] {
  if (pkg.privacy_guard_status === "failed") return "failed";
  if (redactedFiles.has(file)) return "masked";
  return "clean";
}

function PrivacyStage({
  pkg,
  onClick,
  expanded,
}: {
  pkg: ContextPackageModel;
  onClick: () => void;
  expanded: boolean;
}) {
  const status = pkg.privacy_guard_status;
  const StatusIcon = PRIVACY_ICON[status];
  const color = PRIVACY_COLOR[status];
  const count = pkg.metrics.privacy_redactions;

  // Verdict, count, and the way into the ledger — the per-file outcome is
  // already on the ribbons above, so this row adds no prose.
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={expanded}
      className="flex w-full items-center gap-2 rounded border border-border px-2.5 py-1.5 text-left transition-colors hover:bg-surface-muted/30"
    >
      <span className="w-24 shrink-0 font-mono text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
        Privacy gate
      </span>
      <StatusIcon className="h-3.5 w-3.5 shrink-0" style={{ color }} aria-hidden />
      <span
        className="shrink-0 font-mono text-[10px] font-semibold uppercase tracking-wider"
        style={{ color }}
      >
        {status}
      </span>
      {/* A failed guard is the one case that still spends a sentence — the
          flow can show that nothing reached the agent, but not why no claim
          is available. */}
      <span className="min-w-0 flex-1 truncate text-[10px] text-ink-soft">
        {status === "failed"
          ? "The guard errored. No safety claim can be made about this context."
          : `${count} redaction${count === 1 ? "" : "s"}`}
      </span>
      {expanded ? (
        <ChevronDown className="h-3 w-3 shrink-0 text-ink-soft" aria-hidden />
      ) : (
        <ChevronRight className="h-3 w-3 shrink-0 text-ink-soft" aria-hidden />
      )}
    </button>
  );
}

function RedactionLedger({ pkg }: { pkg: ContextPackageModel }) {
  if (pkg.privacy_guard_status === "failed") {
    return (
      <p className="rounded-md border border-status-failed/30 bg-status-failed-bg/40 px-2 py-1.5 text-[11px] text-ink">
        The guard errored. No claim can be made about what this context contained.
      </p>
    );
  }
  if (pkg.redactions.length === 0) {
    return (
      <p className="text-[11px] text-ink-soft">
        No secrets detected in the context handed to the patch generator.
      </p>
    );
  }
  return (
    <ul className="space-y-1">
      {pkg.redactions.map((r, i) => (
        <li
          key={`${r.file}:${r.line}:${i}`}
          className="flex flex-wrap items-center gap-x-2 rounded-md border border-status-retry/30 bg-status-retry-bg/40 px-2 py-1 font-mono text-[10px] text-ink"
        >
          <span>
            {r.file}:{r.line}
          </span>
          <span className="text-ink-soft">{r.identifier || r.kind}</span>
          <span className="ml-auto text-ink-soft">masked by {r.detector}</span>
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------ adoption

function AdoptionStage({ pkg }: { pkg: ContextPackageModel }) {
  const [open, setOpen] = useState(false);
  const adopted = pkg.prefer_focused;
  const hasReason = pkg.metrics.adoption_reason.trim().length > 0;

  // A7 throughout: A7 is the agent that consumes this package (`prefer_focused`
  // decides whether it replaces A7's own extraction), so the terminus, this
  // row and its explanation all name the same agent.
  return (
    <div role="group" aria-label="Adoption by A7" className="rounded border border-border">
      <div className="flex items-center gap-2 px-2.5 py-1.5">
        <span className="w-24 shrink-0 font-mono text-[9px] font-semibold uppercase tracking-wider text-ink-soft">
          A7 repair agent
        </span>
        <span
          className={`shrink-0 font-mono text-[10px] font-semibold uppercase tracking-wider ${
            adopted ? "text-status-completed" : "text-status-retry"
          }`}
        >
          {adopted ? "✓ ADOPTED" : "✕ NOT ADOPTED"}
        </span>
        {/* Adopted: "✓ ADOPTED" is the whole message for a sighted reader, so
            the sentence spelling it out is available to assistive tech rather
            than spending a line of the row.
            Not adopted: stays visible. A package that was fully built and
            privacy-checked can still be discarded by A7, and that is
            consequential enough to state on screen rather than imply from a
            glyph. */}
        {adopted ? (
          <span className="sr-only">
            Focused context accepted — A7 uses this package instead of its own extraction.
          </span>
        ) : (
          <span className="min-w-0 flex-1 truncate text-[10px] text-ink-soft">
            A7 keeps its own extraction; this package was not used.
          </span>
        )}
        {adopted && <span className="flex-1" aria-hidden />}
        {adopted && hasReason && (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex shrink-0 items-center gap-0.5 text-[10px] font-medium text-ink-soft transition-colors hover:text-ink"
          >
            {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            Why
          </button>
        )}
      </div>
      {!adopted && hasReason && (
        <p className="px-2.5 pb-1.5 text-[10px] text-ink-soft">
          Reason: {pkg.metrics.adoption_reason}.
        </p>
      )}
      {adopted && hasReason && open && (
        <p className="px-2.5 pb-1.5 text-[10px] text-ink-soft">{pkg.metrics.adoption_reason}.</p>
      )}
    </div>
  );
}

// -------------------------------------------------------------- relevance map

function ExtractedSymbolBlock({ symbol }: { symbol: ExtractedSymbol }) {
  return (
    <div className="rounded-md border border-border bg-surface-muted/40 p-2">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="font-mono text-[11px] font-semibold text-ink">
          {symbol.qualname || symbol.name}
        </span>
        <span className="text-[9px] uppercase tracking-wider text-ink-soft">{symbol.kind}</span>
        {symbol.signature_only && (
          <span className="text-[9px] uppercase tracking-wider text-status-retry">
            signature only
          </span>
        )}
        <span className="ml-auto font-mono text-[9px] text-ink-soft">
          L{symbol.lineno}-{symbol.end_lineno}
        </span>
      </div>
      {symbol.source && (
        <pre className="mt-1 max-h-[220px] overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] text-ink">
          {symbol.source}
        </pre>
      )}
    </div>
  );
}

/** The three "why selected" badge groups — a quick-glance summary above the
 * full signal breakdown, not a replacement for it. Every badge is gated on
 * a real nonzero signal already in `file.signals`; a file with none of
 * these three groups simply shows no badge (its reason is still visible in
 * the full Signals list below). */
const REASON_BADGES: { glyph: string; label: string; signals: string[] }[] = [
  { glyph: "🎯", label: "Target", signals: ["target_resolution"] },
  { glyph: "🔥", label: "Runtime evidence", signals: ["runtime_stack", "reproduction_evidence"] },
  {
    glyph: "🔗",
    label: "Dependency",
    signals: [
      "sig_distance",
      "import_distance",
      "call_graph_distance",
      "dependency_centrality",
      "call_fan_in",
      "call_fan_out",
      "graph_related",
    ],
  },
];

function reasonBadges(signals: Record<string, number>): { glyph: string; label: string }[] {
  return REASON_BADGES.filter(({ signals: keys }) => keys.some((k) => (signals[k] || 0) !== 0));
}

/** Real lines the extractor kept for this file — the sum of each full-body
 * (non signature-only) symbol's own span. Signature-only symbols contribute
 * no body lines, so they are not counted; this is a fact already implied by
 * `ExtractedSymbol`, not a new measurement. */
function linesSelected(symbols: ExtractedSymbol[]): number {
  return symbols
    .filter((s) => !s.signature_only)
    .reduce((total, s) => total + Math.max(0, s.end_lineno - s.lineno + 1), 0);
}

function FileDetail({
  file,
  inContext,
  symbols,
}: {
  file: RankedContextFile;
  inContext: boolean;
  symbols: ExtractedSymbol[];
}) {
  const signals = nonzeroSignals(file.signals);
  const badges = reasonBadges(file.signals);
  const lines = linesSelected(symbols);
  return (
    <div className="space-y-2 border-t border-border/60 px-2 py-2">
      {badges.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {badges.map((b) => (
            <span
              key={b.label}
              className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-ink"
            >
              <span aria-hidden>{b.glyph}</span>
              {b.label}
            </span>
          ))}
        </div>
      )}
      {inContext && lines > 0 && (
        <div className="text-[10px] text-ink-soft">
          <span className="font-mono font-semibold text-ink">{lines}</span> line
          {lines === 1 ? "" : "s"} selected
        </div>
      )}
      {signals.length > 0 && (
        <div>
          <div className="text-[9px] uppercase tracking-wider text-ink-soft">Signals</div>
          <ul className="mt-0.5 space-y-0.5">
            {signals.map(([key, value]) => {
              const Icon = signalIcon(key);
              return (
                <li key={key} className="flex items-center gap-1.5 text-[10px]">
                  <Icon className="h-3 w-3 text-ink-soft" aria-hidden />
                  <span className="text-ink">{signalLabel(key)}</span>
                  <span className="ml-auto font-mono text-ink-soft">
                    {value > 0 ? "+" : ""}
                    {value.toFixed(2)}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {file.evidence.length > 0 && (
        <div>
          <div className="text-[9px] uppercase tracking-wider text-ink-soft">Evidence</div>
          <ul className="mt-0.5 list-inside list-disc space-y-0.5 text-[10px] text-ink-soft">
            {file.evidence.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="text-[10px] text-ink-soft">
        File confidence: <span className="font-mono text-ink">{file.confidence.toFixed(2)}</span>
      </div>
      {inContext && symbols.length > 0 && (
        <div>
          <div className="mb-1 text-[9px] uppercase tracking-wider text-ink-soft">
            Extracted source
          </div>
          <div className="space-y-1.5">
            {symbols.map((s) => (
              <ExtractedSymbolBlock
                key={`${s.file}:${s.qualname || s.name}:${s.lineno}`}
                symbol={s}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function RelevanceMapRow({
  file,
  topScore,
  inContext,
  symbols,
}: {
  file: RankedContextFile;
  topScore: number;
  inContext: boolean;
  symbols: ExtractedSymbol[];
}) {
  const [open, setOpen] = useState(false);
  const width = topScore > 0 ? ratio(Math.max(file.score, 0), topScore) : 0;

  return (
    <div className="rounded-lg border border-border bg-surface-muted/20">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-1.5">
            <span className="truncate font-mono text-[11px] text-ink" title={file.file}>
              {file.file}
            </span>
            {file.is_target && (
              <span className="rounded bg-primary/15 px-1 text-[9px] font-semibold uppercase tracking-wider text-primary">
                target
              </span>
            )}
          </span>
          <span className="mt-0.5 flex items-center gap-1.5">
            <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-muted">
              <span
                className="block h-full rounded-full bg-primary"
                style={{ width: `${Math.round(width * 100)}%` }}
              />
            </span>
            {/* The bar is normalized for display; this is the real,
                un-normalized backend score, so nothing here can be mistaken
                for a fabricated percentage. */}
            <span className="shrink-0 font-mono text-[10px] text-ink-soft">
              {file.score.toFixed(2)}
            </span>
            <span className="flex shrink-0 gap-1">
              {distinctSignalKeys(file.signals).map((key) => {
                const Icon = signalIcon(key);
                return (
                  <span key={key} title={signalLabel(key)}>
                    <Icon className="h-3 w-3 text-ink-soft" aria-hidden />
                  </span>
                );
              })}
            </span>
          </span>
        </span>
        <span
          className={`shrink-0 text-[9px] font-semibold uppercase tracking-wider ${
            inContext ? "text-status-completed" : "text-ink-soft"
          }`}
        >
          {inContext ? "✓ in context" : "— excluded"}
        </span>
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-ink-soft" aria-hidden />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-ink-soft" aria-hidden />
        )}
      </button>
      {open && <FileDetail file={file} inContext={inContext} symbols={symbols} />}
    </div>
  );
}

function RelevanceMap({
  pkg,
  inContextSet,
  symbolsByFile,
}: {
  pkg: ContextPackageModel;
  inContextSet: Set<string>;
  symbolsByFile: Map<string, ExtractedSymbol[]>;
}) {
  const topScore = useMemo(
    () => Math.max(0, ...pkg.ranked_files.map((f) => f.score)),
    [pkg.ranked_files],
  );

  if (pkg.ranked_files.length === 0) {
    return <p className="text-[11px] text-ink-soft">No candidate files were ranked.</p>;
  }

  return (
    <div className="space-y-1">
      {pkg.ranked_files.map((file) => (
        <RelevanceMapRow
          key={file.file}
          file={file}
          topScore={topScore}
          inContext={inContextSet.has(file.file)}
          symbols={symbolsByFile.get(file.file) ?? []}
        />
      ))}
    </div>
  );
}

// -------------------------------------------------------------- context flow

/** Ribbon color by dominant signal category — the same three groups
 * `REASON_BADGES` already derives from real nonzero signals. A file with no
 * matched group (or no ranked-file entry at all, e.g. it only appears via
 * `related_utilities`) gets the neutral default rather than a guessed one. */
const CATEGORY_RIBBON_COLOR: Record<string, string> = {
  Target: "var(--color-primary)",
  "Runtime evidence": "var(--color-status-retry)",
  Dependency: "var(--color-ink-soft)",
};
const DEFAULT_RIBBON_COLOR = "var(--color-ink-soft)";

function dominantRibbonColor(signals: Record<string, number> | undefined): string {
  if (!signals) return DEFAULT_RIBBON_COLOR;
  const badges = reasonBadges(signals);
  return badges.length > 0
    ? (CATEGORY_RIBBON_COLOR[badges[0].label] ?? DEFAULT_RIBBON_COLOR)
    : DEFAULT_RIBBON_COLOR;
}

/** Bounded presentational stroke thickness from a real line count — never a
 * new metric, just a rendering choice so one large file cannot dominate the
 * row. Files with no extracted body (signature-only, or no ranked entry)
 * still draw a thin, visible line rather than vanishing. */
function ribbonThickness(lines: number): number {
  if (lines <= 0) return 2;
  return Math.max(2, Math.min(8, 2 + Math.round(lines / 10)));
}

/* Flow geometry. Fixed so the ribbon rows, the stage headers above them and
 * the convergence curves on the right all resolve to the same coordinates —
 * the markers and their labels cannot drift apart. */
const ROW_H = 40; // one file row — tall enough to track a ribbon across
const REL_X = 30; // % along the track: relevance marker
const GATE_X = 64; // % along the track: privacy checkpoint
const GATE_GAP = 11; // px the ribbon is cut either side of a masked checkpoint
const NAME_COL = "34%"; // repository / filename column
const CONV_W = 26; // convergence curve column, px
const STAGE_HEAD_H = 50; // label (2 lines) + chip; all four stages share this top
const MIN_FLOW_H = 84; // floor only, so a 1–2 file package is not squashed

/* Widths are proportional first and capped second, so the flow reflows with
 * the panel instead of overflowing it. The cap keeps them from ballooning on
 * a wide screen; the percentage is what makes them shrink on a narrow one.
 *
 * The chip width matters most: ② and ③ are centred on 30% and 64% of the
 * track, so a box of at most 26% of the track spans 17–43% and 51–77%. Those
 * ranges cannot meet at any width, which is what stops the two stage labels
 * from colliding as the panel narrows — the failure mode a fixed 124px box
 * had below roughly 470px of track. */
const NODE_W = "min(140px, 22%)"; // repair-agent terminus
const NODE_COL = `calc(${NODE_W} + 4px)`; // terminus + its 4px gutter
/* ② and ③ are absolutely positioned inside the track, so their 26% is 26% of
 * the track — the figure the no-collision reasoning above depends on. ① and ④
 * sit inside their own columns, where a percentage would resolve against that
 * column instead and collapse the chip, so they cap against their column. */
const CHIP_W = "min(124px, 26%)"; // ② / ③ — measured against the track
const CHIP_MAX = "min(124px, 100%)"; // ① / ④ — measured against their column

/**
 * One real file, flowing repository → relevance → privacy gate → repair
 * context. Non-clickable by design: the existing expand/inspect behavior for
 * a file already lives in `RelevanceMapRow`/`FileDetail` behind the Context
 * Map toggle, so this row reuses that data but not that state.
 *
 * Tooltips are deliberately per-element and short — the full path on the
 * name, the guard verdict on the checkpoint. A row-level `title` carrying
 * path + lines + signals used to render as one wide native tooltip that
 * covered the stage headers, and a native tooltip's position cannot be
 * controlled from CSS; the fix is to not produce a large one. Everything it
 * carried is on screen already (line count) or one click away in the Context
 * Map (why the file ranked).
 *
 * A `failed` guard truncates the ribbon at the checkpoint: nothing can be
 * claimed to have safely reached the agent, so nothing is drawn as if it had.
 */
function FlowRibbonRow({
  file,
  rankedFile,
  lines,
  privacy,
  redactionCount,
}: {
  file: string;
  rankedFile: RankedContextFile | undefined;
  lines: number;
  privacy: ContextPackageModel["privacy_guard_status"];
  redactionCount: number;
}) {
  const color = dominantRibbonColor(rankedFile?.signals);
  const thickness = ribbonThickness(lines);
  const privacyColor = PRIVACY_COLOR[privacy];
  const failed = privacy === "failed";
  const masked = privacy === "masked";
  // Masked ribbons narrow after the gate — a real redaction removed content.
  const afterThickness = masked ? Math.max(2, thickness - 2) : thickness;
  const privacyTitle = failed
    ? "Privacy gate: failed — no safety claim can be made about this context"
    : masked
      ? `Privacy gate: masked — ${redactionCount} redaction${redactionCount === 1 ? "" : "s"} in this file`
      : "Privacy gate: clean";

  return (
    <div className="flex items-center" style={{ height: ROW_H }}>
      <span
        className="shrink-0 truncate pr-2 font-mono text-[11px] text-ink"
        style={{ width: NAME_COL }}
        title={file}
      >
        {file}
      </span>

      <span className="relative min-w-0 flex-1" style={{ height: ROW_H }} aria-hidden>
        {/* Track before the gate. A masked ribbon stops short of the
            checkpoint and resumes past it, thinner — so the gate reads as a
            place where the ribbon was cut and something was taken out of it,
            not merely a badge sitting on an unbroken line. */}
        <span
          className="absolute rounded-full"
          style={{
            left: 0,
            width: `calc(${GATE_X}% - ${masked ? GATE_GAP : 0}px)`,
            top: `calc(50% - ${thickness / 2}px)`,
            height: thickness,
            background: color,
            opacity: 0.7,
          }}
        />
        {/* track after the gate — omitted entirely when the guard failed */}
        {!failed && (
          <span
            className="absolute rounded-full"
            style={{
              left: `calc(${GATE_X}% + ${masked ? GATE_GAP : 0}px)`,
              right: 0,
              top: `calc(50% - ${afterThickness / 2}px)`,
              height: afterThickness,
              background: color,
              opacity: 0.7,
            }}
          />
        )}
        {/* relevance marker */}
        <span
          className="absolute h-1.5 w-1.5 rounded-full"
          style={{
            left: `${REL_X}%`,
            top: "calc(50% - 3px)",
            background: color,
            boxShadow: "0 0 0 3px var(--color-surface)",
          }}
        />
        {/* privacy checkpoint */}
        <span
          className="absolute flex items-center gap-1"
          style={{ left: `${GATE_X}%`, top: "50%", transform: "translate(-50%, -50%)" }}
        >
          <span
            className="flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold leading-none"
            style={{
              background: "var(--color-surface)",
              border: `1px solid ${privacyColor}`,
              color: privacyColor,
            }}
            title={privacyTitle}
          >
            {failed ? "✕" : masked ? "⚠" : "✓"}
          </span>
        </span>
        {(masked || failed) && (
          <span
            /* Below ~32rem of panel the track is too short to seat this
               beside the checkpoint without running into the line count; the
               coloured badge still carries the verdict, and the privacy row
               below states it in words. */
            className="absolute hidden whitespace-nowrap font-mono text-[8px] font-semibold uppercase tracking-wider @lg:inline"
            style={{
              left: `calc(${GATE_X}% + 12px)`,
              top: "50%",
              transform: "translateY(-50%)",
              color: privacyColor,
              background: "var(--color-surface)",
              paddingLeft: 2,
              paddingRight: 2,
            }}
          >
            {/* The badge beside this already carries the ⚠ / ✕ glyph, so the
                label is words only. The masked form carries this file's own
                real redaction count. */}
            {failed ? "Guard failed" : `Masked · ${redactionCount}`}
          </span>
        )}
        {/* How much of this file arrived, annotated at the end of its own
            track rather than in a separate column: the gutter it used to sit
            in lined up with no stage header and broke the ribbon between the
            gate and the convergence. */}
        {lines > 0 && (
          <span
            className="absolute right-0 hidden font-mono text-[9px] text-ink-soft/70 @lg:inline"
            style={{
              top: "50%",
              transform: "translateY(-50%)",
              background: "var(--color-surface)",
              paddingLeft: 4,
            }}
          >
            {lines} lines
          </span>
        )}
      </span>
    </div>
  );
}

/**
 * Candidates that were considered but did not reach the repair context,
 * drawn as one subordinate branch that diverges from the flow and stops at
 * the relevance stage. The count is always derived from real
 * `files_ranked` / real selected files — never estimated — and no reason is
 * implied beyond "not selected", because the package does not carry one.
 */
function ExcludedBranch({ text }: { text: string }) {
  return (
    <div className="flex items-center" style={{ height: ROW_H }}>
      <span className="shrink-0" style={{ width: NAME_COL }} aria-hidden />
      <span className="relative min-w-0 flex-1" style={{ height: ROW_H }}>
        <svg className="absolute inset-0 h-full w-full text-ink-soft/30" aria-hidden={true}>
          {/* diverges away from the flow and stops — these never reach the gate */}
          <path
            d="M 0 0 C 14 0, 10 17, 26 17"
            fill="none"
            stroke="currentColor"
            strokeWidth="1"
            strokeDasharray="2 3"
          />
        </svg>
        <span
          className="absolute truncate whitespace-nowrap text-[9px] italic text-ink-soft/50"
          style={{ left: 32, top: "50%", transform: "translateY(-50%)" }}
        >
          {text}
        </span>
      </span>
    </div>
  );
}

/**
 * The terminus the ribbons converge into. Carries only what the ④ stage
 * header above it does not already say: the adoption state
 * (`prefer_focused`, unchanged logic) and the resolved target function. The
 * agent's name lives in that header; the full adoption reason lives in
 * `AdoptionStage` below.
 */
function RepairAgentNode({ pkg, height }: { pkg: ContextPackageModel; height: number }) {
  const adopted = pkg.prefer_focused;
  const accent = adopted ? "var(--color-status-completed)" : "var(--color-status-retry)";
  return (
    <div
      className="flex w-full min-w-0 flex-col justify-center rounded border px-2 py-2"
      style={{
        minHeight: Math.min(height, 56),
        borderColor: `color-mix(in srgb, ${accent} 35%, transparent)`,
        background: `color-mix(in srgb, ${accent} 7%, transparent)`,
      }}
    >
      <div className="flex min-w-0 items-center gap-1">
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0" style={{ color: accent }} aria-hidden />
        <span
          className="truncate font-mono text-[10px] font-semibold uppercase tracking-wider"
          style={{ color: accent }}
          title={adopted ? "Context adopted" : "Context not adopted"}
        >
          {adopted ? "Context adopted" : "Context not adopted"}
        </span>
      </div>
      {pkg.target_function && (
        <div
          className="mt-1 truncate font-mono text-[10px] text-ink-soft"
          title={pkg.target_function}
        >
          {pkg.target_function}
        </div>
      )}
    </div>
  );
}

/**
 * The privacy stage's own count, so all four stage headers carry one and the
 * band reads as a row rather than three chips and a gap. Deliberately not a
 * `FunnelBarRow`: this is a verdict, not a share of the candidate pool, so it
 * has no ratio to fill. The status word itself stays out of the chip — it is
 * on every ribbon below and in the ledger row — leaving the count, which
 * appears only here.
 */
function PrivacyStageChip({ pkg }: { pkg: ContextPackageModel }) {
  const status = pkg.privacy_guard_status;
  const Icon = PRIVACY_ICON[status];
  const color = PRIVACY_COLOR[status];
  const n = pkg.metrics.privacy_redactions;
  return (
    <div
      className="flex h-6 min-w-0 flex-1 items-center gap-1 rounded border px-1.5"
      style={{
        borderColor: `color-mix(in srgb, ${color} 30%, transparent)`,
        background: `color-mix(in srgb, ${color} 8%, transparent)`,
      }}
    >
      <Icon className="h-3 w-3 shrink-0" style={{ color }} aria-hidden />
      {status === "failed" ? (
        <span className="truncate font-mono text-[9px] font-semibold" style={{ color }}>
          Guard failed
        </span>
      ) : (
        <>
          <span className="shrink-0 font-mono text-[10px] font-semibold text-ink">{n}</span>
          <span className="truncate text-[8px] uppercase tracking-wider text-ink-soft/60">
            redaction{n === 1 ? "" : "s"}
          </span>
        </>
      )}
    </div>
  );
}

/** A numbered stage label. The numbering is the flow's own left-to-right
 * order, and appears nowhere else in the panel. */
function StageLabel({
  index,
  label,
  sublabel,
}: {
  index: string;
  label: string;
  sublabel: string;
}) {
  return (
    <>
      <div
        className="truncate font-mono text-[9px] font-semibold uppercase tracking-wider text-ink-soft"
        title={label}
      >
        {index} {label}
      </div>
      {/* The sublabel is the first thing to go on a narrow panel: it restates
          the stage rather than adding a fact, so dropping it buys width for
          the labels and counts that do. */}
      <div className="hidden truncate text-[8px] uppercase tracking-wider text-ink-soft/50 @md:block">
        {sublabel}
      </div>
    </>
  );
}

/**
 * The hero: real files flowing repository → relevance → privacy gate →
 * repair agent, converging into a single bundle at the terminus.
 *
 * Every row is a file already present in `selectedFileNames` (the same set
 * `SelectedContext`'s chips draw from), so nothing here can show a filename
 * the rest of the panel would call "excluded" — unselected candidates are
 * represented only as a real aggregate count on a subordinate branch, never
 * individually, until the user opens the existing Context Map for the full
 * per-file breakdown.
 */
function ContextFlowDiagram({
  pkg,
  symbolsByFile,
  candidates,
  extracted,
  selectedFileNames,
  relevanceOpen,
  onToggleRelevance,
}: {
  pkg: ContextPackageModel;
  symbolsByFile: Map<string, ExtractedSymbol[]>;
  candidates: number;
  extracted: number;
  selectedFileNames: string[];
  relevanceOpen: boolean;
  onToggleRelevance: () => void;
}) {
  const redactionsByFile = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of pkg.redactions) m.set(r.file, (m.get(r.file) ?? 0) + 1);
    return m;
  }, [pkg.redactions]);
  const rankedByFile = useMemo(() => {
    const m = new Map<string, RankedContextFile>();
    for (const f of pkg.ranked_files) m.set(f.file, f);
    return m;
  }, [pkg.ranked_files]);

  // No early return on an empty selection: "14 considered, none survived" is
  // the case where the stage counts matter most, so the flow still renders —
  // with no ribbons, and the excluded branch carrying the whole candidate set.
  const ROW_CAP = 5;
  const shown = selectedFileNames.slice(0, ROW_CAP);
  const overflowSelected = selectedFileNames.length - shown.length;
  const notSelected = Math.max(0, candidates - selectedFileNames.length);
  const excludedText =
    notSelected > 0
      ? `${notSelected} other candidate${notSelected === 1 ? "" : "s"} excluded`
      : overflowSelected > 0
        ? `+${overflowSelected} more selected file${overflowSelected === 1 ? "" : "s"}`
        : null;

  const contextFiles = pkg.metrics.context_files;
  const contextFunctions = pkg.metrics.context_functions;
  const wCandidates = 100;
  const wExtracted = fillPct(extracted, candidates);
  const wContext = fillPct(contextFiles, candidates);

  const rowCount = shown.length + (excludedText ? 1 : 0);
  const flowH = Math.max(rowCount * ROW_H, MIN_FLOW_H);
  const stackTop = (flowH - rowCount * ROW_H) / 2;
  const centerY = flowH / 2;
  // One curve per ribbon that actually reaches the agent; a failed guard
  // truncates its ribbon at the gate, so it gets no curve either.
  const curves = shown
    .map((file, i) => ({
      file,
      y: stackTop + i * ROW_H + ROW_H / 2,
      color: dominantRibbonColor(rankedByFile.get(file)?.signals),
      reaches: pkg.privacy_guard_status !== "failed",
    }))
    .filter((c) => c.reaches);

  return (
    // `@container`: the flow reflows against the panel's own width, not the
    // viewport's — this panel sits in a column beside a fixed sidebar, so a
    // viewport breakpoint would fire at the wrong moment.
    <div className="@container rounded-lg border border-border bg-surface-muted/10 p-3">
      {/* The target the whole flow is about, on the flow's own header line —
          the card's title and description come from the timeline entry that
          renders this panel, so neither is repeated here. */}
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="shrink-0 font-mono text-[10px] font-semibold uppercase tracking-widest text-ink">
          Context flow
        </span>
        <span className="min-w-0 truncate font-mono text-[11px] text-ink" title={pkg.target_file}>
          {pkg.target_file}
          {pkg.target_function && <span className="text-ink-soft"> :: {pkg.target_function}</span>}
        </span>
      </div>

      {/* Stage headers, each carrying that stage's own count. This row mirrors
          a ribbon row's column structure exactly — name column, track,
          line-count gutter, then the curve column and the terminus — so ② and
          ③ land on the same percentage coordinates as the markers they label,
          at every width. The counts live here rather than in a separate strip
          below: one picture of the reduction, not two. */}
      <div className="flex items-start border-b border-border/60 pb-1.5">
        <div className="flex min-w-0 flex-1">
          <div className="shrink-0 pr-2" style={{ width: NAME_COL }}>
            <StageLabel index="①" label="Repository" sublabel="Candidates" />
            <div className="mt-1 flex" style={{ maxWidth: CHIP_MAX }}>
              <FunnelBarRow
                widthPct={wCandidates}
                trackClass="border-border"
                fillClass="bg-ink-soft/10"
                icon={Files}
                label="candidates"
                value={String(candidates)}
              />
            </div>
          </div>
          <div className="relative min-w-0 flex-1" style={{ height: STAGE_HEAD_H }}>
            {/* ② and ③ label point markers, not columns, so they centre on
                the marker's own x rather than left-aligning beside it. */}
            <div
              className="absolute top-0 text-center"
              style={{ left: `${REL_X}%`, transform: "translateX(-50%)", width: CHIP_W }}
            >
              <StageLabel index="②" label="Relevance" sublabel="Filtered" />
              <div className="mt-1 flex">
                <FunnelBarRow
                  widthPct={wExtracted}
                  trackClass="border-primary/25 hover:border-primary/45"
                  fillClass="bg-primary/15"
                  icon={Filter}
                  label="extracted"
                  value={String(extracted)}
                  onClick={onToggleRelevance}
                  expanded={relevanceOpen}
                />
              </div>
            </div>
            {/* Identical container shape to ② — same fixed width, same
                centring transform — so both resolve to exactly their marker's
                x. A shrink-to-fit box centres differently once its content
                overflows, which is what drifted this label off its badge. */}
            <div
              className="absolute top-0 text-center"
              style={{ left: `${GATE_X}%`, transform: "translateX(-50%)", width: CHIP_W }}
            >
              <StageLabel index="③" label="Privacy gate" sublabel="Sanitized" />
              <div className="mt-1 flex">
                <PrivacyStageChip pkg={pkg} />
              </div>
            </div>
          </div>
        </div>
        <div className="shrink-0" style={{ width: CONV_W }} aria-hidden />
        <div className="shrink-0 pl-1" style={{ width: NODE_COL }}>
          <StageLabel index="④" label="A7 repair agent" sublabel="Context received" />
          <div className="mt-1 flex" style={{ maxWidth: CHIP_MAX }}>
            {/* Value only — "in N files" would truncate at this column width,
                and `SelectedContext` below already states the file count. */}
            <FunnelBarRow
              widthPct={wContext}
              trackClass="border-primary/30"
              fillClass="bg-primary/25"
              icon={Braces}
              label=""
              value={`${contextFunctions} function${contextFunctions === 1 ? "" : "s"}`}
            />
          </div>
        </div>
      </div>

      {/* The flow itself. */}
      <div className="flex items-stretch pt-1">
        <div className="flex min-w-0 flex-1 flex-col justify-center" style={{ height: flowH }}>
          {shown.map((file) => (
            <FlowRibbonRow
              key={file}
              file={file}
              rankedFile={rankedByFile.get(file)}
              lines={linesSelected(symbolsByFile.get(file) ?? [])}
              privacy={filePrivacy(file, pkg, new Set(redactionsByFile.keys()))}
              redactionCount={redactionsByFile.get(file) ?? 0}
            />
          ))}
          {excludedText && <ExcludedBranch text={excludedText} />}
          {rowCount === 0 && (
            <div className="text-[10px] italic text-ink-soft/50">No candidates ranked.</div>
          )}
        </div>

        {/* Convergence into the terminus — the ribbons bundling into one hand-off. */}
        <svg
          width={CONV_W}
          height={flowH}
          className="shrink-0"
          aria-hidden
          style={{ overflow: "visible" }}
        >
          {curves.map((c) => (
            <path
              key={c.file}
              d={`M 0 ${c.y} C ${CONV_W * 0.6} ${c.y}, ${CONV_W * 0.4} ${centerY}, ${CONV_W} ${centerY}`}
              fill="none"
              stroke={c.color}
              strokeWidth="1.5"
              opacity="0.55"
            />
          ))}
        </svg>

        {/* Same width expression as the ④ header column above, resolved
            against the same parent, so the two stay locked together as the
            panel resizes. */}
        <div className="flex shrink-0 items-center pl-1" style={{ width: NODE_COL }}>
          <RepairAgentNode pkg={pkg} height={flowH} />
        </div>
      </div>
    </div>
  );
}

// -------------------------------------------------------------- collapsibles

function CollapsibleSection({
  label,
  count,
  children,
}: {
  label: string;
  count?: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 text-ink-soft" />
        ) : (
          <ChevronRight className="h-3 w-3 text-ink-soft" />
        )}
        <span className="text-[10px] font-semibold uppercase tracking-wider text-ink">{label}</span>
        {count !== undefined && (
          <span className="ml-auto font-mono text-[10px] text-ink-soft">{count}</span>
        )}
      </button>
      {open && <div className="px-2.5 pb-2">{children}</div>}
    </div>
  );
}

function StringList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <p className="text-[11px] text-ink-soft">{empty}</p>;
  return (
    <ul className="list-inside list-disc space-y-0.5 text-[11px] text-ink">
      {items.map((c) => (
        <li key={c}>{c}</li>
      ))}
    </ul>
  );
}

function PerformanceDetails({ pkg }: { pkg: ContextPackageModel }) {
  const m = pkg.metrics;
  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
        <dt className="text-ink-soft">Estimated original tokens</dt>
        <dd className="font-mono text-ink">{m.original_tokens}</dd>
        <dt className="text-ink-soft">Estimated reduced tokens</dt>
        <dd className="font-mono text-ink">{m.reduced_tokens}</dd>
        <dt className="text-ink-soft">Estimated tokens saved</dt>
        <dd className="font-mono text-ink">{m.estimated_saved_tokens}</dd>
        <dt className="text-ink-soft">Token reduction (estimated)</dt>
        <dd className="font-mono text-ink">{pct(m.token_reduction)}</dd>
        <dt className="text-ink-soft">Cache</dt>
        <dd className="text-ink">{m.cache_hit ? "hit" : "miss"}</dd>
        <dt className="text-ink-soft">Degraded by budget</dt>
        <dd className="text-ink">{m.degraded ? "yes" : "no"}</dd>
        <dt className="text-ink-soft">Ranking time</dt>
        <dd className="font-mono text-ink">{m.ranking_time_ms}ms</dd>
        <dt className="text-ink-soft">Extraction time</dt>
        <dd className="font-mono text-ink">{m.extraction_time_ms}ms</dd>
        <dt className="text-ink-soft">Privacy scan time</dt>
        <dd className="font-mono text-ink">{m.privacy_time_ms}ms</dd>
        <dt className="text-ink-soft">Total build time</dt>
        <dd className="font-mono text-ink">{m.build_time_ms}ms</dd>
      </dl>
      <p className="mt-1 text-[9px] text-ink-soft">
        Token counts are estimated (≈4 characters per token), not exact tokenizer output.
      </p>
    </div>
  );
}

// ------------------------------------------------------------------- panel

export function ContextEngineeringPanel({
  runId,
  status,
}: {
  runId: string;
  status?: AgentStatus;
}) {
  const [pkg, setPkg] = useState<ContextPackageModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [relevanceOpen, setRelevanceOpen] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getRunContext(runId)
      .then((p) => {
        if (!cancelled) setPkg(p);
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
          Context Engineering
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">Loading context package…</p>
      </section>
    );
  }

  if (error) {
    return <ErrorBanner message={error} onRetry={() => setAttempt((n) => n + 1)} />;
  }

  if (!pkg) {
    return (
      <section className="rounded-2xl border border-border bg-surface p-4">
        <h3 className="text-[11px] font-medium uppercase tracking-wider text-ink-soft">
          Context Engineering
        </h3>
        <p className="mt-1.5 text-xs text-ink-soft">
          {status === "running"
            ? "RUNNING — A5.5 is engineering the repair context now; this panel renders once it publishes."
            : "PENDING — Context Engineering has not produced a package yet."}
        </p>
      </section>
    );
  }

  const inContextSet = inContextFiles(pkg);
  const symbolsByFile = symbolsByFileOf(pkg);
  const candidates = pkg.metrics.files_ranked;
  const extracted = pkg.metrics.files_extracted;
  // Full repository-relative paths — never truncated to a basename that
  // could name any file in the tree. `ChipRow`/`SelectedContext` truncate
  // visually with CSS and keep the full path in a tooltip.
  const selectedFileNames = selectedFiles(pkg, inContextSet);
  const selectedSymbols = selectedContextSymbols(pkg);

  return (
    <section className="space-y-2 rounded-2xl border border-border bg-surface p-4">
      {/* The hero: real files flowing repository → relevance → privacy gate →
          A7, carrying each stage's own count in its header. */}
      <ContextFlowDiagram
        pkg={pkg}
        symbolsByFile={symbolsByFile}
        candidates={candidates}
        extracted={extracted}
        selectedFileNames={selectedFileNames}
        relevanceOpen={relevanceOpen}
        onToggleRelevance={() => setRelevanceOpen((o) => !o)}
      />

      {relevanceOpen && (
        <div>
          <div className="mb-1.5 text-[10px] font-medium uppercase tracking-wider text-ink-soft">
            Context Map ({pkg.ranked_files.length} candidate
            {pkg.ranked_files.length === 1 ? "" : "s"} scored)
          </div>
          <RelevanceMap pkg={pkg} inContextSet={inContextSet} symbolsByFile={symbolsByFile} />
        </div>
      )}

      {/* What survived. */}
      <FlowConnector />
      <SelectedContext
        fileNames={selectedFileNames}
        symbols={selectedSymbols}
        lines={pkg.metrics.context_lines}
      />

      {/* Diagnostics: the totals behind the flow's per-file markers, and the
          way into the redaction ledger and the adoption reason. */}
      <FlowConnector />
      <div className="space-y-1">
        <PrivacyStage pkg={pkg} onClick={() => setPrivacyOpen((o) => !o)} expanded={privacyOpen} />
        {privacyOpen && (
          <div className="rounded border border-border bg-surface-muted/20 p-2.5">
            <RedactionLedger pkg={pkg} />
          </div>
        )}
        <AdoptionStage pkg={pkg} />
      </div>

      <CollapsibleSection
        label="Constraints carried forward"
        count={
          pkg.acceptance_criteria.length +
          pkg.patch_constraints.length +
          pkg.contracts.length +
          pkg.validation_requirements.length
        }
      >
        <div className="space-y-2">
          <div>
            <div className="text-[9px] uppercase tracking-wider text-ink-soft">
              Acceptance criteria
            </div>
            <StringList items={pkg.acceptance_criteria} empty="None recorded." />
          </div>
          <div>
            <div className="text-[9px] uppercase tracking-wider text-ink-soft">
              Patch constraints
            </div>
            <StringList items={pkg.patch_constraints} empty="None recorded." />
          </div>
          {pkg.contracts.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-wider text-ink-soft">Contracts</div>
              <StringList items={pkg.contracts} empty="None recorded." />
            </div>
          )}
          {pkg.validation_requirements.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-wider text-ink-soft">
                Validation requirements
              </div>
              <StringList items={pkg.validation_requirements} empty="None recorded." />
            </div>
          )}
          {pkg.dependency_summary.length > 0 && (
            <div>
              <div className="text-[9px] uppercase tracking-wider text-ink-soft">
                Dependency summary
              </div>
              <StringList items={pkg.dependency_summary} empty="None recorded." />
            </div>
          )}
        </div>
      </CollapsibleSection>

      <CollapsibleSection label="Token & performance details">
        <PerformanceDetails pkg={pkg} />
      </CollapsibleSection>
    </section>
  );
}
