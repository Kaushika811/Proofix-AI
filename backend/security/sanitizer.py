"""Remove organisational identity from source before it leaves the building.

Secrets and PII are about *disclosure of values*. This module is about
*disclosure of identity*: internal hostnames, private registries, company
namespaces and the deployment topology a repository accidentally documents.
None of it is confidential in the way a password is, and all of it tells an
external party who you are and how you are built.

The controlling constraint is that **the code must still execute**. A repair
model asked to fix `auth.py` cannot work on source whose imports have been
mangled. So substitutions are structure-preserving and consistent: the same
internal domain maps to the same placeholder everywhere in a package, and a
dotted import path keeps its shape (`acme.internal.auth` → `org0.internal.auth`)
so the module structure the repair depends on survives.

Marked-confidential comment blocks are the one case where content is dropped
rather than substituted. A block a developer explicitly labelled CONFIDENTIAL is
a statement that it must not be shared, and there is no substitution that honours
that while keeping the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.models.security import SanitizationFinding

# Markers a developer writes to say "this must not leave". Matched on comment
# lines only, so a string constant discussing confidentiality is untouched.
CONFIDENTIAL_MARKERS = (
    "CONFIDENTIAL",
    "PROPRIETARY",
    "INTERNAL ONLY",
    "INTERNAL-ONLY",
    "DO NOT DISTRIBUTE",
    "TRADE SECRET",
    "NOT FOR DISTRIBUTION",
)

_MARKER_RE = re.compile(
    r"(?im)^(?P<indent>\s*)(?P<hash>#+)\s*.*\b(?:" + "|".join(re.escape(m) for m in CONFIDENTIAL_MARKERS) + r")\b.*$"
)

# Public hosts that are not organisational identity and must survive: removing
# them would break the model's understanding of a dependency.
PUBLIC_DOMAINS = frozenset({
    "github.com", "gitlab.com", "bitbucket.org", "pypi.org", "npmjs.com",
    "python.org", "readthedocs.io", "readthedocs.org", "stackoverflow.com",
    "example.com", "example.org", "localhost", "docker.io", "golang.org",
    "googleapis.com", "amazonaws.com", "azure.com", "cloudflare.com",
    "w3.org", "ietf.org", "json.org", "apache.org", "mit.edu",
})

# Internal-looking TLDs and suffixes. A host under one of these is topology.
INTERNAL_SUFFIXES = (
    ".internal", ".intranet", ".corp", ".local", ".lan", ".private",
    ".test", ".invalid", ".home.arpa",
)

_URL_RE = re.compile(r"\bhttps?://(?P<host>[A-Za-z0-9.-]+)(?P<path>[^\s'\"<>)\]]*)")
_BARE_HOST_RE = re.compile(r"\b(?P<host>[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)\b")


@dataclass
class SanitizerConfig:
    """What counts as "ours". Empty lists mean the detector is inactive."""

    company_identifiers: tuple[str, ...] = ()
    internal_domains: tuple[str, ...] = ()
    private_registries: tuple[str, ...] = ()
    private_package_prefixes: tuple[str, ...] = ()
    redact_repository_names: bool = False
    repository_names: tuple[str, ...] = ()
    strip_confidential_comments: bool = True

    @classmethod
    def from_settings(cls, settings) -> SanitizerConfig:
        def split(value: str) -> tuple[str, ...]:
            return tuple(v.strip() for v in (value or "").split(",") if v.strip())

        return cls(
            company_identifiers=split(getattr(settings, "security_company_identifiers", "")),
            internal_domains=split(getattr(settings, "security_internal_domains", "")),
            private_registries=split(getattr(settings, "security_private_registries", "")),
            private_package_prefixes=split(getattr(settings, "security_private_package_prefixes", "")),
            redact_repository_names=bool(getattr(settings, "security_redact_repository_names", False)),
            repository_names=split(getattr(settings, "security_repository_names", "")),
            strip_confidential_comments=bool(
                getattr(settings, "security_strip_confidential_comments", True)
            ),
        )


@dataclass
class SanitizerResult:
    text: str
    findings: list[SanitizationFinding] = field(default_factory=list)
    # Stable substitution map, so the same identifier maps to the same
    # placeholder across every file in one package.
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.findings


class _AliasAllocator:
    """Deterministic, consistent placeholder naming.

    Consistency is not cosmetic: if `acme.internal` became `org0` in one file and
    `org1` in another, the model would read them as two different systems and
    could produce a patch that wires them together incorrectly.
    """

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def alias(self, kind: str, value: str) -> str:
        key = f"{kind}:{value.lower()}"
        if key not in self.aliases:
            index = self._counters.get(kind, 0)
            self._counters[kind] = index + 1
            self.aliases[key] = f"{kind}{index}"
        return self.aliases[key]


def is_internal_host(host: str, config: SanitizerConfig) -> bool:
    """True when a hostname discloses internal topology."""
    lowered = host.lower().rstrip(".")
    if not lowered or lowered in PUBLIC_DOMAINS:
        return False
    if any(lowered.endswith(f".{public}") or lowered == public for public in PUBLIC_DOMAINS):
        return False
    if any(lowered.endswith(suffix) for suffix in INTERNAL_SUFFIXES):
        return True
    if any(lowered == d.lower() or lowered.endswith(f".{d.lower()}") for d in config.internal_domains):
        return True
    if any(lowered == r.lower() or lowered.endswith(f".{r.lower()}") for r in config.private_registries):
        return True
    return any(identifier.lower() in lowered for identifier in config.company_identifiers)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def sanitize(
    text: str,
    file: str = "",
    config: SanitizerConfig | None = None,
    allocator: _AliasAllocator | None = None,
) -> SanitizerResult:
    """Remove organisational identity while keeping the code executable."""
    if not text:
        return SanitizerResult(text=text)

    config = config or SanitizerConfig()
    allocator = allocator or _AliasAllocator()
    findings: list[SanitizationFinding] = []
    result = text

    result, marker_findings = _strip_confidential_blocks(result, file, config)
    findings.extend(marker_findings)

    result, url_findings = _sanitize_urls(result, file, config, allocator)
    findings.extend(url_findings)

    result, host_findings = _sanitize_bare_hosts(result, file, config, allocator)
    findings.extend(host_findings)

    result, package_findings = _sanitize_packages(result, file, config, allocator)
    findings.extend(package_findings)

    result, identifier_findings = _sanitize_identifiers(result, file, config, allocator)
    findings.extend(identifier_findings)

    result, repo_findings = _sanitize_repository_names(result, file, config, allocator)
    findings.extend(repo_findings)

    findings.sort(key=lambda f: (f.line, f.category, f.detector))
    return SanitizerResult(text=result, findings=findings, aliases=dict(allocator.aliases))


def _strip_confidential_blocks(
    text: str,
    file: str,
    config: SanitizerConfig,
) -> tuple[str, list[SanitizationFinding]]:
    """Replace comment lines carrying a confidentiality marker.

    The marker line and any immediately following comment lines are treated as
    one block: a `# CONFIDENTIAL` header applies to the paragraph beneath it, not
    only to its own line.
    """
    if not config.strip_confidential_comments:
        return text, []

    findings: list[SanitizationFinding] = []
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if _MARKER_RE.match(line.rstrip("\n")):
            indent = re.match(r"\s*", line).group(0)
            findings.append(
                SanitizationFinding(
                    category="confidential_comment",
                    detector="marker_block",
                    file=file,
                    line=index + 1,
                    replaced_with="<REDACTED_CONFIDENTIAL_COMMENT>",
                )
            )
            out.append(f"{indent}# <REDACTED_CONFIDENTIAL_COMMENT>\n")
            index += 1
            # Consume the rest of the contiguous comment block.
            while index < len(lines) and re.match(r"\s*#", lines[index]):
                index += 1
            continue
        out.append(line)
        index += 1

    return "".join(out), findings


def _sanitize_urls(
    text: str,
    file: str,
    config: SanitizerConfig,
    allocator: _AliasAllocator,
) -> tuple[str, list[SanitizationFinding]]:
    """Replace internal URLs, keeping the scheme and path shape."""
    findings: list[SanitizationFinding] = []
    result = text
    spans = []

    for match in _URL_RE.finditer(result):
        host = match.group("host")
        if not is_internal_host(host, config):
            continue
        alias = allocator.alias("host", host)
        spans.append((match.start("host"), match.end("host"), alias, _line_of(result, match.start())))

    for start, end, alias, line in reversed(spans):
        findings.append(
            SanitizationFinding(
                category="internal_url",
                detector="url_host",
                file=file,
                line=line,
                replaced_with=f"{alias}.example.internal",
            )
        )
        result = result[:start] + f"{alias}.example.internal" + result[end:]

    return result, findings


def _sanitize_bare_hosts(
    text: str,
    file: str,
    config: SanitizerConfig,
    allocator: _AliasAllocator,
) -> tuple[str, list[SanitizationFinding]]:
    """Replace internal hostnames appearing outside a URL."""
    findings: list[SanitizationFinding] = []
    result = text
    spans = []

    for match in _BARE_HOST_RE.finditer(result):
        host = match.group("host")
        if "example.internal" in host or not is_internal_host(host, config):
            continue
        alias = allocator.alias("host", host)
        spans.append((match.start(), match.end(), alias, _line_of(result, match.start())))

    for start, end, alias, line in reversed(spans):
        findings.append(
            SanitizationFinding(
                category="private_domain",
                detector="bare_hostname",
                file=file,
                line=line,
                replaced_with=f"{alias}.example.internal",
            )
        )
        result = result[:start] + f"{alias}.example.internal" + result[end:]

    return result, findings


def _sanitize_packages(
    text: str,
    file: str,
    config: SanitizerConfig,
    allocator: _AliasAllocator,
) -> tuple[str, list[SanitizationFinding]]:
    """Rename private package namespaces, preserving dotted structure.

    `acme.billing.invoice` becomes `pkg0.billing.invoice`: the module hierarchy
    the repair reasons about is intact, only the top-level namespace is renamed.
    """
    findings: list[SanitizationFinding] = []
    result = text

    for prefix in config.private_package_prefixes:
        if not prefix:
            continue
        alias = allocator.alias("pkg", prefix)
        pattern = re.compile(rf"(?<![\w.]){re.escape(prefix)}(?=[\s.,)\]'\"]|$)")
        for match in list(pattern.finditer(result)):
            findings.append(
                SanitizationFinding(
                    category="private_package",
                    detector="package_prefix",
                    file=file,
                    line=_line_of(result, match.start()),
                    replaced_with=alias,
                )
            )
        result = pattern.sub(alias, result)

    return result, findings


def _sanitize_identifiers(
    text: str,
    file: str,
    config: SanitizerConfig,
    allocator: _AliasAllocator,
) -> tuple[str, list[SanitizationFinding]]:
    """Replace remaining bare company identifiers."""
    findings: list[SanitizationFinding] = []
    result = text

    for identifier in config.company_identifiers:
        if not identifier:
            continue
        alias = allocator.alias("org", identifier)
        pattern = re.compile(rf"\b{re.escape(identifier)}\b", re.IGNORECASE)
        for match in list(pattern.finditer(result)):
            findings.append(
                SanitizationFinding(
                    category="company_identifier",
                    detector="identifier",
                    file=file,
                    line=_line_of(result, match.start()),
                    replaced_with=alias,
                )
            )
        result = pattern.sub(alias, result)

    return result, findings


def _sanitize_repository_names(
    text: str,
    file: str,
    config: SanitizerConfig,
    allocator: _AliasAllocator,
) -> tuple[str, list[SanitizationFinding]]:
    """Optional policy: replace repository names."""
    if not config.redact_repository_names:
        return text, []

    findings: list[SanitizationFinding] = []
    result = text

    for name in config.repository_names:
        if not name:
            continue
        alias = allocator.alias("repo", name)
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        for match in list(pattern.finditer(result)):
            findings.append(
                SanitizationFinding(
                    category="repository_name",
                    detector="repository",
                    file=file,
                    line=_line_of(result, match.start()),
                    replaced_with=alias,
                )
            )
        result = pattern.sub(alias, result)

    return result, findings


def new_allocator() -> _AliasAllocator:
    """A fresh alias namespace, shared across one package's files."""
    return _AliasAllocator()
