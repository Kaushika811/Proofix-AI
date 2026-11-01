"""Personal-identifier detection for text leaving the process.

Format-driven and deliberately conservative. Every detector matches a *shape*
that is unambiguous in a source repository — an RFC-5322 address, a Luhn-valid
card number, an SSN in canonical form — rather than guessing at meaning.

**Name detection is the hard one, and it is scoped, not general.** There is no
deterministic way to tell a person's name from an identifier in arbitrary text,
and a detector that tried would redact `Session`, `Parser` and every capitalised
word in a docstring, destroying the context a repair depends on. So names are
detected only where a repository actually states one:

* git author strings — `Ada Lovelace <ada@example.com>`
* `@author` / `Copyright (c)` annotations
* an explicit configured roster (`settings.pii_known_names`)

Anything else is left alone and reported as out of scope. That is an honest
limitation, stated here rather than implied by silence: this detector reduces
PII exposure substantially, and does not eliminate the possibility of a name
appearing in free-form prose. For AIR_GAPPED work the policy engine forbids
external egress entirely, which is the control that actually closes that gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.models.security import PIIFinding, Severity

PLACEHOLDERS = {
    "email": "<REDACTED_EMAIL>",
    "phone": "<REDACTED_PHONE>",
    "ssn": "<REDACTED_SSN>",
    "credit_card": "<REDACTED_CARD>",
    "iban": "<REDACTED_IBAN>",
    "address": "<REDACTED_ADDRESS>",
    "person_name": "<REDACTED_NAME>",
    "employee_id": "<REDACTED_EMPLOYEE_ID>",
    "customer_id": "<REDACTED_CUSTOMER_ID>",
    "medical_id": "<REDACTED_MEDICAL_ID>",
    "financial_id": "<REDACTED_FINANCIAL_ID>",
    "internal_username": "<REDACTED_USERNAME>",
    "internal_hostname": "<REDACTED_HOSTNAME>",
    "ip_address": "<REDACTED_IP>",
}


@dataclass(frozen=True)
class PIIRule:
    detector: str
    category: str
    pattern: re.Pattern[str]
    severity: Severity = "medium"
    value_group: int = 0
    # Optional extra validation — Luhn for cards, range checks for SSNs.
    validator: str = ""


def _rule(detector, category, pattern, severity="medium", flags=0, value_group=0, validator="") -> PIIRule:
    return PIIRule(detector, category, re.compile(pattern, flags), severity, value_group, validator)


# Order matters. Rules whose match depends on *surrounding context* run first:
# `Ada Lovelace <ada@corp.com>` is only recognisable as a name while the email
# is still there to anchor it. Redacting the email first destroys that evidence
# and the name survives — the exact failure this ordering prevents.
RULES: tuple[PIIRule, ...] = (
    # -- contextual: attributed names, needing their anchors intact
    _rule(
        "git_author",
        "person_name",
        r"(?P<value>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*<[^>]+@[^>]+>",
        "high",
        value_group=1,
    ),
    _rule(
        "author_annotation",
        "person_name",
        r"(?i)(?:@author|author\s*[:=]|written\s+by)\s*['\"]?(?P<value>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        "high",
        value_group=1,
    ),
    _rule(
        "copyright_holder",
        "person_name",
        r"(?i)copyright\s*(?:\(c\)|©)?\s*\d{0,4}\s*,?\s*(?P<value>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        "medium",
        value_group=1,
    ),
    # -- direct identifiers, self-contained
    _rule(
        "email_address",
        "email",
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "high",
    ),
    _rule(
        "us_ssn",
        "ssn",
        r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
        "critical",
    ),
    _rule(
        "credit_card",
        "credit_card",
        r"\b(?:\d[ -]?){13,19}\b",
        "critical",
        validator="luhn",
    ),
    _rule(
        "iban",
        "iban",
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
        "high",
    ),
    _rule(
        "phone_international",
        "phone",
        r"(?<![\w.])\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w.])",
        "high",
    ),
    _rule(
        "phone_us",
        "phone",
        r"(?<![\w.\d])\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w.\d])",
        "high",
    ),
    # -- organisational identifiers
    _rule(
        "employee_id",
        "employee_id",
        r"(?i)\b(?:employee|emp|staff|personnel)[_-]?(?:id|number|no)\b\s*[:=]\s*['\"]?(?P<value>[A-Za-z0-9-]{3,})['\"]?",
        "high",
        value_group=1,
    ),
    _rule(
        "customer_id",
        "customer_id",
        r"(?i)\b(?:customer|client|account|subscriber)[_-]?(?:id|number|no)\b\s*[:=]\s*['\"]?(?P<value>[A-Za-z0-9-]{3,})['\"]?",
        "high",
        value_group=1,
    ),
    _rule(
        "medical_id",
        "medical_id",
        r"(?i)\b(?:patient|medical|mrn|health|nhs)[_-]?(?:id|record|number|no)\b\s*[:=]\s*['\"]?(?P<value>[A-Za-z0-9-]{3,})['\"]?",
        "critical",
        value_group=1,
    ),
    _rule(
        "financial_id",
        "financial_id",
        r"(?i)\b(?:account|routing|iban|swift|bic|tax|vat|ssn)[_-]?(?:number|no|id)\b\s*[:=]\s*['\"]?(?P<value>[A-Za-z0-9-]{3,})['\"]?",
        "critical",
        value_group=1,
    ),
    # -- internal infrastructure
    _rule(
        "internal_hostname",
        "internal_hostname",
        r"\b[a-z0-9][a-z0-9-]{1,62}\.(?:internal|intranet|corp|local|lan|private|test)\b",
        "medium",
    ),
    _rule(
        "private_ipv4",
        "ip_address",
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
        "low",
    ),
    _rule(
        "internal_username",
        "internal_username",
        r"(?i)\b(?:username|user|login|operator|owner)\b\s*[:=]\s*['\"](?P<value>[A-Za-z][A-Za-z0-9._-]{2,})['\"]",
        "medium",
        value_group=1,
    ),
    # -- postal addresses, in the one form that is unambiguous
    _rule(
        "street_address",
        "address",
        r"\b\d{1,5}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*\s+"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way)\b\.?",
        "high",
    ),
)

_ALREADY_REDACTED = re.compile(r"<REDACTED_[A-Z_]+>")

# Values that match a PII shape but identify nobody.
_PLACEHOLDER_VALUES = frozenset({
    "none", "null", "true", "false", "example", "test", "todo", "unknown",
    "user@example.com", "noreply@example.com", "john doe", "jane doe",
    "your name", "first last", "localhost", "0.0.0.0", "127.0.0.1",
})

# Version-like digit runs that the card detector would otherwise match.
_VERSION_LIKE = re.compile(r"^\d{1,4}(?:[.-]\d{1,4}){2,}$")


def luhn_valid(value: str) -> bool:
    """Luhn checksum. Without it, any 16-digit run reads as a card number."""
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for position, digit in enumerate(reversed(digits)):
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


_VALIDATORS = {"luhn": luhn_valid}


def _is_placeholder(value: str) -> bool:
    raw = value.strip().strip("'\"")
    # Checked against the original case — see the note in `secret_scanner`.
    if _ALREADY_REDACTED.search(raw):
        return True
    return raw.lower() in _PLACEHOLDER_VALUES or not raw


@dataclass
class PIIResult:
    text: str
    findings: list[PIIFinding] = field(default_factory=list)

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


def detect(
    text: str,
    file: str = "",
    known_names: tuple[str, ...] = (),
) -> PIIResult:
    """Detect and redact personal identifiers.

    `known_names` is the configured roster — the only way a bare name with no
    surrounding annotation is redacted, because nothing else can distinguish it
    from an identifier deterministically.
    """
    if not text:
        return PIIResult(text=text)

    findings: list[PIIFinding] = []
    result = text

    for rule in RULES:
        spans: list[tuple[int, int, int]] = []
        for match in rule.pattern.finditer(result):
            group = rule.value_group if rule.value_group and rule.value_group <= match.re.groups else 0
            value = match.group(group)
            if not value or _is_placeholder(value):
                continue
            if rule.validator:
                validator = _VALIDATORS.get(rule.validator)
                if validator and not validator(value):
                    continue
            if rule.category == "credit_card" and _VERSION_LIKE.match(value.strip()):
                continue
            spans.append((match.start(group), match.end(group), _line_of(result, match.start(group))))

        placeholder = PLACEHOLDERS.get(rule.category, "<REDACTED_PII>")
        for start, end, line in reversed(spans):
            findings.append(
                PIIFinding(
                    category=rule.category,
                    detector=rule.detector,
                    file=file,
                    line=line,
                    severity=rule.severity,
                    redacted_as=placeholder,
                )
            )
            result = result[:start] + placeholder + result[end:]

    result, roster_findings = _apply_known_names(result, file, known_names)
    findings.extend(roster_findings)

    findings.sort(key=lambda f: (f.line, f.category, f.detector))
    return PIIResult(text=result, findings=findings)


def _apply_known_names(
    text: str,
    file: str,
    known_names: tuple[str, ...],
) -> tuple[str, list[PIIFinding]]:
    """Redact names from the configured roster, longest first.

    Longest-first matters: redacting "Ada" before "Ada Lovelace" would leave
    "<REDACTED_NAME> Lovelace" and disclose the surname.
    """
    findings: list[PIIFinding] = []
    result = text

    for name in sorted({n.strip() for n in known_names if n.strip()}, key=len, reverse=True):
        pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
        for match in list(pattern.finditer(result)):
            findings.append(
                PIIFinding(
                    category="person_name",
                    detector="known_name_roster",
                    file=file,
                    line=_line_of(result, match.start()),
                    severity="high",
                    redacted_as=PLACEHOLDERS["person_name"],
                )
            )
        result = pattern.sub(PLACEHOLDERS["person_name"], result)

    return result, findings


def contains_pii(text: str, known_names: tuple[str, ...] = ()) -> bool:
    """True when any detector fires. Used by the firewall as a final check."""
    return not detect(text, known_names=known_names).clean


def category_counts(findings: list[PIIFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1
    return dict(sorted(counts.items()))
