"""Correlate A4's evidence into an auditable investigation report.

A4 already gathers evidence and produces a cited root-cause brief. What it did
not do until now is *show its work*: which upstream sources it consulted, what
each one actually said, and which of those statements argue for the finding
being real versus against it. This module answers exactly that, deterministically
and with no LLM involvement.

Everything here is derived from artifacts other agents genuinely persisted:

* **scanner** — `StaticAnalysisReport.scanner_status` (A3's own per-tool
  outcome) and the tool attribution on each ranked finding.
* **reproduction** — `ReproductionResult` (A3.5), including the command it ran
  and the exit code it got.
* **source** — A4's citations after `citation_verifier` has tried to anchor each
  one to real source, plus the stack-trace reference when there is one.
* **dependency** — A2's OSV advisories, but only those it classified as
  reachable against A1's import graph.

Two judgements are deliberately *not* made:

1. A3.5's gate is a full-suite run that does not target a specific A3 finding
   (`orchestrator/nodes.py::reproduction_gate` never passes `static_report`
   into it). A passing suite therefore does not contradict a static finding —
   it is recorded as neutral, with the reason stated on the item.
2. A source that could not run produces an `unavailable` item, never a
   `contradicting` one. No evidence is not evidence of absence.
"""

from __future__ import annotations

from backend.models.investigation import (
    ConfidenceComponent,
    EvidenceCompleteness,
    EvidenceItem,
    EvidenceStatus,
    InvestigationReport,
    RootCauseSource,
    UnavailableSource,
)
from backend.models.root_cause import RootCauseBrief

#: A3.5's four outcomes in the workspace's vocabulary. Identical mapping to
#: `ui_projection._REPRODUCTION_UI_STATUS`, kept in the model layer so the
#: report is complete before any projection runs.
_REPRODUCTION_STATUS = {
    "CONFIRMED": "reproduced",
    "UNCONFIRMED": "not_reproduced",
    "NO_TESTS": "unavailable",
    "INFRA_ERROR": "error",
}

#: A3's per-scanner outcomes → what that means as evidence.
#: `stubbed` is deliberately `unavailable`: the tool was absent and A3
#: substituted its own heuristic scan, so the finding is not that scanner's
#: measurement and must not be presented as one.
_SCANNER_EVIDENCE_STATUS: dict[str, EvidenceStatus] = {
    "ok": "present",
    "ok_no_findings": "absent",
    "unavailable": "unavailable",
    "stubbed": "unavailable",
}

_SCANNER_REASON = {
    "unavailable": "The scanner is not installed or could not be executed.",
    "stubbed": (
        "The scanner could not be executed; A3 substituted its own heuristic "
        "scan, so any finding here is not this scanner's measurement."
    ),
}

_CATEGORIES = ("scanner", "reproduction", "source", "dependency")


def _subject_from(
    reproduction: dict, prioritized: list[dict]
) -> tuple[str | None, dict]:
    """Pick what A4 investigated, and say which kind of thing it is.

    A reproduced runtime failure outranks a static finding: it is observed
    behaviour rather than a pattern match. With neither, there is no subject —
    the caller reports `no_finding` rather than promoting an arbitrary row.
    """
    if reproduction.get("status") == "CONFIRMED":
        exception_type = reproduction.get("exception_type")
        message = reproduction.get("exception_message")
        title = (
            f"{exception_type}: {message}"
            if exception_type and message
            else exception_type or reproduction.get("failing_test")
        )
        return "runtime_failure", {
            "finding_id": reproduction.get("failing_test") or "runtime-failure",
            "title": title,
            "file": reproduction.get("failing_file"),
            "line": reproduction.get("failing_line"),
            # A runtime failure has no severity: no tool assigned one, and
            # inventing a band here would be exactly the fabrication this
            # report exists to rule out.
            "severity": None,
            "severity_measured": False,
        }

    if prioritized:
        top = prioritized[0]
        return "static_finding", {
            "finding_id": top.get("id"),
            "title": top.get("message") or None,
            "file": top.get("file"),
            "line": top.get("line"),
            "severity": top.get("severity"),
            "severity_measured": bool(top.get("severity_measured", False)),
        }

    return None, {}


def _scanner_items(
    static_report: dict | None,
    prioritized: list[dict],
    subject_kind: str | None,
    subject: dict,
) -> tuple[list[EvidenceItem], list[UnavailableSource]]:
    if static_report is None:
        return (
            [
                EvidenceItem(
                    id="scanner",
                    category="scanner",
                    source="static analysis",
                    description="A3 has not produced a static-analysis report for this run.",
                    status="unavailable",
                    stance="neutral",
                )
            ],
            [UnavailableSource(source="A3 static analysis", reason="A3 did not complete")],
        )

    scanner_status: dict[str, str] = static_report.get("scanner_status") or {}
    if not scanner_status:
        return (
            [
                EvidenceItem(
                    id="scanner",
                    category="scanner",
                    source="static analysis",
                    description=(
                        "A3 ran but recorded no per-scanner outcome, so which "
                        "scanners executed is unknown."
                    ),
                    status="unavailable",
                    stance="neutral",
                )
            ],
            [
                UnavailableSource(
                    source="A3 scanner status", reason="A3 recorded no per-scanner outcome"
                )
            ],
        )

    items: list[EvidenceItem] = []
    unavailable: list[UnavailableSource] = []
    subject_file = subject.get("file")

    for scanner in sorted(scanner_status):
        label = scanner_status[scanner]
        status = _SCANNER_EVIDENCE_STATUS.get(label, "unavailable")
        matched = [f for f in prioritized if scanner in (f.get("tools") or [])]
        # Correlation is at file granularity: A3 clusters findings into 5-line
        # buckets, so demanding an exact line match against a runtime failure's
        # line would report "no scanner evidence" for a scanner that flagged
        # the very function that failed.
        on_subject = [f for f in matched if subject_file and f.get("file") == subject_file]

        if status == "unavailable":
            reason = _SCANNER_REASON.get(label, "The scanner did not run.")
            items.append(
                EvidenceItem(
                    id=f"scanner:{scanner}",
                    category="scanner",
                    source=scanner,
                    description=reason,
                    status="unavailable",
                    stance="neutral",
                    detail={"scannerStatus": label},
                )
            )
            unavailable.append(UnavailableSource(source=scanner, reason=reason))
            continue

        if status == "absent":
            items.append(
                EvidenceItem(
                    id=f"scanner:{scanner}",
                    category="scanner",
                    source=scanner,
                    description="Ran successfully and reported no findings.",
                    status="absent",
                    # A clean scan of a repository is not an argument against a
                    # specific finding another tool did report.
                    stance="neutral",
                    detail={"scannerStatus": label, "findings": 0},
                )
            )
            continue

        # `present`: the scanner ran and contributed findings.
        measured = [f for f in on_subject if f.get("severity_measured")]
        strength = max((f.get("severity") or 0.0) for f in measured) if measured else None
        items.append(
            EvidenceItem(
                id=f"scanner:{scanner}",
                category="scanner",
                source=scanner,
                description=(
                    f"Reported {len(on_subject)} finding(s) in the file under investigation."
                    if on_subject
                    else (
                        f"Ran and contributed {len(matched)} ranked finding(s) elsewhere "
                        "in the repository, none in the file under investigation."
                    )
                ),
                status="present",
                stance="supporting" if on_subject else "neutral",
                strength=strength,
                strength_basis=(
                    f"{scanner}'s own severity for its finding in the file under investigation"
                    if strength is not None
                    else None
                ),
                detail={
                    "scannerStatus": label,
                    "findings": len(matched),
                    "findingsAtSubject": len(on_subject),
                    "lines": sorted({f.get("line") for f in on_subject if f.get("line")}),
                },
            )
        )

    return items, unavailable


def _reproduction_items(
    reproduction: dict | None, subject_kind: str | None
) -> tuple[list[EvidenceItem], list[UnavailableSource]]:
    if not reproduction:
        return (
            [
                EvidenceItem(
                    id="reproduction",
                    category="reproduction",
                    source="pytest",
                    description="A3.5 has not run, so there is no runtime evidence either way.",
                    status="unavailable",
                    stance="neutral",
                )
            ],
            [UnavailableSource(source="A3.5 reproduction", reason="A3.5 did not complete")],
        )

    raw_status = reproduction.get("status")
    ui_status = _REPRODUCTION_STATUS.get(raw_status)
    detail = {
        "reproductionStatus": raw_status,
        "command": reproduction.get("command") or None,
        "exitCode": reproduction.get("exit_code"),
        "failingTest": reproduction.get("failing_test"),
        "testsCollected": reproduction.get("tests_collected"),
        "testsPassed": reproduction.get("tests_passed"),
        "testsFailed": reproduction.get("tests_failed"),
        "evidenceSource": reproduction.get("evidence_source"),
        "timedOut": bool(reproduction.get("timed_out")),
    }

    if ui_status == "reproduced":
        return (
            [
                EvidenceItem(
                    id="reproduction",
                    category="reproduction",
                    source="pytest",
                    description=(
                        f"{reproduction.get('failing_test') or 'A test'} failed with "
                        f"{reproduction.get('exception_type') or 'an error'} — the reported "
                        "behaviour was observed, not inferred."
                    ),
                    status="present",
                    stance="supporting",
                    # A3.5's own confidence: 0.9 for a structured pytest report,
                    # 0.7 for the output-text fallback. Its own measurement, not
                    # a value assigned here.
                    strength=reproduction.get("confidence"),
                    strength_basis=(
                        "A3.5's confidence in its own evidence source "
                        f"({reproduction.get('evidence_source') or 'unrecorded'})"
                    ),
                    detail=detail,
                )
            ],
            [],
        )

    if ui_status == "not_reproduced":
        return (
            [
                EvidenceItem(
                    id="reproduction",
                    category="reproduction",
                    source="pytest",
                    description=(
                        "The full suite passed. A3.5 runs every test rather than "
                        "targeting this finding, so this neither confirms nor "
                        "refutes it."
                        if subject_kind == "static_finding"
                        else "The full suite passed — no failure was observed."
                    ),
                    status="absent",
                    stance="neutral",
                    detail=detail,
                )
            ],
            [],
        )

    if ui_status == "unavailable":
        reason = "pytest collected zero tests, so there was nothing to reproduce against."
        return (
            [
                EvidenceItem(
                    id="reproduction",
                    category="reproduction",
                    source="pytest",
                    description=reason,
                    status="unavailable",
                    stance="neutral",
                    detail=detail,
                )
            ],
            [UnavailableSource(source="A3.5 reproduction", reason=reason)],
        )

    if ui_status == "error":
        reason = (
            reproduction.get("infra_detail")
            or "The pytest subprocess did not complete, so reproduction is unresolved."
        )
        return (
            [
                EvidenceItem(
                    id="reproduction",
                    category="reproduction",
                    source="pytest",
                    description=reason,
                    status="error",
                    stance="neutral",
                    detail=detail,
                )
            ],
            [UnavailableSource(source="A3.5 reproduction", reason=reason)],
        )

    reason = f"A3.5 recorded an unrecognised status ({raw_status!r})."
    return (
        [
            EvidenceItem(
                id="reproduction",
                category="reproduction",
                source="pytest",
                description=reason,
                status="error",
                stance="neutral",
                detail=detail,
            )
        ],
        [UnavailableSource(source="A3.5 reproduction", reason=reason)],
    )


def _source_items(brief: RootCauseBrief) -> list[EvidenceItem]:
    """Report source anchoring separately from semantic claim support."""
    items: list[EvidenceItem] = []

    if not brief.citations:
        items.append(
            EvidenceItem(
                id="source",
                category="source",
                source="source anchoring",
                description="A4 produced no citations, so nothing was anchored to source.",
                status="absent",
                stance="neutral",
            )
        )
    for idx, citation in enumerate(brief.citations):
        verified = bool(citation.verified)
        items.append(
            EvidenceItem(
                id=f"citation:{idx}",
                category="source",
                source=f"{citation.file}:{citation.line}",
                description=citation.claim
                or ("Claim anchored to source." if verified else "Claim could not be anchored."),
                status="present" if verified else "absent",
                stance=("supporting" if citation.claim_support_status == "supported"
                        else "contradicting" if citation.claim_support_status == "contradicted"
                        or not verified else "neutral"),
                detail={
                    "file": citation.file,
                    "line": citation.line,
                    "verified": verified,
                    "sourceLocated": verified,
                    "claimSupportStatus": citation.claim_support_status,
                    "claimSupportReason": citation.claim_support_reason,
                    "claim": citation.claim,
                    # There is no source viewer behind this: the repository clone
                    # is removed when the run finishes, so A4 cannot serve the
                    # lines it cited. Stated as a fact rather than offered as a
                    # button that would not work.
                    "sourceAvailable": False,
                },
            )
        )

    stack = next((r for r in brief.evidence_refs if r.source == "stack_trace"), None)
    if stack is not None:
        items.append(
            EvidenceItem(
                id="stack_trace",
                category="source",
                source="stack trace",
                description="A traceback from the reproduced failure was available to the analysis.",
                status="present",
                stance="supporting",
                strength=stack.weight or None,
                strength_basis=(
                    "STACK_WEIGHT in root_cause_builder — A4's own weighting for "
                    "stack-trace evidence"
                )
                if stack.weight
                else None,
                detail={"hasTraceback": bool(brief.stack_evidence)},
            )
        )

    return items


def _dependency_items(
    cve_report: dict | None, brief: RootCauseBrief
) -> tuple[list[EvidenceItem], list[UnavailableSource]]:
    if cve_report is None:
        return (
            [
                EvidenceItem(
                    id="dependency",
                    category="dependency",
                    source="OSV",
                    description="A2 has not run, so dependency reachability is unknown.",
                    status="unavailable",
                    stance="neutral",
                )
            ],
            [UnavailableSource(source="A2 dependency analysis", reason="A2 did not complete")],
        )

    findings = cve_report.get("findings") or []
    reachable = [f for f in findings if f.get("classification") == "Critical"]

    if not reachable:
        return (
            [
                EvidenceItem(
                    id="dependency",
                    category="dependency",
                    source="OSV",
                    description=(
                        f"A2 checked {cve_report.get('total_dependencies') or 0} dependencies and "
                        f"found {len(findings)} advisory(ies), none reachable from this "
                        "repository's own code."
                    ),
                    status="absent",
                    stance="neutral",
                    detail={
                        "advisories": len(findings),
                        "reachable": 0,
                        "manifest": cve_report.get("manifest"),
                    },
                )
            ],
            [],
        )

    items = [
        EvidenceItem(
            id=f"cve:{f.get('cve_id') or f.get('package')}",
            category="dependency",
            source=f.get("cve_id") or f.get("package") or "advisory",
            description=(
                f"{f.get('cve_id') or 'An advisory'} affects {f.get('package')} "
                f"{f.get('installed_version') or ''}".strip()
                + " and A2 proved it reachable from this repository's code."
            ),
            status="present",
            # Reachable only counts for this investigation when A4 actually used
            # it — `cve_context` is the set A4 folded into its analysis.
            stance="supporting" if (f.get("cve_id") in brief.cve_context) else "neutral",
            # OSV severity is a free-form string (sometimes a CVSS score,
            # sometimes the word "HIGH"), so there is no 0..1 strength to state.
            strength=None,
            strength_basis=None,
            detail={
                "package": f.get("package"),
                "installedVersion": f.get("installed_version"),
                "severity": f.get("severity"),
                "affectedSymbol": f.get("affected_symbol"),
                "reachPath": f.get("reach_path"),
            },
        )
        for f in reachable
    ]
    return items, []


def _completeness(items: list[EvidenceItem]) -> EvidenceCompleteness:
    """Coverage of the four evidence categories — not a quality score.

    A category counts as measured when at least one of its items reached a real
    conclusion (`present` or `absent`). `unavailable` and `error` do not count:
    a source that could not speak has not been consulted.
    """
    status_by_category: dict[str, EvidenceStatus] = {}
    for category in _CATEGORIES:
        in_category = [i for i in items if i.category == category]
        if not in_category:
            status_by_category[category] = "unavailable"
        elif any(i.status == "present" for i in in_category):
            status_by_category[category] = "present"
        elif any(i.status == "absent" for i in in_category):
            status_by_category[category] = "absent"
        elif any(i.status == "error" for i in in_category):
            status_by_category[category] = "error"
        else:
            status_by_category[category] = "unavailable"

    measured = sum(1 for s in status_by_category.values() if s in ("present", "absent"))
    total = len(_CATEGORIES)
    return EvidenceCompleteness(
        measured_categories=measured,
        total_categories=total,
        ratio=round(measured / total, 3) if total else None,
        category_status=status_by_category,
    )


def build_investigation_report(
    *,
    brief: RootCauseBrief,
    static_report: dict | None,
    reproduction: dict | None,
    cve_report: dict | None,
    confidence_components: list[tuple[str, float, str]],
    root_cause_source: RootCauseSource | None,
    errors: list[str] | None = None,
) -> InvestigationReport:
    """Correlate everything A4 consulted into one auditable report.

    Pure and deterministic: same inputs, same report, no LLM call and no I/O.
    """
    errors = list(errors or [])
    repro = reproduction or {}
    prioritized = list((static_report or {}).get("prioritized") or [])

    subject_kind, subject = _subject_from(repro, prioritized)

    scanner_items, scanner_missing = _scanner_items(
        static_report, prioritized, subject_kind, subject
    )
    repro_items, repro_missing = _reproduction_items(reproduction, subject_kind)
    source_items = _source_items(brief)
    dependency_items, dependency_missing = _dependency_items(cve_report, brief)

    evidence = scanner_items + repro_items + source_items + dependency_items
    unavailable = scanner_missing + repro_missing + dependency_missing
    completeness = _completeness(evidence)

    if subject_kind is None:
        status = "no_finding"
    elif errors:
        status = "error"
    elif unavailable or not brief.root_cause:
        status = "partial"
    else:
        status = "complete"

    return InvestigationReport(
        status=status,
        subject_kind=subject_kind,
        finding_id=subject.get("finding_id"),
        title=subject.get("title"),
        file=subject.get("file"),
        line=subject.get("line"),
        severity=subject.get("severity"),
        severity_measured=bool(subject.get("severity_measured", False)),
        reproduction_status=_REPRODUCTION_STATUS.get(repro.get("status"))
        if reproduction
        else None,
        evidence=evidence,
        root_cause=brief.root_cause or None,
        summary=brief.summary or None,
        root_cause_source=root_cause_source,
        diagnosis_status=brief.diagnosis_status,
        # No evidence means no confidence — not zero confidence. The breakdown
        # is empty in exactly the same case, so the two never disagree.
        confidence=brief.confidence if confidence_components else None,
        confidence_breakdown=[
            ConfidenceComponent(component=name, points=points, basis=basis)
            for name, points, basis in confidence_components
        ],
        completeness=completeness,
        unavailable_sources=unavailable,
        errors=errors,
    )
