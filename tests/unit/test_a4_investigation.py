"""A4's evidence investigation — `services/evidence_investigation.py`.

The governing rule these tests enforce is the one the whole workspace is built
on: absent data is reported as absent. A scanner that could not run, a
reproduction that never happened and a severity nobody assigned must each come
back as `unavailable` / `None`, never as a default that reads like a
measurement. Several tests below exist purely to fail if a fabricated default
is ever introduced.
"""

import pytest

from backend.models.investigation import InvestigationReport
from backend.models.root_cause import Citation, EvidenceReference, RootCauseBrief
from backend.services.evidence_investigation import build_investigation_report
from backend.services.root_cause_builder import (
    compute_confidence,
    compute_confidence_breakdown,
)


def _brief(**overrides) -> RootCauseBrief:
    base = {
        "summary": "Expired tokens are accepted",
        "root_cause": "validate_token never compares exp against the clock",
        "citations": [Citation(file="app/auth.py", line=42, claim="no exp check", verified=True)],
        "evidence_refs": [
            EvidenceReference(
                source="runtime",
                ref_id="test_expired",
                file="app/auth.py",
                line=42,
                claim="AssertionError",
                weight=0.35,
            )
        ],
        "confidence": 0.6,
    }
    base.update(overrides)
    return RootCauseBrief(**base)


def _static(**overrides) -> dict:
    base = {
        "raw_count": 3,
        "scanner_status": {"bandit": "ok", "semgrep": "ok_no_findings", "ruff": "unavailable"},
        "prioritized": [
            {
                "id": "finding-0",
                "file": "app/auth.py",
                "line": 40,
                "message": "hardcoded comparison",
                "tools": ["bandit"],
                "severity": 0.9,
                "severity_measured": True,
            }
        ],
    }
    base.update(overrides)
    return base


def _reproduction(**overrides) -> dict:
    base = {
        "status": "CONFIRMED",
        "failing_test": "tests/test_auth.py::test_expired",
        "exception_type": "AssertionError",
        "exception_message": "expired token accepted",
        "failing_file": "app/auth.py",
        "failing_line": 42,
        "confidence": 0.9,
        "evidence_source": "pytest_report",
        "command": "pytest -q",
        "exit_code": 1,
        "tests_collected": 12,
        "tests_passed": 11,
        "tests_failed": 1,
    }
    base.update(overrides)
    return base


def _cve(**overrides) -> dict:
    base = {
        "manifest": "requirements.txt",
        "total_dependencies": 8,
        "findings": [
            {
                "cve_id": "CVE-2024-0001",
                "package": "pyjwt",
                "installed_version": "1.0.0",
                "severity": "HIGH",
                "classification": "Critical",
                "reach_path": ["app/auth.py"],
            }
        ],
    }
    base.update(overrides)
    return base


def _build(**overrides) -> InvestigationReport:
    kwargs = {
        "brief": _brief(),
        "static_report": _static(),
        "reproduction": _reproduction(),
        "cve_report": _cve(),
        "confidence_components": [("runtime evidence", 0.35, "1 runtime reference")],
        "root_cause_source": "deterministic",
        "errors": None,
    }
    kwargs.update(overrides)
    return build_investigation_report(**kwargs)


def _item(report: InvestigationReport, item_id: str):
    return next(e for e in report.evidence if e.id == item_id)


# ------------------------------------------------------------ 1. fully populated


def test_fully_populated_investigation_reports_every_category():
    report = _build()

    assert report.status == "partial"  # ruff is unavailable — honestly reported
    assert report.subject_kind == "runtime_failure"
    assert report.finding_id == "tests/test_auth.py::test_expired"
    assert report.title == "AssertionError: expired token accepted"
    assert report.file == "app/auth.py"
    assert report.line == 42
    assert report.root_cause == "validate_token never compares exp against the clock"
    assert report.root_cause_source == "deterministic"

    categories = {e.category for e in report.evidence}
    assert categories == {"scanner", "reproduction", "source", "dependency"}


# ---------------------------------------------------------------- 2. reproduced


def test_reproduced_failure_is_the_subject_and_supporting_evidence():
    report = _build()

    assert report.reproduction_status == "reproduced"
    repro = _item(report, "reproduction")
    assert repro.status == "present"
    assert repro.stance == "supporting"
    # A3.5's own confidence, carried through — not a value assigned here.
    assert repro.strength == 0.9
    assert repro.detail["command"] == "pytest -q"
    assert repro.detail["exitCode"] == 1


def test_runtime_subject_has_no_severity_because_no_tool_assigned_one():
    report = _build()

    assert report.severity is None
    assert report.severity_measured is False


# ------------------------------------------------------------ 3. not reproduced


def test_passing_suite_does_not_contradict_a_static_finding():
    """A3.5 runs the whole suite and does not target a finding.

    Reading a green suite as evidence against A3's finding would be exactly the
    "absence of evidence is evidence of absence" mistake.
    """
    report = _build(reproduction=_reproduction(status="UNCONFIRMED", failing_test=None))

    assert report.subject_kind == "static_finding"
    assert report.finding_id == "finding-0"
    assert report.reproduction_status == "not_reproduced"

    repro = _item(report, "reproduction")
    assert repro.status == "absent"
    assert repro.stance == "neutral"
    assert "neither confirms nor refutes" in repro.description
    assert report.contradicting == []


# ------------------------------------------------------- 4. reproduction absent


def test_no_tests_is_unavailable_not_a_negative_result():
    report = _build(reproduction=_reproduction(status="NO_TESTS"))

    assert report.reproduction_status == "unavailable"
    repro = _item(report, "reproduction")
    assert repro.status == "unavailable"
    assert repro.stance == "neutral"
    assert repro.strength is None
    assert any("A3.5" in u.source for u in report.unavailable_sources)


def test_infra_error_is_reported_as_an_execution_error():
    report = _build(
        reproduction=_reproduction(
            status="INFRA_ERROR", infra_detail="pytest could not be executed"
        )
    )

    assert report.reproduction_status == "error"
    repro = _item(report, "reproduction")
    assert repro.status == "error"
    assert repro.description == "pytest could not be executed"
    assert report.status == "partial"


# --------------------------------------------------------- 5. scanner outcomes


def test_scanner_outcomes_map_to_distinct_evidence_states():
    report = _build()

    bandit = _item(report, "scanner:bandit")
    assert bandit.status == "present"
    assert bandit.stance == "supporting"
    assert bandit.strength == 0.9
    assert bandit.strength_basis is not None

    semgrep = _item(report, "scanner:semgrep")
    assert semgrep.status == "absent"
    assert semgrep.stance == "neutral"  # ran clean ≠ argues against the finding
    assert semgrep.strength is None

    ruff = _item(report, "scanner:ruff")
    assert ruff.status == "unavailable"
    assert ruff.stance == "neutral"
    assert any(u.source == "ruff" for u in report.unavailable_sources)


def test_stubbed_scanner_is_unavailable_not_a_measurement():
    """A3 substitutes a heuristic scan when a tool is absent.

    Those findings carry the tool's name but are not the tool's measurement, so
    the scanner must not be presented as having reported them.
    """
    report = _build(
        static_report=_static(
            scanner_status={"bandit": "stubbed", "semgrep": "ok_no_findings", "ruff": "ok"}
        )
    )

    bandit = _item(report, "scanner:bandit")
    assert bandit.status == "unavailable"
    assert bandit.strength is None
    assert "heuristic" in bandit.description


def test_unmeasured_severity_never_becomes_a_strength():
    """semgrep and ruff assign a constant severity; A3 flags that.

    A constant is not a measurement, so it must not surface as an evidence
    strength.
    """
    report = _build(
        reproduction=_reproduction(status="UNCONFIRMED"),
        static_report=_static(
            scanner_status={"ruff": "ok"},
            prioritized=[
                {
                    "id": "finding-0",
                    "file": "app/auth.py",
                    "line": 3,
                    "message": "unused import",
                    "tools": ["ruff"],
                    "severity": 0.4,
                    "severity_measured": False,
                }
            ],
        ),
    )

    assert report.severity == 0.4
    assert report.severity_measured is False
    ruff = _item(report, "scanner:ruff")
    assert ruff.status == "present"
    assert ruff.strength is None


# ------------------------------------------------------------- 6. missing A3


def test_missing_static_report_is_unavailable_not_zero_findings():
    report = _build(static_report=None)

    scanner = _item(report, "scanner")
    assert scanner.status == "unavailable"
    assert scanner.stance == "neutral"
    assert report.completeness.category_status["scanner"] == "unavailable"
    assert any("A3" in u.source for u in report.unavailable_sources)


def test_static_report_without_scanner_status_is_unavailable():
    report = _build(static_report={"raw_count": 0, "prioritized": []})

    scanner = _item(report, "scanner")
    assert scanner.status == "unavailable"


# ------------------------------------------------------------ 7. missing A3.5


def test_missing_reproduction_leaves_status_none_not_a_verdict():
    report = _build(reproduction=None)

    assert report.reproduction_status is None
    repro = _item(report, "reproduction")
    assert repro.status == "unavailable"
    assert report.subject_kind == "static_finding"


# --------------------------------------------------------------- 8. partial


def test_partial_evidence_reports_coverage_honestly():
    report = _build(static_report=None, cve_report=None)

    assert report.status == "partial"
    assert report.completeness.total_categories == 4
    assert report.completeness.measured_categories == 2  # reproduction + source
    assert report.completeness.ratio == 0.5
    assert report.completeness.category_status["dependency"] == "unavailable"


def test_complete_only_when_every_source_answered():
    report = _build(
        static_report=_static(
            scanner_status={"bandit": "ok", "semgrep": "ok_no_findings", "ruff": "ok"}
        )
    )

    assert report.unavailable_sources == []
    assert report.status == "complete"
    assert report.completeness.ratio == 1.0


# --------------------------------------------------------- 9. contradicting


def test_unverified_citation_is_the_real_contradicting_evidence():
    report = _build(
        brief=_brief(
            citations=[
                Citation(file="app/auth.py", line=42, claim="no exp check", verified=True),
                Citation(file="app/ghost.py", line=9, claim="phantom claim", verified=False),
            ]
        )
    )

    contradicting = report.contradicting
    assert len(contradicting) == 1
    assert contradicting[0].source == "app/ghost.py:9"
    assert contradicting[0].status == "absent"
    # There is no source viewer behind a citation — the clone is gone by the
    # time anyone reads this — and the report says so rather than implying one.
    assert contradicting[0].detail["sourceAvailable"] is False


def test_no_contradicting_evidence_is_an_empty_list_not_an_invented_item():
    report = _build()
    assert report.contradicting == []
    assert len(report.supporting) >= 1


# -------------------------------------------------------------- 10. empty


def test_no_subject_at_all_is_reported_as_no_finding():
    report = _build(
        brief=RootCauseBrief(),
        static_report=_static(prioritized=[], scanner_status={"bandit": "ok_no_findings"}),
        reproduction=_reproduction(status="UNCONFIRMED"),
        cve_report={"manifest": "requirements.txt", "total_dependencies": 0, "findings": []},
        confidence_components=[],
    )

    assert report.status == "no_finding"
    assert report.subject_kind is None
    assert report.finding_id is None
    assert report.title is None
    assert report.file is None
    assert report.severity is None
    assert report.confidence is None
    assert report.root_cause is None

    source = _item(report, "source")
    assert source.status == "absent"
    assert source.stance == "neutral"


# --------------------------------------------------------------- 11. error


def test_investigation_error_is_surfaced_not_swallowed():
    report = _build(errors=["LLM investigation unavailable (TimeoutError: boom)"])

    assert report.status == "error"
    assert report.errors == ["LLM investigation unavailable (TimeoutError: boom)"]
    # A degraded investigation still reports the evidence it did gather.
    assert report.evidence


def test_llm_provenance_is_recorded_when_the_llm_produced_the_brief():
    assert _build(root_cause_source="llm").root_cause_source == "llm"
    assert _build(root_cause_source="deterministic").root_cause_source == "deterministic"


# ----------------------------------------------------------- 12. confidence


def test_confidence_breakdown_sums_to_the_published_confidence():
    refs = [
        EvidenceReference(source="runtime", ref_id="t", claim="x", weight=0.35),
        EvidenceReference(source="finding", ref_id="f", claim="y", weight=0.15),
        EvidenceReference(source="stack_trace", ref_id="s", claim="z", weight=0.15),
    ]
    total, components = compute_confidence_breakdown(refs, 1, {"status": "CONFIRMED"})

    assert total == compute_confidence(refs, 1, {"status": "CONFIRMED"})
    # The published number is the ledger's sum, capped at 1.0 — so the cap is
    # the only thing that may ever separate them.
    assert total == min(1.0, round(sum(c[1] for c in components), 3))
    names = {c[0] for c in components}
    assert "verified citations" in names
    assert "source diversity" in names
    assert "runtime confirmation" in names


def test_confidence_breakdown_sums_exactly_below_the_cap():
    refs = [EvidenceReference(source="finding", ref_id="f", claim="y", weight=0.15)]
    total, components = compute_confidence_breakdown(refs, 1, {})

    assert pytest.approx(sum(c[1] for c in components), abs=1e-9) == total
    assert total == 0.4  # 0.15 finding + 0.25 verified citation, nothing else


def test_confidence_breakdown_is_empty_without_evidence():
    total, components = compute_confidence_breakdown([], 0, {})
    assert total == 0.0
    assert components == []


def test_confidence_components_reach_the_report():
    report = _build(
        confidence_components=[
            ("runtime evidence", 0.35, "1 runtime reference(s) at 0.35 each"),
            ("verified citations", 0.25, "1 citation(s) anchored to real source"),
        ]
    )

    assert report.confidence == 0.6
    assert [c.component for c in report.confidence_breakdown] == [
        "runtime evidence",
        "verified citations",
    ]
    assert all(c.basis for c in report.confidence_breakdown)


def test_confidence_is_none_when_nothing_was_scored():
    """No evidence means no confidence — not 0% likely."""
    report = _build(confidence_components=[], brief=_brief(confidence=0.0))
    assert report.confidence is None


# ------------------------------------------------- 13. no fabricated defaults


def test_no_fabricated_defaults_anywhere_in_an_empty_investigation():
    report = build_investigation_report(
        brief=RootCauseBrief(),
        static_report=None,
        reproduction=None,
        cve_report=None,
        confidence_components=[],
        root_cause_source=None,
    )

    assert report.status == "no_finding"
    assert (report.confidence, report.severity, report.line, report.file) == (
        None,
        None,
        None,
        None,
    )
    assert report.root_cause is None and report.summary is None
    assert report.root_cause_source is None
    assert report.reproduction_status is None
    # "A4 produced no citations" is a conclusion A4 reached, so `source`
    # counts as measured; the three upstreams that never ran do not.
    assert report.completeness.measured_categories == 1
    assert report.completeness.ratio == 0.25
    assert report.completeness.category_status == {
        "scanner": "unavailable",
        "reproduction": "unavailable",
        "source": "absent",
        "dependency": "unavailable",
    }
    assert {e.status for e in report.evidence} <= {"unavailable", "absent"}
    assert all(e.stance == "neutral" for e in report.evidence)
    assert all(e.strength is None for e in report.evidence)


def test_dependency_evidence_only_supports_when_a4_actually_used_the_advisory():
    used = _build(brief=_brief(cve_context=["CVE-2024-0001"]))
    assert _item(used, "cve:CVE-2024-0001").stance == "supporting"

    unused = _build(brief=_brief(cve_context=[]))
    assert _item(unused, "cve:CVE-2024-0001").stance == "neutral"


def test_unreachable_advisories_are_absent_not_contradicting():
    report = _build(
        cve_report=_cve(
            findings=[
                {
                    "cve_id": "CVE-2024-0002",
                    "package": "requests",
                    "classification": "Informational",
                }
            ]
        )
    )

    dependency = _item(report, "dependency")
    assert dependency.status == "absent"
    assert dependency.stance == "neutral"
    assert dependency.detail["advisories"] == 1
