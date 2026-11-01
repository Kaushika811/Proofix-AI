/**
 * Unified-diff renderer.
 *
 * The diff is the product — everything upstream exists to produce it — and it
 * was reachable only as two filenames on the patch card. `/runs/{id}/patch`
 * has served both sides of every file all along.
 *
 * Deliberately no syntax highlighter. `shiki` was removed from this project
 * once already; re-adding ~2 MB to colour keywords would buy less than the
 * add/remove colouring below, which is the distinction a reviewer actually
 * reads. Line classification is the whole parser, and it is a switch on the
 * first character because that *is* the unified-diff format.
 */

import { useMemo, useState } from "react";

export type DiffLineKind = "add" | "del" | "hunk" | "file" | "meta" | "context";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  /** 1-based line number in the original file; null on added lines. */
  oldNumber: number | null;
  /** 1-based line number in the patched file; null on removed lines. */
  newNumber: number | null;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/**
 * Parse a unified diff into displayable lines with real line numbers.
 *
 * Numbers come from the hunk headers and advance per side, so they are the
 * file's own numbering rather than an index into the diff. A reviewer looking
 * up `auth.py:27` needs the former; the latter would send them to the wrong
 * place in any file with more than one hunk.
 */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  if (!diff) return [];
  const out: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;

  for (const raw of diff.split("\n")) {
    // Headers first: `+++`/`---` start with the same characters as added and
    // removed lines, so testing for them second would classify every file
    // header as a change.
    if (raw.startsWith("+++") || raw.startsWith("---")) {
      out.push({ kind: "file", text: raw, oldNumber: null, newNumber: null });
      continue;
    }
    const hunk = HUNK.exec(raw);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      out.push({ kind: "hunk", text: raw, oldNumber: null, newNumber: null });
      continue;
    }
    if (raw.startsWith("+")) {
      out.push({ kind: "add", text: raw.slice(1), oldNumber: null, newNumber: newNo++ });
      continue;
    }
    if (raw.startsWith("-")) {
      out.push({ kind: "del", text: raw.slice(1), oldNumber: oldNo++, newNumber: null });
      continue;
    }
    if (raw.startsWith("\\")) {
      // "\ No newline at end of file" — real diff output, not a content line.
      out.push({ kind: "meta", text: raw, oldNumber: null, newNumber: null });
      continue;
    }
    out.push({ kind: "context", text: raw.slice(1), oldNumber: oldNo++, newNumber: newNo++ });
  }

  // A trailing newline in the diff yields one empty context line that is not
  // part of the file.
  const last = out[out.length - 1];
  if (last && last.kind === "context" && last.text === "") out.pop();

  return out;
}

/** Added and removed line counts, for the summary line above the diff. */
export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  return {
    added: lines.filter((l) => l.kind === "add").length,
    removed: lines.filter((l) => l.kind === "del").length,
  };
}

const FILE_START = /^--- a\/(.+)$/;

/**
 * Split a multi-file unified diff back into its per-file chunks.
 *
 * `PatchBundle.diff_text` is several `unified_diff(...)` outputs concatenated
 * (`generate_diff_from_patches`), one per patched file, each starting with its
 * own `--- a/<file>` header. This partitions the real backend text along
 * those boundaries — it does not re-diff anything — so a multi-file patch can
 * be shown one file at a time without inventing a diff algorithm client-side.
 */
export function splitDiffByFile(diff: string): Map<string, string> {
  const chunks = new Map<string, string>();
  if (!diff) return chunks;

  let currentFile: string | null = null;
  let buffer: string[] = [];
  const flush = () => {
    if (currentFile !== null) chunks.set(currentFile, buffer.join("\n"));
  };

  for (const line of diff.split("\n")) {
    const start = FILE_START.exec(line);
    if (start) {
      flush();
      currentFile = start[1];
      buffer = [line];
      continue;
    }
    if (currentFile !== null) buffer.push(line);
  }
  flush();

  return chunks;
}

/**
 * A code-editor diff palette — true black canvas (`#000000`, not a
 * dark-navy approximation) with VS Code Dark+ token colours — fixed
 * regardless of the host page's light/dark theme, the same way a rendered
 * code diff in a coding assistant or editor does not repaint itself when
 * the surrounding chrome does.
 */
const TERMINAL_BG = "#000000";

const ROW_CLASS: Record<DiffLineKind, string> = {
  add: "bg-[#1a3a24] text-[#d4d4d4]",
  del: "bg-[#3a1f22] text-[#d4d4d4]",
  hunk: "text-[#6e7681]",
  file: "text-[#6e7681]",
  meta: "text-[#6e7681]",
  context: "text-[#d4d4d4]",
};

const MARKER_CLASS: Record<DiffLineKind, string> = {
  add: "text-[#4ec97c]",
  del: "text-[#f16d6d]",
  hunk: "text-[#6e7681]",
  file: "text-[#6e7681]",
  meta: "text-[#6e7681]",
  context: "text-[#6e7681]",
};

const MARKER: Record<DiffLineKind, string> = {
  add: "+",
  del: "-",
  hunk: "",
  file: "",
  meta: "",
  context: " ",
};

/**
 * Minimal single-pass token colouring for the content column — not a real
 * parser, and deliberately not `shiki` (removed from this project once
 * already for its ~2 MB cost). Comments, strings, numbers and a fixed
 * keyword set cover Python and JS/TS well enough to read like a syntax-
 * highlighted editor; anything else stays the default foreground rather
 * than being mis-tokenized.
 */
const KEYWORDS = new Set([
  "def",
  "return",
  "if",
  "elif",
  "else",
  "for",
  "while",
  "import",
  "from",
  "as",
  "class",
  "try",
  "except",
  "finally",
  "with",
  "raise",
  "pass",
  "break",
  "continue",
  "in",
  "is",
  "not",
  "and",
  "or",
  "None",
  "True",
  "False",
  "self",
  "async",
  "await",
  "yield",
  "lambda",
  "global",
  "nonlocal",
  "assert",
  "del",
  "const",
  "let",
  "var",
  "function",
  "new",
  "export",
  "default",
  "interface",
  "type",
  "extends",
  "implements",
  "public",
  "private",
  "static",
  "void",
  "this",
  "typeof",
  "instanceof",
  "null",
  "undefined",
  "true",
  "false",
  "case",
  "switch",
  "throw",
]);

const TOKEN_RE =
  /(#.*$|\/\/.*$)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_]\w*\b)(?=\s*\()|(\b[A-Za-z_]\w*\b)/g;

const TOKEN_COLOR: Record<string, string> = {
  comment: "#6a9955",
  string: "#ce9178",
  number: "#b5cea8",
  func: "#dcdcaa",
  keyword: "#ff7ab2",
};

function highlight(text: string): { text: string; cls: string | null }[] {
  const out: { text: string; cls: string | null }[] = [];
  let last = 0;
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN_RE.exec(text))) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), cls: null });
    if (m[1] !== undefined) out.push({ text: m[1], cls: "comment" });
    else if (m[2] !== undefined) out.push({ text: m[2], cls: "string" });
    else if (m[3] !== undefined) out.push({ text: m[3], cls: "number" });
    else if (m[4] !== undefined) out.push({ text: m[4], cls: "func" });
    else if (m[5] !== undefined)
      out.push({ text: m[5], cls: KEYWORDS.has(m[5]) ? "keyword" : null });
    last = TOKEN_RE.lastIndex;
  }
  if (last < text.length) out.push({ text: text.slice(last), cls: null });
  return out;
}

const HIGHLIGHTABLE: ReadonlySet<DiffLineKind> = new Set(["add", "del", "context"]);

function CodeContent({ line }: { line: DiffLine }) {
  if (!HIGHLIGHTABLE.has(line.kind)) return <>{line.text}</>;
  return (
    <>
      {highlight(line.text).map((tok, i) =>
        tok.cls ? (
          <span key={i} style={{ color: TOKEN_COLOR[tok.cls] }}>
            {tok.text}
          </span>
        ) : (
          <span key={i}>{tok.text}</span>
        ),
      )}
    </>
  );
}

/** Rows shown before a large diff folds, and how many trailing rows stay
 * visible below the fold — the same head/tail shape a terminal session uses
 * so the file's closing lines are never hidden behind a click. */
const COLLAPSE_THRESHOLD = 64;
const HEAD_ROWS = 36;
const TAIL_ROWS = 12;

export function DiffView({ diff }: { diff: string }) {
  const lines = parseUnifiedDiff(diff);
  const [expanded, setExpanded] = useState(false);

  // Every file's diff starts collapsed on first mount, even a short one —
  // this state is keyed by the component instance (one per active file in
  // `PatchPanel`), so switching files resets it rather than carrying an
  // earlier file's "expanded" choice onto an unrelated diff.
  const folded = !expanded && lines.length > COLLAPSE_THRESHOLD;
  const visible = useMemo(() => {
    if (!folded) return lines;
    return [...lines.slice(0, HEAD_ROWS), ...lines.slice(lines.length - TAIL_ROWS)];
  }, [lines, folded]);
  const hiddenCount = lines.length - HEAD_ROWS - TAIL_ROWS;

  if (lines.length === 0) {
    return <p className="text-[11px] text-ink-soft">The patch bundle carries no diff.</p>;
  }

  const row = (line: DiffLine, key: number) => (
    <tr key={key} className={ROW_CLASS[line.kind]}>
      <td className="w-10 select-none px-1.5 text-right align-top text-[10px] text-[#6e7681]">
        {line.oldNumber ?? ""}
      </td>
      <td className="w-10 select-none px-1.5 text-right align-top text-[10px] text-[#6e7681]">
        {line.newNumber ?? ""}
      </td>
      <td
        className={`w-4 select-none px-1 text-center align-top font-semibold ${MARKER_CLASS[line.kind]}`}
      >
        {MARKER[line.kind]}
      </td>
      <td className="whitespace-pre px-2 align-top">
        <CodeContent line={line} />
      </td>
    </tr>
  );

  return (
    // Wide code scrolls inside its own box; the page never scrolls sideways.
    // A large repo's edit can run to hundreds of changed lines — the fold
    // below keeps that render bounded to a fixed row count instead of the
    // panel growing without limit, while a small repo's short diff never
    // triggers it at all.
    <div className="overflow-x-auto rounded-lg" style={{ backgroundColor: TERMINAL_BG }}>
      <table className="w-full border-collapse font-mono text-[12px] leading-[1.6]">
        <tbody>
          {folded ? (
            <>
              {visible.slice(0, HEAD_ROWS).map((line, i) => row(line, i))}
              <tr>
                <td colSpan={4} className="px-2 py-1">
                  <button
                    type="button"
                    onClick={() => setExpanded(true)}
                    className="font-mono text-[11px] text-[#7d8590] transition-colors hover:text-[#e6edf3]"
                  >
                    … +{hiddenCount} line{hiddenCount === 1 ? "" : "s"} (click to expand)
                  </button>
                </td>
              </tr>
              {visible.slice(HEAD_ROWS).map((line, i) => row(line, HEAD_ROWS + i))}
            </>
          ) : (
            lines.map((line, i) => row(line, i))
          )}
        </tbody>
      </table>
    </div>
  );
}
