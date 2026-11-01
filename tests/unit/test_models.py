
from backend.models.sig import SemanticIntentGraph
from backend.models.cve import CVEReachabilityReport, CVERecord
from backend.models.reproduction import ReproductionResult, ReproductionStatus
from backend.state.schema import RunStateModel


def test_run_state_model():
    state = RunStateModel(run_id="test-123", repo_path="/tmp/sample-repo")
    assert state.status == "pending"
    assert state.retry_count == 0


def test_sig_model():
    sig = SemanticIntentGraph(repo_path="/tmp", files={})
    assert sig.repo_path == "/tmp"


def test_cve_unknown_classification():
    record = CVERecord(
        package="urllib3",
        cve_id="CVE-TEST-001",
        severity="HIGH",
        reachable=None,
        classification="Unknown",
    )
    assert record.classification == "Unknown"


def test_reproduction_force_draft():
    result = ReproductionResult(status=ReproductionStatus.UNCONFIRMED)
    assert result.force_draft_pr is True
    assert result.reproduced is False


def test_reproduction_confirmed():
    result = ReproductionResult(
        status=ReproductionStatus.CONFIRMED,
        failing_test="tests/t.py::test_x",
        exception_type="AssertionError",
    )
    assert result.reproduced is True
    assert result.force_draft_pr is False


def test_cve_report():
    report = CVEReachabilityReport(
        findings=[CVERecord(package="urllib3", cve_id="CVE-1", severity="HIGH", classification="Informational")],
        critical_queue=[],
    )
    assert len(report.findings) == 1
