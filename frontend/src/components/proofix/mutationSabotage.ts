/**
 * Pure helpers for the Mutation Validation sabotage board — no React, no
 * network. Everything here is a mechanical function of the real `before`/
 * `after` strings A8 fetched from `mutmut show`. Nothing here knows anything
 * about what the patched repository actually does: `classifyMutation`
 * recognizes mutmut's own operator-swap vocabulary (comparisons, booleans,
 * arithmetic, constants) — a fact about mutmut, not a guess about this
 * repo's domain. That is a deliberate line: it is the difference between
 * "this changed a comparison operator" (true of any Python file) and "this
 * breaks JWT expiry checking" (true of exactly one fixture, and false, and
 * misleading, everywhere else).
 */

export interface MutationClassification {
  /** Short token-level summary, e.g. "< → <=". */
  operator: string;
  /** Human label for the operator family. */
  label: string;
  /** Generic, mechanically-templated suggestion — never domain-specific. */
  suggestion: string;
}

const OPERATOR_FAMILIES: { pairs: [string, string][]; label: string; suggestion: string }[] = [
  {
    pairs: [
      ["<", "<="],
      ["<=", "<"],
      [">", ">="],
      [">=", ">"],
      ["==", "!="],
      ["!=", "=="],
      ["<", ">"],
      [">", "<"],
    ],
    label: "comparison operator",
    suggestion:
      "Add a test where the two sides of the comparison are exactly equal — that is the boundary this mutation crosses.",
  },
  {
    pairs: [
      ["and", "or"],
      ["or", "and"],
    ],
    label: "boolean operator",
    suggestion: "Add a test where the two operands of this condition disagree with each other.",
  },
  {
    pairs: [
      ["+", "-"],
      ["-", "+"],
      ["*", "/"],
      ["/", "*"],
      ["//", "/"],
      ["**", "*"],
    ],
    label: "arithmetic operator",
    suggestion: "Add a test that asserts the exact computed value, not just its sign or range.",
  },
  {
    pairs: [
      ["True", "False"],
      ["False", "True"],
    ],
    label: "boolean literal",
    suggestion: "Add a test that exercises both branches this literal controls.",
  },
];

// Multi-character operators must be tried before the single-char fallback,
// or `<=` tokenizes as `<` then `=` and the diff below sees a one-character
// edit instead of the operator swap it actually is.
const DIFF_TOKEN_RE = /(\s+|<=|>=|==|!=|\*\*|\/\/|[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*|.)/g;

function tokenize(text: string): string[] {
  return text.match(DIFF_TOKEN_RE) ?? [];
}

/** Token-level diff (not character-level) so `<` → `<=` reads as one operator
 * edit, not a stray `=` insertion. */
function diffSegment(before: string, after: string): { removed: string; added: string } {
  const b = tokenize(before);
  const a = tokenize(after);
  let start = 0;
  const maxStart = Math.min(b.length, a.length);
  while (start < maxStart && b[start] === a[start]) start++;
  let endB = b.length;
  let endA = a.length;
  while (endB > start && endA > start && b[endB - 1] === a[endA - 1]) {
    endB--;
    endA--;
  }
  return {
    removed: b.slice(start, endB).join("").trim(),
    added: a.slice(start, endA).join("").trim(),
  };
}

/** `null` only when `before`/`after` are identical — never a fabricated guess. */
export function classifyMutation(before: string, after: string): MutationClassification | null {
  const { removed, added } = diffSegment(before, after);
  if (!removed && !added) return null;

  for (const family of OPERATOR_FAMILIES) {
    if (family.pairs.some(([a, b]) => a === removed && b === added)) {
      return {
        operator: `${removed || "∅"} → ${added || "∅"}`,
        label: family.label,
        suggestion: family.suggestion,
      };
    }
  }

  if (/^-?\d+(\.\d+)?$/.test(removed) && /^-?\d+(\.\d+)?$/.test(added)) {
    return {
      operator: `${removed} → ${added}`,
      label: "numeric constant",
      suggestion: "Add a test that pins this exact value rather than a loose range.",
    };
  }

  return {
    operator: `${removed || "∅"} → ${added || "∅"}`,
    label: "code changed",
    suggestion: "Add a test that distinguishes the original behavior from this mutated one.",
  };
}

// -------------------------------------------------------------- syntax paint

export type TokenKind = "keyword" | "string" | "comment" | "number" | "text";

export interface Token {
  kind: TokenKind;
  text: string;
}

const KEYWORDS = new Set([
  "def",
  "class",
  "if",
  "elif",
  "else",
  "return",
  "raise",
  "import",
  "from",
  "for",
  "while",
  "in",
  "not",
  "and",
  "or",
  "is",
  "True",
  "False",
  "None",
  "try",
  "except",
  "finally",
  "with",
  "as",
  "lambda",
  "yield",
  "async",
  "await",
  "pass",
  "break",
  "continue",
  "global",
  "nonlocal",
  "assert",
  "del",
  "self",
]);

const TOKEN_RE =
  /("""[\s\S]*?"""|'''[\s\S]*?'''|"[^"\n]*"|'[^'\n]*'|#.*$|\b\d+\.?\d*\b|\b[A-Za-z_][A-Za-z0-9_]*\b)/g;

/** Minimal, dependency-free Python tokenizer for one source line. */
export function highlightPythonLine(line: string): Token[] {
  const tokens: Token[] = [];
  let lastIndex = 0;
  for (const match of line.matchAll(TOKEN_RE)) {
    const text = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) tokens.push({ kind: "text", text: line.slice(lastIndex, index) });
    if (text.startsWith("#")) tokens.push({ kind: "comment", text });
    else if (text.startsWith('"') || text.startsWith("'")) tokens.push({ kind: "string", text });
    else if (/^\d/.test(text)) tokens.push({ kind: "number", text });
    else if (KEYWORDS.has(text)) tokens.push({ kind: "keyword", text });
    else tokens.push({ kind: "text", text });
    lastIndex = index + text.length;
  }
  if (lastIndex < line.length) tokens.push({ kind: "text", text: line.slice(lastIndex) });
  return tokens;
}
