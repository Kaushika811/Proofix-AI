"""Final inspection of every outgoing prompt.

The firewall runs *after* sanitization and *after* policy evaluation, on the
exact bytes that would go on the wire. That ordering is the point: the earlier
stages decide what should happen, and the firewall verifies what actually did.
A sanitizer bug, a prompt-assembly mistake, or an agent that concatenated a raw
file into a template all produce the same symptom here — a residual secret in
the final string — and all are caught by the same check.

Eight rules, each independently reported so a rejection names every reason:

    residual_secrets      a credential survived sanitization
    residual_pii          a personal identifier survived sanitization
    private_key_material  PEM blocks, in any form
    binary_content        non-text bytes, base64 blobs, embedded archives
    prompt_size           oversized context
    file_count            more files than the policy permits
    whole_repository      the shape of a full-repository dump
    repository_metadata   .git internals, environment dumps, host paths

Every rejection is logged with its rule and the observed value — never the
matched content, which would put the secret in the log.
"""

from __future__ import annotations

import logging
import re
import time

from backend.models.security import (
    Decision,
    FirewallVerdict,
    PolicyViolation,
    SecurityPolicy,
)
from backend.security.pii_detector import detect as detect_pii
from backend.security.secret_scanner import scan_text as scan_secrets
from backend.services.privacy_guard import shannon_entropy

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4

# A prompt naming this many distinct source files is a repository dump, whatever
# the policy's per-request file limit says.
WHOLE_REPOSITORY_FILE_MARKERS = 40

# Base64 runs this long are payloads, not identifiers.
BINARY_BLOB_MIN_LENGTH = 512

_PEM_RE = re.compile(r"-----BEGIN [A-Z ]*(?:PRIVATE KEY|CERTIFICATE)[A-Z ]*-----")
_BASE64_BLOB_RE = re.compile(rf"[A-Za-z0-9+/]{{{BINARY_BLOB_MIN_LENGTH},}}={{0,2}}")
_DATA_URI_RE = re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,", re.IGNORECASE)
_ARCHIVE_MAGIC = ("PK\x03\x04", "\x1f\x8b", "%PDF-", "\x89PNG", "GIF89a", "\xff\xd8\xff")

# File-path shapes counted when estimating how much of a repository is present.
_PATH_RE = re.compile(r"(?:^|[\s\"'`(\[])([A-Za-z0-9_./-]+\.(?:py|pyi|js|ts|go|rs|java|rb|php|c|h|cpp))\b")

# Repository internals and host state that should never reach a provider.
# `(?<![\w])` rather than `(?:^|/)`: these paths appear mid-sentence in
# tracebacks and prose ("see .git/config"), and anchoring to line start or a
# slash silently misses exactly those cases.
_METADATA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("git_internals", re.compile(r"(?<![\w])\.git/(?:config|HEAD|objects|refs|logs)\b")),
    ("git_credentials", re.compile(r"(?<![\w])\.git-credentials\b")),
    ("env_file", re.compile(r"(?<![\w])\.env(?:\.[a-z]+)?\b")),
    ("environment_dump", re.compile(r"(?im)^\s*(?:PATH|HOME|USER|LD_LIBRARY_PATH|AWS_PROFILE)\s*=\s*\S+")),
    ("host_path", re.compile(r"(?:/Users/|/home/|C:\\\\Users\\\\|/root/)[A-Za-z0-9._-]+")),
    ("ssh_config", re.compile(r"(?<![\w])\.ssh/(?:id_[a-z0-9]+|config|known_hosts)\b")),
    ("cloud_credentials_file", re.compile(r"(?<![\w])\.aws/credentials\b|(?<![\w])\.kube/config\b")),
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


def count_referenced_files(text: str) -> int:
    """Distinct source-file paths named in the prompt."""
    return len({m.group(1) for m in _PATH_RE.finditer(text or "")})


# Real base64 payloads carry ~5–6 bits of entropy per character. Length alone is
# not evidence: a run of repeated characters, an `=====` separator rule, or a
# long placeholder string all match the base64 charset and none is a payload.
BINARY_ENTROPY_THRESHOLD = 3.0


def contains_binary(text: str) -> bool:
    """True when the text carries non-text payload."""
    if not text:
        return False
    if any(magic in text for magic in _ARCHIVE_MAGIC):
        return True
    if _DATA_URI_RE.search(text):
        return True
    if "\x00" in text:
        return True
    return any(
        shannon_entropy(match.group(0)) >= BINARY_ENTROPY_THRESHOLD
        for match in _BASE64_BLOB_RE.finditer(text)
    )


# Rules that describe a *structurally wrong* prompt rather than a merely dirty
# one. Sanitization can legitimately fix a hardcoded password; it cannot fix a
# prompt that contains a private key file, a zip archive, or the whole
# repository — those mean context assembly went wrong upstream, and redacting
# them would hide the fault instead of surfacing it. These are therefore checked
# *before* sanitization, on the caller's original text.
STRUCTURAL_RULES = frozenset({
    "private_key_material",
    "binary_content",
    "whole_repository",
    "repository_metadata",
})


class PromptFirewall:
    """Inspects a fully-assembled prompt against a policy."""

    def __init__(self, policy: SecurityPolicy, known_names: tuple[str, ...] = ()):
        self.policy = policy
        self.known_names = known_names

    def pre_inspect(
        self,
        prompt: str,
        system: str = "",
        *,
        files: tuple[str, ...] = (),
        run_id: str = "",
    ) -> FirewallVerdict:
        """Structural rules only, run before sanitization.

        Catches the cases where sanitization would otherwise erase the evidence
        of a problem: a PEM block redacted to `<REDACTED_PRIVATE_KEY>` passes
        every post-sanitization check, and the fact that a key file reached the
        context builder at all would never be reported.
        """
        # `log=False`: the full inspection runs here only to reach the
        # structural rules, and the non-structural findings it reports are about
        # to be *fixed* by sanitization. Logging them would fill the security
        # log with rejections that never happened.
        full = self.inspect(prompt, system, files=files, run_id=run_id, log=False)

        structural = {k: v for k, v in full.rule_results.items() if k in STRUCTURAL_RULES}
        violations = [v for v in full.violations if v.rule in STRUCTURAL_RULES]

        verdict = FirewallVerdict(
            decision="reject" if violations else "allow",
            rule_results=structural,
            violations=violations,
            prompt_chars=full.prompt_chars,
            estimated_tokens=full.estimated_tokens,
            file_count=full.file_count,
            inspected_ms=full.inspected_ms,
        )
        if violations:
            self._log_rejection(verdict, run_id, stage="pre_sanitization")
        return verdict

    def inspect(
        self,
        prompt: str,
        system: str = "",
        *,
        files: tuple[str, ...] = (),
        run_id: str = "",
        log: bool = True,
    ) -> FirewallVerdict:
        """Run every rule. Returns a verdict naming each rule's outcome."""
        started = time.perf_counter()
        combined = f"{system}\n{prompt}" if system else prompt

        results: dict[str, bool] = {}
        violations: list[PolicyViolation] = []

        def fail(rule: str, detail: str, severity: str, observed: str, permitted: str) -> None:
            results[rule] = False
            violations.append(
                PolicyViolation(
                    rule=rule,
                    detail=detail,
                    severity=severity,  # type: ignore[arg-type]
                    observed=observed,
                    permitted=permitted,
                )
            )

        # -- residual credentials
        secret_scan = scan_secrets(combined)
        results["residual_secrets"] = secret_scan.clean
        if not secret_scan.clean:
            categories = sorted({f.category for f in secret_scan.findings})
            fail(
                "residual_secrets",
                f"{len(secret_scan.findings)} credential(s) survived sanitization",
                "critical",
                ", ".join(categories),
                "0",
            )

        # -- residual PII
        if not self.policy.allow_pii:
            pii_scan = detect_pii(combined, known_names=self.known_names)
            results["residual_pii"] = pii_scan.clean
            if not pii_scan.clean:
                categories = sorted({f.category for f in pii_scan.findings})
                fail(
                    "residual_pii",
                    f"{len(pii_scan.findings)} personal identifier(s) survived sanitization",
                    "critical",
                    ", ".join(categories),
                    "0",
                )
        else:
            results["residual_pii"] = True

        # -- key material
        results["private_key_material"] = not _PEM_RE.search(combined)
        if not results["private_key_material"]:
            fail(
                "private_key_material",
                "prompt contains PEM key or certificate material",
                "critical",
                "PEM block",
                "none",
            )

        # -- binary payloads
        results["binary_content"] = not contains_binary(combined)
        if not results["binary_content"]:
            fail(
                "binary_content",
                "prompt contains binary or base64 payload rather than source",
                "high",
                "binary blob",
                "text only",
            )

        # -- size
        size = len(combined)
        results["prompt_size"] = size <= self.policy.max_context_chars
        if not results["prompt_size"]:
            fail(
                "prompt_size",
                f"prompt is {size} chars, {self.policy.name} permits {self.policy.max_context_chars}",
                "high",
                str(size),
                str(self.policy.max_context_chars),
            )

        # -- file count
        declared = len(files)
        referenced = count_referenced_files(combined)
        observed_files = max(declared, referenced)
        results["file_count"] = observed_files <= self.policy.max_files
        if not results["file_count"]:
            fail(
                "file_count",
                f"{observed_files} file(s) present, {self.policy.name} permits {self.policy.max_files}",
                "high",
                str(observed_files),
                str(self.policy.max_files),
            )

        # -- whole-repository shape
        results["whole_repository"] = referenced < WHOLE_REPOSITORY_FILE_MARKERS
        if not results["whole_repository"]:
            fail(
                "whole_repository",
                f"prompt references {referenced} distinct source files — this is a repository dump",
                "critical",
                str(referenced),
                f"<{WHOLE_REPOSITORY_FILE_MARKERS}",
            )

        # -- repository and host metadata
        metadata_hits = sorted(
            name for name, pattern in _METADATA_PATTERNS if pattern.search(combined)
        )
        results["repository_metadata"] = not metadata_hits
        if metadata_hits:
            fail(
                "repository_metadata",
                f"prompt exposes repository or host internals: {', '.join(metadata_hits)}",
                "critical",
                ", ".join(metadata_hits),
                "none",
            )

        decision: Decision = "reject" if violations else "allow"
        verdict = FirewallVerdict(
            decision=decision,
            rule_results=dict(sorted(results.items())),
            violations=violations,
            prompt_chars=size,
            estimated_tokens=estimate_tokens(combined),
            file_count=observed_files,
            inspected_ms=int((time.perf_counter() - started) * 1000),
        )

        if decision == "reject" and log:
            self._log_rejection(verdict, run_id, stage="egress")

        return verdict

    def _log_rejection(self, verdict: FirewallVerdict, run_id: str, stage: str) -> None:
        """Record a rejection: rules and observed shapes, never matched content."""
        logger.warning(
            "prompt_firewall_rejected",
            extra={
                "firewall": {
                    "run_id": run_id,
                    "stage": stage,
                    "policy": self.policy.name,
                    "rules_failed": verdict.rules_failed,
                    "prompt_chars": verdict.prompt_chars,
                    "file_count": verdict.file_count,
                    "violations": [
                        {"rule": v.rule, "observed": v.observed, "severity": v.severity}
                        for v in verdict.violations
                    ],
                }
            },
        )
