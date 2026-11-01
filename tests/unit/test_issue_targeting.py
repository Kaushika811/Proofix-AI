"""A reported issue must keep its selected test and source target."""

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock

from backend.agents.a10_mci_scorer import A10MCIScorerAgent
from backend.agents.a6_fix_dag_planner import A6FixDAGPlannerAgent
from backend.api.routes.ui import CreateRunBody
from backend.config import Settings
from backend.models.blast import BlastGraphResult
from backend.models.root_cause import RootCauseBrief
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.reproduction_parser import parse_pytest_report
from backend.services.issue_targeting import resolved_issue_target
from backend.services.target_resolver import resolve_patch_target
from backend.agents.a7_patch_engine import build_patch_plans
from backend.state.schema import RunStateModel


ISSUE = {
    "description": "An uploaded shell script is accepted as an image",
    "target_test": "tests/test_upload.py::test_reject_shell_script",
    "expected_behavior": "Reject uploads ending in .sh",
    "target_file": "app/uploads.py",
}


def test_api_requires_a_test_for_a_reported_issue():
    with pytest.raises(ValidationError):
        CreateRunBody(url="https://github.com/owner/repo", issue_hint="reject shell scripts")

    body = CreateRunBody(url="https://github.com/owner/repo", issue_hint=ISSUE["description"],
                         target_test=ISSUE["target_test"], expected_behavior=ISSUE["expected_behavior"],
                         target_file=ISSUE["target_file"])
    assert body.issue_contract() == ISSUE
    assert CreateRunBody(url="https://github.com/owner/repo").issue_contract() is None


@pytest.mark.parametrize("test_id", [
    "../outside.py::test_bug", "/tmp/outside.py::test_bug", "-k.py::test_bug",
    "tests/test_ok.py::test_bug;echo_bad",
])
def test_target_test_rejects_unsafe_paths(test_id):
    with pytest.raises(ValidationError):
        CreateRunBody(url="repo", issue_hint="bug", target_test=test_id)


def _report():
    return {
        "exitcode": 1,
        "summary": {"collected": 2, "failed": 1, "passed": 1},
        "tests": [
            {"nodeid": "tests/test_other.py::test_unrelated", "outcome": "failed", "call": {}},
            {"nodeid": ISSUE["target_test"], "outcome": "passed"},
        ],
    }


def test_reproduction_does_not_substitute_an_unrelated_failure(tmp_path):
    result = parse_pytest_report(_report(), 1, "", "", tmp_path / "report.json",
                                 requested_test=ISSUE["target_test"])
    assert result.status.value == "UNCONFIRMED"
    assert result.failing_test is None
    assert result.requested_test == ISSUE["target_test"]

    report = _report()
    report["tests"][1] = {"nodeid": ISSUE["target_test"], "outcome": "failed", "call": {}}
    report["summary"] = {"collected": 2, "failed": 2, "passed": 0}
    confirmed = parse_pytest_report(report, 1, "", "", tmp_path / "report.json",
                                    requested_test=ISSUE["target_test"])
    assert confirmed.status.value == "CONFIRMED"
    assert confirmed.failing_test == ISSUE["target_test"]


def test_explicit_source_file_wins_over_unrelated_scanner_finding(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/uploads.py").write_text("def upload(): pass\n")
    (tmp_path / "app/auth.py").write_text("def auth(): pass\n")
    sig = SemanticIntentGraph(
        repo_path=str(tmp_path), source_roots=["app/"],
        files={path: FileNode(path=path, role="internal-util") for path in
               ("app/uploads.py", "app/auth.py")},
    )
    state = RunStateModel(
        run_id="r", repo_path=str(tmp_path), issue_contract=ISSUE,
        reproduction={"status": "CONFIRMED", "failing_test": ISSUE["target_test"]},
        static_report={"prioritized": [{"id": "f1", "file": "app/auth.py"}]},
    )
    result = resolve_patch_target(tmp_path, state, sig)
    assert result.resolved_application_path == "app/uploads.py"
    assert result.resolution_source == "issue_contract"

    state.issue_contract = {**ISSUE, "target_file": "app/missing.py"}
    assert resolve_patch_target(tmp_path, state, sig).resolved_application_path is None


class _Store:
    async def get_json(self, *_args):
        return None


@pytest.mark.asyncio
async def test_repair_plan_contains_only_the_reported_issue(monkeypatch):
    agent = A6FixDAGPlannerAgent(_Store(), Settings(stub_mode=True))

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(agent, "emit_status", noop)
    state = RunStateModel(
        run_id="r", repo_path="/repo", issue_contract=ISSUE,
        reproduction={"status": "CONFIRMED", "failing_test": ISSUE["target_test"]},
        static_report={"prioritized": [{"id": "f1", "file": "app/auth.py"}]},
        blast_graph={"auto_patch_scope": ["app/uploads.py", "app/auth.py"],
                     "target_resolution": {"resolved_path": "app/uploads.py", "source": "issue_contract"}},
    )
    assert resolved_issue_target(state) == "app/uploads.py"
    planned = await agent.run(state)
    assert len(planned.fix_dag["nodes"]) == 1
    assert planned.fix_dag["nodes"][0]["files"] == ["app/uploads.py"]
    assert planned.fix_dag["execution_order"][0].startswith("reported-")

    state.reproduction = {"status": "UNCONFIRMED"}
    assert resolved_issue_target(state) is None
    assert (await agent.run(state)).fix_dag["nodes"] == []

    state.reproduction = {"status": "CONFIRMED", "failing_test": ISSUE["target_test"]}
    state.blast_graph["target_resolution"]["source"] = "fallback"
    assert resolved_issue_target(state) is None
    assert (await agent.run(state)).fix_dag["nodes"] == []


def test_patch_plan_carries_reported_expectation_as_unverified_input():
    plan = build_patch_plans(
        ["app/uploads.py"], RootCauseBrief(summary="validation missing"),
        BlastGraphResult(auto_patch_scope=["app/uploads.py"]), None,
        reproduction={"status": "CONFIRMED", "failing_test": ISSUE["target_test"]},
        issue_contract=ISSUE,
    )[0]
    assert "Developer-reported expectation (not yet verified)" in plan.expected_behavior
    assert ISSUE["expected_behavior"] in plan.expected_behavior
    assert any(ISSUE["target_test"] in goal for goal in plan.validation_goals)


@pytest.mark.asyncio
async def test_targeted_issue_without_a_patch_does_not_publish_pr(monkeypatch):
    class Store:
        set_json = AsyncMock()

    agent = A10MCIScorerAgent(Store(), Settings(stub_mode=True, github_dry_run=True))
    agent.emit_status = AsyncMock()

    def unexpected_publish(*_args, **_kwargs):
        raise AssertionError("an empty targeted repair must not publish a PR")

    monkeypatch.setattr("backend.agents.a10_mci_scorer.GitHubPRService.publish_fix_with_proof",
                        unexpected_publish)
    result = await agent.run(RunStateModel(
        run_id="r", repo_path="/repo", issue_contract=ISSUE,
        reproduction={"status": "UNCONFIRMED", "requested_test": ISSUE["target_test"]},
        patch_bundle={"patches": [], "diff_text": ""},
    ))
    assert result.pr_decision["pr_url"] is None
    assert result.pr_decision["pr_type"] == "draft"
    assert "No patch generated" in result.pr_decision["review_note"]
