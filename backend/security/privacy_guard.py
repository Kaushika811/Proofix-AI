"""Sanitize a Context Package before it can become a prompt.

Composes the three detectors in a fixed order, chosen because each stage would
otherwise corrupt the next:

1. **Secrets** first. A connection string contains a password *and* a hostname;
   redacting the credential first means the sanitizer sees a clean host to alias
   rather than re-processing a value already replaced.
2. **PII** second. Email addresses inside credential URIs are gone by now, so
   what remains is genuine contact data rather than the tail of a secret.
3. **Sanitizer** last. It aliases what survives — hostnames, packages, company
   identifiers — and its output contains no values the earlier stages would have
   wanted.

**A5.5 is not modified.** This module takes a finished `ContextPackage`, returns
a sanitized copy, and never touches ranking, selection or the metrics A5.5
recorded. The package's own `redactions` list is extended, not replaced, so the
Phase 2 source-level masking and this layer's findings are both visible.

**Fail closed.** If any stage raises, the result is `status="failed"` with the
original package *withheld* — the caller receives a package marked unusable
rather than an unsanitized one. `security_pipeline` turns that into a rejection.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from backend.models.context import ContextPackage, ExtractedSymbol
from backend.models.security import SanitizationFinding, SanitizationReport
from backend.security import pii_detector, secret_scanner
from backend.security.repository_isolation import HOST_PATH_RE, scrub_environment
from backend.security.sanitizer import SanitizerConfig, new_allocator, sanitize


@dataclass
class GuardOptions:
    """What to run. Disabling a stage is a deliberate, recorded choice."""

    detect_secrets: bool = True
    detect_pii: bool = True
    sanitize_identity: bool = True
    known_names: tuple[str, ...] = ()
    sanitizer_config: SanitizerConfig | None = None

    @classmethod
    def from_settings(cls, settings) -> GuardOptions:
        names = getattr(settings, "pii_known_names", "") or ""
        return cls(
            detect_secrets=bool(getattr(settings, "security_detect_secrets", True)),
            detect_pii=bool(getattr(settings, "security_detect_pii", True)),
            sanitize_identity=bool(getattr(settings, "security_sanitize_identity", True)),
            known_names=tuple(n.strip() for n in names.split(",") if n.strip()),
            sanitizer_config=SanitizerConfig.from_settings(settings),
        )


@dataclass
class GuardOutcome:
    """A sanitized package plus the report of what changed."""

    package: ContextPackage | None
    report: SanitizationReport

    @property
    def approved(self) -> bool:
        return self.package is not None and self.report.status != "failed"


class ContextPrivacyGuard:
    """Runs the three detectors over every text field of a package."""

    def __init__(self, options: GuardOptions | None = None):
        self.options = options or GuardOptions()
        self._allocator = new_allocator()

    # -- text-level ------------------------------------------------------

    def sanitize_text(self, text: str, file: str, report: SanitizationReport) -> str:
        """Run the full chain over one string, accumulating findings."""
        if not text:
            return text

        result = text

        # Host paths first. Tracebacks are the highest-risk field in a package
        # and they carry `/Users/<name>/...`, which discloses both the operating
        # user and the deployment layout. Done here rather than only in the
        # pipeline so the guard is complete when used directly on a package.
        result, host_findings = _scrub_host_paths(result, file)
        report.sanitized.extend(host_findings)

        if self.options.detect_secrets:
            scan = secret_scanner.scan_text(result, file=file)
            report.secrets.extend(scan.findings)
            result = scan.text

        if self.options.detect_pii:
            found = pii_detector.detect(result, file=file, known_names=self.options.known_names)
            report.pii.extend(found.findings)
            result = found.text

        if self.options.sanitize_identity:
            cleaned = sanitize(
                result,
                file=file,
                config=self.options.sanitizer_config or SanitizerConfig(),
                allocator=self._allocator,
            )
            report.sanitized.extend(cleaned.findings)
            result = cleaned.text

        return result

    # -- package-level ---------------------------------------------------

    def sanitize_package(self, package: ContextPackage) -> GuardOutcome:
        """Return a sanitized copy of a package, or a failed outcome.

        The original is never mutated: A5.5's stored artifact must remain what
        A5.5 produced, so the security layer's output is provably a separate
        object and a reviewer can diff the two.
        """
        started = time.perf_counter()
        report = SanitizationReport(original_chars=_package_chars(package))

        try:
            clean = package.model_copy(deep=True)

            clean.root_cause_summary = self.sanitize_text(
                clean.root_cause_summary, package.target_file, report
            )
            clean.focused_context = self.sanitize_text(
                clean.focused_context, package.target_file, report
            )
            clean.expected_output_format = self.sanitize_text(
                clean.expected_output_format, package.target_file, report
            )

            clean.relevant_imports = [
                self.sanitize_text(line, package.target_file, report)
                for line in clean.relevant_imports
            ]
            for field in ("acceptance_criteria", "dependency_summary", "contracts",
                          "validation_requirements", "patch_constraints"):
                setattr(
                    clean,
                    field,
                    [self.sanitize_text(item, package.target_file, report) for item in getattr(clean, field)],
                )

            for collection in ("relevant_classes", "relevant_functions", "related_utilities", "constants"):
                setattr(
                    clean,
                    collection,
                    [self._sanitize_symbol(symbol, report) for symbol in getattr(clean, collection)],
                )

            clean.runtime_evidence = self._sanitize_evidence(
                clean.runtime_evidence, package.target_file, report
            )

            # The complete original file is excluded from storage but reaches the
            # prompt as A7's reconstruction contract, so it must be sanitized too.
            if clean.original_complete_file:
                clean.original_complete_file = self.sanitize_text(
                    clean.original_complete_file, package.target_file, report
                )

            report.sanitized_chars = _package_chars(clean)
            report.status = "sanitized" if report.total_findings else "clean"
            report.duration_ms = int((time.perf_counter() - started) * 1000)

            clean.privacy_guard_status = "masked" if report.total_findings else "clean"
            return GuardOutcome(package=clean, report=report)

        except Exception as exc:  # noqa: BLE001 — fail closed, never pass through
            report.status = "failed"
            report.failure_reason = f"{type(exc).__name__}: {exc}"
            report.duration_ms = int((time.perf_counter() - started) * 1000)
            return GuardOutcome(package=None, report=report)

    def _sanitize_symbol(self, symbol: ExtractedSymbol, report: SanitizationReport) -> ExtractedSymbol:
        clean = symbol.model_copy(deep=True)
        clean.source = self.sanitize_text(clean.source, symbol.file, report)
        if getattr(clean, "signature", ""):
            clean.signature = self.sanitize_text(clean.signature, symbol.file, report)
        if getattr(clean, "docstring", None):
            clean.docstring = self.sanitize_text(clean.docstring, symbol.file, report)
        return clean

    def _sanitize_evidence(self, evidence: dict, file: str, report: SanitizationReport) -> dict:
        """Runtime evidence is the highest-risk field: tracebacks carry host paths.

        Values are sanitized; keys are not, because they are a fixed vocabulary
        A7 reads by name.
        """
        clean: dict = {}
        for key, value in (evidence or {}).items():
            if isinstance(value, str):
                clean[key] = self.sanitize_text(value, file, report)
            elif isinstance(value, list):
                clean[key] = [
                    self.sanitize_text(v, file, report) if isinstance(v, str) else v for v in value
                ]
            else:
                clean[key] = value
        return clean


def _scrub_host_paths(text: str, file: str) -> tuple[str, list[SanitizationFinding]]:
    """Replace absolute host paths and environment dumps, recording each one."""
    findings: list[SanitizationFinding] = []

    without_env = scrub_environment(text)
    if without_env != text:
        findings.append(
            SanitizationFinding(
                category="environment",
                detector="env_dump",
                file=file,
                replaced_with="<REDACTED_ENV>",
            )
        )

    for match in HOST_PATH_RE.finditer(without_env):
        findings.append(
            SanitizationFinding(
                category="host_path",
                detector="absolute_path",
                file=file,
                line=without_env.count("\n", 0, match.start()) + 1,
                replaced_with="<PATH>",
            )
        )

    return HOST_PATH_RE.sub("<PATH>", without_env), findings


def _package_chars(package: ContextPackage) -> int:
    total = len(package.focused_context or "") + len(package.root_cause_summary or "")
    for collection in ("relevant_classes", "relevant_functions", "related_utilities", "constants"):
        total += sum(len(s.source or "") for s in getattr(package, collection, []))
    return total


def guard_for_settings(settings) -> ContextPrivacyGuard:
    return ContextPrivacyGuard(GuardOptions.from_settings(settings))
