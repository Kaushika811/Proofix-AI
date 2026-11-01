"""Offline, seeded benchmark of source anchoring and narrow claim corroboration."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from backend.agents.a4_evidence_investigator import A4EvidenceInvestigatorAgent
from backend.config import Settings
from backend.models.root_cause import Citation, RootCauseBrief
from backend.orchestrator.edges import should_reinvestigate
from backend.orchestrator.trust_gating import draft_reasons
from backend.services.acceptance_test_integrity import pin_acceptance_test
from backend.services.citation_verifier import verify_citation
from backend.services.reproduction_parser import load_pytest_report, parse_pytest_report
from backend.services.subprocess_runner import PYTHON
from backend.services.ui_projection import build_investigation, build_run_report
from backend.state.schema import RunStateModel

NODEID = "tests/test_auth.py::test_reported_issue"

CASES = (
    ("supported", "auth.py", "validate_token(-1) returns True", True, True,
     "supported", True),
    ("false_claim_valid_citation", "auth.py", "validate_token(-1) returns False", True,
     True, "contradicted", True),
    ("irrelevant_citation", "helper.py", "helper(-1) returns True", True, True,
     "incomplete", True),
    ("conflicting_runtime", "auth.py", "validate_token(-1) returns True", True, False,
     "incomplete", True),
    ("unavailable_execution", "auth.py", "validate_token(-1) returns True", True, True,
     "unavailable", False),
    ("incomplete_investigation", "auth.py", "validate_token(-1) returns True", True,
     True, "incomplete", True),
    ("unstructured_false_claim", "auth.py",
     "validate_token(-1) deletes all customer records", True, True, "incomplete", True),
    ("missing_source", "missing.py", "validate_token(-1) returns True", False, True,
     "incomplete", True),
)


def _run_target(repo: Path) -> dict:
    report_path = repo / "pytest-report.json"
    command = [PYTHON, "-m", "pytest", NODEID, "-q", "--tb=short",
               "--json-report", f"--json-report-file={report_path}"]
    completed = subprocess.run(command, cwd=repo, capture_output=True, text=True,
                               timeout=30, check=False)
    report = load_pytest_report(report_path)
    result = parse_pytest_report(report, completed.returncode, completed.stdout,
                                 completed.stderr, report_path, repo_root=repo,
                                 requested_test=NODEID)
    return result.model_dump(mode="json")


async def _run_investigation(state: RunStateModel, claim: str, cited_file: str) -> RunStateModel:
    store = MagicMock()
    store.append_event = AsyncMock()
    agent = A4EvidenceInvestigatorAgent(store, Settings(stub_mode=True))
    agent._stub_brief = MagicMock(return_value=RootCauseBrief(
        summary=claim, root_cause=claim,
        citations=[Citation(file=cited_file, line=1, claim=claim)],
    ))
    return await agent.run(state)


def run_case(row: tuple) -> dict:
    name, cited_file, claim, expected_location, original_return, expected_claim, execute = row
    with tempfile.TemporaryDirectory(prefix="proofix-verification-") as directory:
        repo = Path(directory)
        (repo / "tests").mkdir()
        auth_path = repo / "auth.py"
        test_path = repo / "tests" / "test_auth.py"
        auth_path.write_text(
            f"def validate_token(exp):\n    return {str(original_return)}\n", encoding="utf-8"
        )
        (repo / "helper.py").write_text("def helper(exp):\n    return True\n", encoding="utf-8")
        test_path.write_text(
            "from auth import validate_token\n\n"
            "def test_reported_issue():\n"
            + ("    assert validate_token(-1) is True\n" if name == "conflicting_runtime"
               else "    assert validate_token(-1) is False\n"),
            encoding="utf-8",
        )
        issue = {"description": "Check expired-token behavior", "target_test": NODEID,
                 "expected_behavior": "Expired tokens are rejected"}
        pinned = pin_acceptance_test(repo, NODEID)
        if name == "conflicting_runtime":
            # The report is genuinely measured, but against an earlier source
            # snapshot. Restoring the source creates a source/runtime conflict.
            auth_path.write_text("def validate_token(exp):\n    return False\n", encoding="utf-8")
        reproduction = _run_target(repo) if execute else {
            "status": "INFRA_ERROR", "infra_detail": "controlled execution unavailable"
        }
        if name == "conflicting_runtime":
            auth_path.write_text("def validate_token(exp):\n    return True\n", encoding="utf-8")
        citation, location_method = verify_citation(
            repo, {"file": cited_file, "line": 1, "claim": claim,
                   "claim_support_status": "supported"},
        )
        state = RunStateModel(
            run_id=f"verification-{name}", issue_contract=issue,
            repo_path=str(repo), repo_clone_path=str(repo), reproduction=reproduction,
            acceptance_test=None if name == "incomplete_investigation" else pinned,
            root_cause={"reinvestigation_count": 2}
            if name in {"false_claim_valid_citation", "missing_source"} else None,
        )
        state = asyncio.run(_run_investigation(state, claim, cited_file))
        assessed = (state.root_cause or {})["citations"][0]
        status = (state.root_cause or {})["diagnosis_status"]
        item = next(item for item in (state.investigation or {})["evidence"]
                    if item["id"] == "citation:0")
        route = should_reinvestigate(state.model_dump(mode="json"))
        reason_codes = [reason.code for reason in draft_reasons(state)]
        report_row = next(item for item in build_run_report(state, [])["evidence"]
                          if item["text"] == "Root-cause claims supported")
        projected_investigation = build_investigation(state) or {}
        return {
            "case": name,
            "expected_source_located": expected_location,
            "source_located": citation["source_located"],
            "source_location_method": location_method,
            "expected_claim_status": expected_claim,
            "claim_status": assessed["claim_support_status"],
            "claim_reason": assessed["claim_support_reason"],
            "diagnosis_status": status,
            "projected_diagnosis_status": projected_investigation.get("diagnosisStatus"),
            "evidence_stance": item["stance"],
            "reproduction_status": reproduction.get("status"),
            "patch_route": route,
            "draft_reason_codes": reason_codes,
            "presented_as_confirmed": report_row["ok"],
            "execution_attempted": execute,
        }


def run_benchmark() -> dict:
    cases = [run_case(row) for row in CASES]
    metrics = {
        "cases": len(cases),
        "source_location_correct": sum(
            case["source_located"] == case["expected_source_located"] for case in cases
        ),
        "source_location_true_positives": sum(case["source_located"] for case in cases),
        "claim_classification_correct": sum(
            case["claim_status"] == case["expected_claim_status"] for case in cases
        ),
        "supported": sum(case["claim_status"] == "supported" for case in cases),
        "contradicted": sum(case["claim_status"] == "contradicted" for case in cases),
        "incomplete": sum(case["claim_status"] == "incomplete" for case in cases),
        "unavailable": sum(case["claim_status"] == "unavailable" for case in cases),
        "false_supports": sum(case["presented_as_confirmed"] and
                              case["expected_claim_status"] != "supported" for case in cases),
        "non_supported_as_confirmed": sum(case["claim_status"] != "supported" and
                                          case["presented_as_confirmed"] for case in cases),
        "contradicted_to_patch": sum(case["claim_status"] == "contradicted" and
                                     case["patch_route"] == "blast_scope" for case in cases),
        "hypothesis_patch_routes": sum(case["claim_status"] == "incomplete" and
                                       case["patch_route"] == "blast_scope" for case in cases),
    }
    return {"mode": "controlled_local_verification", "live_model_measured": False,
            "semantic_truth_outside_literal_return_claims": None,
            "independent_repositories_measured": False,
            "cases": cases, "metrics": metrics}


def main() -> None:
    result = run_benchmark()
    output = Path(__file__).with_name("results.json")
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": result["metrics"], "output": str(output)}))


if __name__ == "__main__":
    main()
