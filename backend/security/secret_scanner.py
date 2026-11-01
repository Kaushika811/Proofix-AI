"""Credential detection across the full enterprise category list.

Builds on `services/privacy_guard`, which already masks credential literals in
Python source using layered AST, format and entropy detection. That module stays
the engine for *source*; this one adds the categories an enterprise review asks
for by name — cloud credentials, connection strings, database credentials, OAuth
secrets, SSH private keys — and scans *any* text, not only parseable Python.

The two are deliberately not merged. `services/privacy_guard` is structure-aware
and must keep the code it masks executable; this scanner is format-aware and runs
over prompts, tracebacks, evidence strings and documentation, where there is no
structure to preserve.

**Findings never carry the secret.** A finding records category, detector and
location. The value is replaced in the returned text and discarded. A security
layer that logs what it redacts has moved the leak, not closed it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.models.security import SecretFinding, Severity
from backend.services.privacy_guard import shannon_entropy

# Placeholder vocabulary. Typed so the model can tell a masked password from a
# masked certificate and still reason about the code.
PLACEHOLDERS = {
    "aws_secret_access_key": "<REDACTED_AWS_SECRET>",
    "aws_access_key_id": "<REDACTED_AWS_KEY_ID>",
    "aws_session_token": "<REDACTED_AWS_SESSION_TOKEN>",
    "gcp_service_account": "<REDACTED_GCP_CREDENTIAL>",
    "azure_credential": "<REDACTED_AZURE_CREDENTIAL>",
    "private_key": "<REDACTED_PRIVATE_KEY>",
    "ssh_private_key": "<REDACTED_SSH_KEY>",
    "certificate": "<REDACTED_CERTIFICATE>",
    "jwt": "<REDACTED_JWT>",
    "jwt_secret": "<REDACTED_JWT_SECRET>",
    "oauth_secret": "<REDACTED_OAUTH_SECRET>",
    "api_key": "<REDACTED_API_KEY>",
    "github_token": "<REDACTED_GITHUB_TOKEN>",
    "slack_token": "<REDACTED_SLACK_TOKEN>",
    "stripe_key": "<REDACTED_STRIPE_KEY>",
    "npm_token": "<REDACTED_NPM_TOKEN>",
    "connection_string": "<REDACTED_CONNECTION_STRING>",
    "database_credential": "<REDACTED_DB_CREDENTIAL>",
    "password": "<REDACTED_PASSWORD>",
    "bearer_token": "<REDACTED_BEARER_TOKEN>",
    "generic_secret": "<REDACTED_SECRET>",
    "high_entropy": "<REDACTED_SECRET>",
}


@dataclass(frozen=True)
class SecretRule:
    """One detector: a pattern, the category it proves, and how bad it is."""

    detector: str
    category: str
    pattern: re.Pattern[str]
    severity: Severity = "high"
    # Which capture group holds the value to replace. 0 replaces the whole match,
    # which is wrong when the pattern anchors on a surrounding assignment.
    value_group: int = 0


def _rule(detector, category, pattern, severity="high", flags=0, value_group=0) -> SecretRule:
    return SecretRule(detector, category, re.compile(pattern, flags), severity, value_group)


# Assignment-shaped detectors. The value group excludes the name and operator so
# `AWS_SECRET_ACCESS_KEY = "..."` keeps its left-hand side and stays readable.
#
# The charset is "anything that is not a delimiter" rather than an allowlist of
# safe characters: real passwords contain punctuation (`P@ssw0rd!`), and an
# alphanumeric-only pattern silently misses exactly the strong ones.
_ASSIGNMENT = r"""(?P<value>[^\s'"();,]{8,})"""

RULES: tuple[SecretRule, ...] = (
    # -- cloud credentials
    _rule(
        "aws_secret_access_key",
        "aws_secret_access_key",
        rf"(?i)\baws[_-]?secret[_-]?access[_-]?key\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "critical",
        value_group=1,
    ),
    _rule("aws_access_key_id", "aws_access_key_id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "critical"),
    _rule(
        "aws_session_token",
        "aws_session_token",
        rf"(?i)\baws[_-]?session[_-]?token\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "critical",
        value_group=1,
    ),
    _rule(
        "gcp_service_account",
        "gcp_service_account",
        r'"type"\s*:\s*"service_account"',
        "critical",
    ),
    _rule("gcp_api_key", "api_key", r"\bAIza[0-9A-Za-z_-]{35}\b", "critical"),
    _rule(
        "azure_credential",
        "azure_credential",
        r"(?i)\bDefaultEndpointsProtocol=https?;AccountName=[^;]+;AccountKey=[^;\s]+",
        "critical",
    ),
    # -- keys and certificates
    _rule(
        "ssh_private_key",
        "ssh_private_key",
        r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |ED25519 )?PRIVATE KEY-----[\s\S]*?-----END [^-]*-----",
        "critical",
    ),
    _rule(
        "pem_private_key",
        "private_key",
        r"-----BEGIN (?:PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----[\s\S]*?-----END [^-]*-----",
        "critical",
    ),
    _rule(
        "certificate",
        "certificate",
        r"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----",
        "medium",
    ),
    # -- tokens
    _rule("jwt", "jwt", r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b", "high"),
    _rule(
        "jwt_secret",
        "jwt_secret",
        rf"(?i)\bjwt[_-]?(?:secret|signing[_-]?key)\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "critical",
        value_group=1,
    ),
    _rule("github_token", "github_token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "critical"),
    _rule("slack_token", "slack_token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "high"),
    _rule("stripe_key", "stripe_key", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b", "critical"),
    _rule("openai_key", "api_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b", "critical"),
    _rule("npm_token", "npm_token", r"\bnpm_[A-Za-z0-9]{30,}\b", "high"),
    _rule(
        "bearer_token",
        "bearer_token",
        r"(?i)\bauthorization\b\s*[:=]\s*['\"]?Bearer\s+(?P<value>[A-Za-z0-9._~+/-]{16,}=*)",
        "high",
        value_group=1,
    ),
    # -- OAuth
    _rule(
        "oauth_client_secret",
        "oauth_secret",
        rf"(?i)\b(?:client[_-]?secret|oauth[_-]?secret)\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "critical",
        value_group=1,
    ),
    # -- connection strings and database credentials
    _rule(
        "uri_credentials",
        "connection_string",
        r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s/@]+@[^\s/]+",
        "critical",
    ),
    _rule(
        "odbc_connection_string",
        "connection_string",
        # `(?:[^;]*;)*?` so the password may sit any number of segments after
        # the server — `Server=x;Database=y;Password=z` is the common form.
        r"(?i)\b(?:Server|Data Source)=[^;]+;(?:[^;]*;)*?[^;]*(?:Password|Pwd)=[^;\s]+",
        "critical",
    ),
    _rule(
        "database_password",
        "database_credential",
        rf"(?i)\b(?:db|database|postgres|mysql|mongo|redis)[_-]?(?:password|passwd|pwd)\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "critical",
        value_group=1,
    ),
    # -- generic named credentials
    _rule(
        "password_assignment",
        "password",
        rf"(?i)\b(?:password|passwd|passphrase)\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "critical",
        value_group=1,
    ),
    _rule(
        "api_key_assignment",
        "api_key",
        rf"(?i)\b(?:api[_-]?key|apikey|access[_-]?key)\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "critical",
        value_group=1,
    ),
    _rule(
        "generic_secret_assignment",
        "generic_secret",
        rf"(?i)\b(?:secret|credential)s?\b\s*[:=]\s*['\"]?{_ASSIGNMENT}['\"]?",
        "high",
        value_group=1,
    ),
)

# Values that match a credential shape but carry nothing. Masking these adds
# noise to the repair context and teaches reviewers to ignore redactions.
_PLACEHOLDER_VALUES = frozenset({
    "none", "null", "true", "false", "changeme", "password", "secret",
    "your_api_key", "xxx", "todo", "example", "placeholder", "redacted",
    "dummy", "test", "fake", "sample", "<redacted>", "os.environ", "getenv",
})

_ALREADY_REDACTED = re.compile(r"<REDACTED_[A-Z_]+>")

# Entropy sweep for unlabelled random tokens no named rule caught.
ENTROPY_MIN_LENGTH = 32
ENTROPY_THRESHOLD = 4.2
_ENTROPY_TOKEN = re.compile(r"\b[A-Za-z0-9+/=_-]{32,}\b")


def _is_placeholder(value: str) -> bool:
    raw = value.strip().strip("'\"")
    # Placeholder detection runs against the original case: `<REDACTED_...>` is
    # uppercase by construction, and lowercasing first would make an already
    # sanitized value look like a fresh secret. That inflates counts and — worse
    # — makes the firewall reject a prompt it has already cleaned.
    if _ALREADY_REDACTED.search(raw):
        return True

    stripped = raw.lower()
    if not stripped or stripped in _PLACEHOLDER_VALUES:
        return True
    # Environment lookups are references, not values.
    return stripped.startswith(("os.environ", "os.getenv", "process.env", "${", "$("))


@dataclass
class ScanResult:
    text: str
    findings: list[SecretFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings

    def categories(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.category] = counts.get(finding.category, 0) + 1
        return dict(sorted(counts.items()))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(text: str, file: str = "", include_entropy: bool = True) -> ScanResult:
    """Detect and redact credentials in arbitrary text.

    Rules are applied in declaration order, most specific first, so a value that
    matches both `aws_secret_access_key` and the generic `secret` assignment is
    reported under the precise category.
    """
    if not text:
        return ScanResult(text=text)

    findings: list[SecretFinding] = []
    result = text

    for rule in RULES:
        # Collect every match against the current text, then rewrite them
        # right-to-left so earlier offsets stay valid. Rewriting left-to-right
        # (or re-searching after each edit) either invalidates offsets or loops
        # forever when a replacement still matches the pattern.
        spans: list[tuple[int, int, int]] = []
        for match in rule.pattern.finditer(result):
            group = rule.value_group if rule.value_group and rule.value_group <= match.re.groups else 0
            value = match.group(group)
            if not value or _is_placeholder(value):
                continue
            spans.append((match.start(group), match.end(group), _line_of(result, match.start(group))))

        placeholder = PLACEHOLDERS.get(rule.category, "<REDACTED_SECRET>")
        for start, end, line in reversed(spans):
            findings.append(
                SecretFinding(
                    category=rule.category,
                    detector=rule.detector,
                    file=file,
                    line=line,
                    severity=rule.severity,
                    redacted_as=placeholder,
                )
            )
            result = result[:start] + placeholder + result[end:]

    if include_entropy:
        result, entropy_findings = _entropy_sweep(result, file)
        findings.extend(entropy_findings)

    findings.sort(key=lambda f: (f.line, f.category, f.detector))
    return ScanResult(text=result, findings=findings)


def _entropy_sweep(text: str, file: str) -> tuple[str, list[SecretFinding]]:
    """Catch unlabelled random tokens that no named rule matched."""
    findings: list[SecretFinding] = []
    result = text

    for match in list(_ENTROPY_TOKEN.finditer(text)):
        value = match.group(0)
        if _ALREADY_REDACTED.search(value) or _is_placeholder(value):
            continue
        if shannon_entropy(value) < ENTROPY_THRESHOLD:
            continue
        # A hex digest is usually a checksum, not a credential.
        if re.fullmatch(r"[0-9a-f]{32,}", value, re.IGNORECASE):
            continue

        findings.append(
            SecretFinding(
                category="high_entropy",
                detector="entropy",
                file=file,
                line=_line_of(text, match.start()),
                severity="medium",
                redacted_as=PLACEHOLDERS["high_entropy"],
            )
        )
        result = result.replace(value, PLACEHOLDERS["high_entropy"])

    return result, findings


def scan_lines(text: str, file: str = "") -> ScanResult:
    """Line-oriented scan, preserving line count exactly.

    Used where offsets matter to the caller — extracted source spans whose line
    numbers are cited elsewhere in the context package.
    """
    if not text:
        return ScanResult(text=text)

    findings: list[SecretFinding] = []
    out: list[str] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        scanned = scan_text(line, file=file)
        for finding in scanned.findings:
            finding.line = lineno
            findings.append(finding)
        out.append(scanned.text)

    trailing = "\n" if text.endswith("\n") else ""
    return ScanResult(text="\n".join(out) + trailing, findings=findings)


def contains_secret(text: str) -> bool:
    """True when any detector fires. Used by the firewall as a final check."""
    return not scan_text(text).clean


def category_counts(findings: list[SecretFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1
    return dict(sorted(counts.items()))
