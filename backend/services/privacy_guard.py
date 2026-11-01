"""Mask secrets in repair context before it ever reaches an LLM.

This is the first point in the pipeline where source leaving the process is
inspected. The guard is deliberately conservative about *what* it changes:
only string and bytes literal **values** are replaced. Identifiers, function
and class names, imports, control flow, indentation, and comments are left
exactly as written, so the masked source still parses and the model still sees
the shape of the code it must repair.

Detection is layered so that a secret has to evade all three to get through:

1. **Structural** — an assignment or keyword argument whose *name* looks like a
   credential (``SECRET_KEY``, ``api_key``, ``password``) and whose value is a
   literal. Name-driven, so it catches secrets in any format.
2. **Format** — values matching a known credential shape: PEM blocks, JWTs, AWS
   access keys, provider prefixes (``sk-``, ``ghp_``), URIs with inline
   credentials, emails.
3. **Entropy** — long unbroken high-entropy strings that neither of the above
   named, which is what an unlabelled random token looks like.

Placeholders are typed (``<REDACTED_SECRET>``, ``<REDACTED_TOKEN>``,
``<REDACTED_KEY>``, …) so the model can tell a masked password from a masked
certificate and reason about the code accordingly.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass, field

from backend.models.context import Redaction, RedactionKind

# -- structural detection --------------------------------------------------

# Identifier fragments that mark a value as credential-bearing, matched
# case-insensitively against the assignment target or keyword name.
#
# Split by how much the name alone proves. A *strong* marker means any non-empty
# string value is masked. A *weak* marker ("token", "key", "auth") appears
# constantly in ordinary code — `max_tokens`, `token_source`, `key_type` — so it
# additionally requires the value to look like a credential. Without that split
# the guard masks configuration constants and inflates the repair context with
# nothing gained.
_STRONG_NAME_PATTERNS: tuple[tuple[str, RedactionKind], ...] = (
    (r"pass(?:word|wd|phrase)", "REDACTED_PASSWORD"),
    (r"client[_-]?secret", "REDACTED_SECRET"),
    (r"secret", "REDACTED_SECRET"),
    (r"credential", "REDACTED_SECRET"),
    (r"api[_-]?key", "REDACTED_KEY"),
    (r"access[_-]?key", "REDACTED_KEY"),
    (r"private[_-]?key", "REDACTED_KEY"),
    (r"encryption[_-]?key", "REDACTED_KEY"),
    (r"signing[_-]?key", "REDACTED_KEY"),
    (r"ssh[_-]?key", "REDACTED_KEY"),
)

_WEAK_NAME_PATTERNS: tuple[tuple[str, RedactionKind], ...] = (
    (r"public[_-]?key", "REDACTED_KEY"),
    (r"\bkey\b", "REDACTED_KEY"),
    (r"token", "REDACTED_TOKEN"),
    (r"auth", "REDACTED_SECRET"),
    (r"salt", "REDACTED_SECRET"),
    (r"certificate", "REDACTED_CERTIFICATE"),
    (r"\bcert\b", "REDACTED_CERTIFICATE"),
)

_STRONG_RULES = tuple((re.compile(p, re.IGNORECASE), k) for p, k in _STRONG_NAME_PATTERNS)
_WEAK_RULES = tuple((re.compile(p, re.IGNORECASE), k) for p, k in _WEAK_NAME_PATTERNS)

# Minimum length before a weakly-named value is even considered.
_WEAK_MIN_LENGTH = 6


def _looks_like_a_credential(value: str) -> bool:
    """Heuristic for weakly-named values: does this look generated, not written?

    Credentials carry digits, mixed case, or separator punctuation. Prose and
    enum-style constants (`"estimated"`, `"bearer"`) carry none of those.
    """
    if len(value) < _WEAK_MIN_LENGTH or " " in value:
        return False
    has_digit = any(c.isdigit() for c in value)
    has_mixed_case = any(c.islower() for c in value) and any(c.isupper() for c in value)
    has_separator = any(c in "-_+=/." for c in value)
    return has_digit or has_mixed_case or has_separator

# Names that look credential-ish but carry no secret — masking these would
# destroy meaning the repair depends on.
_NAME_ALLOWLIST = frozenset({
    "key",  # dict iteration variable
    "keys",
    "key_type",
    "key_name",
    "key_prefix",
    "keyerror",
    "token_type",
    "token_url",
    "auth_header",
    "auth_scheme",
    "authorization_header",
    "secret_name",
    "password_field",
    "cert_path",
    "key_path",
    "algorithm",
})

# -- format detection ------------------------------------------------------

_VALUE_RULES: tuple[tuple[re.Pattern[str], RedactionKind, str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)"), "REDACTED_CERTIFICATE", "pem_block"),
    (re.compile(r"^ey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.?"), "REDACTED_TOKEN", "jwt"),
    (re.compile(r"^(?:AKIA|ASIA)[0-9A-Z]{16}$"), "REDACTED_KEY", "aws_access_key"),
    (re.compile(r"^(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}$"), "REDACTED_KEY", "provider_api_key"),
    (re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"), "REDACTED_TOKEN", "github_token"),
    (re.compile(r"^xox[baprs]-[A-Za-z0-9-]{10,}$"), "REDACTED_TOKEN", "slack_token"),
    (re.compile(r"^ssh-(?:rsa|ed25519|dss) [A-Za-z0-9+/]{32,}"), "REDACTED_KEY", "ssh_public_key"),
    (re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"), "REDACTED_SECRET", "uri_credentials"),
    (
        re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"),
        "REDACTED_EMAIL",
        "email_address",
    ),
)

# -- entropy detection -----------------------------------------------------

# Below this length a random-looking string is more likely a hash constant, an
# enum value, or a test fixture than a live credential.
_ENTROPY_MIN_LENGTH = 32
_ENTROPY_THRESHOLD = 4.0
_ENTROPY_CHARSET = re.compile(r"^[A-Za-z0-9+/=_-]+$")

PLACEHOLDER_RE = re.compile(r"<REDACTED_[A-Z]+>")


def shannon_entropy(value: str) -> float:
    """Bits of entropy per character."""
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _classify_name(name: str, value: str | None = None) -> RedactionKind | None:
    """Kind to mask a named value as, or None to leave it alone.

    `value` is required to resolve weak markers; without it only strong markers
    can be decided (used by the textual fallback for unparseable sources).
    """
    if not name or name.lower() in _NAME_ALLOWLIST:
        return None

    for pattern, kind in _STRONG_RULES:
        if pattern.search(name):
            return kind

    for pattern, kind in _WEAK_RULES:
        if pattern.search(name):
            if value is None or _looks_like_a_credential(value):
                return kind
            return None

    return None


def _classify_value(value: str) -> tuple[RedactionKind, str] | None:
    stripped = value.strip()
    if not stripped:
        return None
    for pattern, kind, detector in _VALUE_RULES:
        if pattern.search(stripped):
            return kind, detector
    if (
        len(stripped) >= _ENTROPY_MIN_LENGTH
        and " " not in stripped
        and _ENTROPY_CHARSET.match(stripped)
        and shannon_entropy(stripped) >= _ENTROPY_THRESHOLD
    ):
        return "REDACTED_SECRET", "high_entropy"
    return None


def placeholder(kind: RedactionKind) -> str:
    return f"<{kind}>"


@dataclass
class _Hit:
    """One literal to replace, located by exact source offset."""

    lineno: int
    col: int
    end_lineno: int
    end_col: int
    kind: RedactionKind
    detector: str
    identifier: str = ""


@dataclass
class GuardResult:
    source: str
    redactions: list[Redaction] = field(default_factory=list)
    status: str = "clean"
    failed_reason: str | None = None


class _LiteralScanner(ast.NodeVisitor):
    """Locate credential-bearing literals without touching anything else."""

    def __init__(self) -> None:
        self.hits: list[_Hit] = []

    # -- helpers

    def _record(self, node: ast.Constant, kind: RedactionKind, detector: str, identifier: str) -> None:
        if node.end_lineno is None or node.end_col_offset is None:
            return
        self.hits.append(
            _Hit(
                lineno=node.lineno,
                col=node.col_offset,
                end_lineno=node.end_lineno,
                end_col=node.end_col_offset,
                kind=kind,
                detector=detector,
                identifier=identifier,
            )
        )

    def _consider_named(self, name: str, value: ast.expr) -> bool:
        """Mask a literal because its *name* marks it as a credential.

        Only string and bytes literals qualify. A numeric literal under a
        credential-ish name is virtually always configuration (`max_tokens`,
        `key_length`), and masking it corrupts the code the model must repair.
        """
        if not isinstance(value, ast.Constant) or not isinstance(value.value, (str, bytes)):
            return False

        text = value.value.decode("utf-8", "replace") if isinstance(value.value, bytes) else value.value
        # An empty string is not a secret; masking it would hide that no value is
        # configured, which may be the defect under repair.
        if not text.strip():
            return False

        kind = _classify_name(name, text)
        if kind is None:
            return False

        self._record(value, kind, "named_assignment", name)
        return True

    # -- visitors

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            name = _target_name(target)
            if name and self._consider_named(name, node.value):
                break
        else:
            self.generic_visit(node)
            return
        # Named match handled; still descend for nested literals.
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        name = _target_name(node.target)
        if name and node.value is not None:
            self._consider_named(name, node.value)
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:
        if node.arg:
            self._consider_named(node.arg, node.value)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self._consider_named(key.value, value)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            classified = _classify_value(node.value)
            if classified:
                kind, detector = classified
                self._record(node, kind, detector, "")
        self.generic_visit(node)


def _target_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _apply_hits(source: str, hits: list[_Hit]) -> tuple[str, list[_Hit]]:
    """Replace each located literal with its placeholder, preserving layout."""
    if not hits:
        return source, []

    lines = source.splitlines(keepends=True)

    # Deduplicate by position, then rewrite last-first so earlier offsets stay valid.
    unique: dict[tuple[int, int, int, int], _Hit] = {}
    for hit in hits:
        unique.setdefault((hit.lineno, hit.col, hit.end_lineno, hit.end_col), hit)
    ordered = sorted(unique.values(), key=lambda h: (h.lineno, h.col), reverse=True)

    applied: list[_Hit] = []
    for hit in ordered:
        start_idx = hit.lineno - 1
        end_idx = hit.end_lineno - 1
        if start_idx < 0 or end_idx >= len(lines):
            continue
        replacement = f'"{placeholder(hit.kind)}"'
        if start_idx == end_idx:
            line = lines[start_idx]
            lines[start_idx] = line[: hit.col] + replacement + line[hit.end_col :]
        else:
            head = lines[start_idx][: hit.col]
            tail = lines[end_idx][hit.end_col :]
            lines[start_idx : end_idx + 1] = [head + replacement + tail]
        applied.append(hit)

    return "".join(lines), applied


def scan_source(source: str, file: str = "") -> GuardResult:
    """Mask credential literals in Python source.

    Falls back to a line-level scan when the source does not parse, so a
    syntactically broken file — exactly the kind a repair run handles — is never
    forwarded unmasked.
    """
    if not source:
        return GuardResult(source=source)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _scan_unparseable(source, file)

    scanner = _LiteralScanner()
    scanner.visit(tree)
    masked, applied = _apply_hits(source, scanner.hits)

    redactions = [
        Redaction(
            file=file,
            line=hit.lineno,
            kind=hit.kind,
            detector=hit.detector,
            identifier=hit.identifier,
        )
        for hit in sorted(applied, key=lambda h: (h.lineno, h.col))
    ]
    return GuardResult(
        source=masked,
        redactions=redactions,
        status="masked" if redactions else "clean",
    )


_QUOTED_RE = re.compile(r"(['\"])((?:(?!\1).){4,})\1")


def _scan_unparseable(source: str, file: str) -> GuardResult:
    """Line-oriented fallback for source the AST cannot read."""
    redactions: list[Redaction] = []
    out_lines: list[str] = []

    for lineno, line in enumerate(source.splitlines(keepends=True), start=1):
        name_match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_.]*)\s*[:=]", line)

        def replace(match: re.Match[str]) -> str:
            value = match.group(2)
            classified = _classify_value(value)
            name_kind = _classify_name(name_match.group(1), value) if name_match else None
            if classified:
                kind, detector = classified
            elif name_kind:
                kind, detector = name_kind, "named_assignment_textual"
            else:
                return match.group(0)
            redactions.append(
                Redaction(
                    file=file,
                    line=lineno,
                    kind=kind,
                    detector=detector,
                    identifier=name_match.group(1) if name_match else "",
                )
            )
            return f'{match.group(1)}{placeholder(kind)}{match.group(1)}'

        out_lines.append(_QUOTED_RE.sub(replace, line))

    return GuardResult(
        source="".join(out_lines),
        redactions=redactions,
        status="masked" if redactions else "clean",
    )


def scan_text(text: str, file: str = "") -> GuardResult:
    """Mask credentials in free text — tracebacks, claims, evidence strings."""
    if not text:
        return GuardResult(source=text)

    redactions: list[Redaction] = []
    result = text

    for pattern, kind, detector in _VALUE_RULES:
        # Emails inside prose are the common case; the others are anchored
        # patterns that need a word-boundary search rather than a full match.
        search = (r"(?<![\w./\\@+-])[A-Za-z0-9.!#$%&'*+=?^_`{|}~-]+"
                  r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
                  r"(?![\w/\\@-])" if detector == "email_address"
                  else pattern.pattern.lstrip("^").rstrip("$"))
        for match in reversed(list(re.finditer(search, result))):
            value = match.group(0)
            if PLACEHOLDER_RE.search(value):
                continue
            redactions.append(
                Redaction(file=file, line=0, kind=kind, detector=detector)
            )
            result = result[:match.start()] + placeholder(kind) + result[match.end():]

    return GuardResult(
        source=result,
        redactions=redactions,
        status="masked" if redactions else "clean",
    )
