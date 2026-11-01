from datetime import datetime
from pathlib import Path

from backend.agents.base import AgentBase
from backend.services.acceptance_test_integrity import (
    acceptance_test_integrity_error, pin_acceptance_test,
)
from backend.models.reproduction import ReproductionResult, ReproductionStatus
from backend.services.issue_test_generation import (
    generate_issue_test, generated_test_integrity_error, generated_test_replay_command,
    run_with_fresh_bytecode,
)
from backend.services.reproduction_commands import build_reproduction_command
from backend.services.reproduction_parser import (
    extract_failed_nodeids,
    load_pytest_report,
    parse_pytest_report,
    pytest_report_path,
)
from backend.services.subprocess_runner import PYTHON, run_command
from backend.state.schema import RunStateModel

_STATUS_MESSAGES = {
    ReproductionStatus.CONFIRMED: "Reproduction confirmed",
    ReproductionStatus.UNCONFIRMED: "Reproduction NOT confirmed",
    ReproductionStatus.INFRA_ERROR: "Reproduction infrastructure error",
    ReproductionStatus.NO_TESTS: "No tests available for reproduction",
}


class A35ReproductionAgent(AgentBase):
    agent_id = "A3.5"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Running reproduction gate via pytest")
        repo = Path(state.repo_clone_path or state.repo_path).resolve()
        requested_test = (state.issue_contract or {}).get("target_test")
        if state.issue_contract and not requested_test:
            await self.emit_status(state, "progress", "Generating a regression test from the reported expectation")
            state.generated_test = state.generated_test or await generate_issue_test(state, repo, self.settings)
            if state.generated_test.get("status") != "generated":
                result = ReproductionResult(
                    status=ReproductionStatus.UNCONFIRMED,
                    infra_detail=state.generated_test.get("reason"),
                )
                state.reproduction = result.model_dump(mode="json")
                await self.emit_status(state, "completed", "No executable regression test generated", state.generated_test)
                return state
            requested_test = state.generated_test["nodeid"]
            state.issue_contract = {**state.issue_contract, "target_test": requested_test}
        integrity_error = generated_test_integrity_error(repo, state.generated_test)
        if integrity_error:
            state.reproduction = ReproductionResult(
                status=ReproductionStatus.UNCONFIRMED, requested_test=requested_test,
                infra_detail=integrity_error,
            ).model_dump(mode="json")
            await self.emit_status(state, "completed", integrity_error)
            return state
        if requested_test:
            target_file = (repo / requested_test.split("::", 1)[0]).resolve()
            if not target_file.is_relative_to(repo) or not target_file.is_file():
                result = ReproductionResult(
                    status=ReproductionStatus.NO_TESTS,
                    requested_test=requested_test,
                    infra_detail="Requested test file is absent or outside the repository",
                )
                state.reproduction = result.model_dump(mode="json")
                await self.emit_status(state, "completed", _STATUS_MESSAGES[result.status], {
                    "status": result.status.value,
                    "requested_test": requested_test,
                    "reason": result.infra_detail,
                })
                return state
            state.acceptance_test = pin_acceptance_test(repo, requested_test)
        report_path = pytest_report_path(state.run_id)

        if report_path.exists():
            report_path.unlink()

        cmd = [
            PYTHON,
            "-m",
            "pytest",
            "--tb=long",
            "--json-report",
            f"--json-report-file={report_path}",
            "-v",
        ]
        started_at = datetime.utcnow()
        code, stdout, stderr = await run_command(cmd, cwd=repo, timeout=120)
        finished_at = datetime.utcnow()

        report = load_pytest_report(report_path)
        baseline_failures = extract_failed_nodeids(report)
        executed_cmd = "python -m pytest --tb=long --json-report -v"
        baseline_available = report is not None and code in (0, 1, 5)
        if state.generated_test and state.generated_test.get("status") == "generated":
            # Explicit selection also works when pytest's testpaths/python_files
            # would exclude the newly added test from default collection.
            report_path = report_path.with_name(report_path.stem + "_generated.json")
            report_path.unlink(missing_ok=True)
            cmd = [PYTHON, "-m", "pytest", requested_test, "--tb=long", "--json-report",
                   f"--json-report-file={report_path}", "-v"]
            started_at = datetime.utcnow()
            code, stdout, stderr = await run_with_fresh_bytecode(cmd, cwd=repo, timeout=120)
            finished_at = datetime.utcnow()
            report = load_pytest_report(report_path)
            executed_cmd = build_reproduction_command(requested_test)[0] + " --json-report"
        result = parse_pytest_report(
            report, code, stdout, stderr, report_path, repo_root=repo,
            requested_test=requested_test,
        )
        acceptance_error = acceptance_test_integrity_error(repo, state.acceptance_test)
        if acceptance_error:
            result = ReproductionResult(
                status=ReproductionStatus.UNCONFIRMED, requested_test=requested_test,
                infra_detail=acceptance_error, report_path=str(report_path),
                stdout=stdout, stderr=stderr, exit_code=code,
            )
        if state.generated_test and state.generated_test.get("status") == "generated":
            selected = next((t for t in (report or {}).get("tests", []) if t.get("nodeid") == requested_test), {})
            call = selected.get("call") or {}
            crash_message = (call.get("crash") or {}).get("message", "")
            assertion_failure = crash_message.startswith(("assert ", "AssertionError", "Failed: DID NOT RAISE"))
            reason = generated_test_integrity_error(repo, state.generated_test)
            if not baseline_available:
                reason = "Baseline test suite could not be measured"
            elif result.status == ReproductionStatus.CONFIRMED and (
                code != 1 or call.get("outcome") != "failed" or not assertion_failure
            ):
                reason = "Generated test did not produce a behavioral assertion failure; review its imports, fixtures, and API usage"
            if reason:
                result = ReproductionResult(
                    status=ReproductionStatus.UNCONFIRMED, requested_test=requested_test,
                    infra_detail=reason, report_path=str(report_path), stdout=stdout, stderr=stderr, exit_code=code,
                )
            state.generated_test.update(before_status=result.status.value, before_exit_code=code,
                                        reason=result.infra_detail)
        result.pre_existing_failures = (
            [test for test in baseline_failures if test != requested_test]
            if state.generated_test else baseline_failures
        )
        # The command that actually produced this evidence — every element
        # literal, no shell interpolation, safe to show and copy verbatim.
        result.command = executed_cmd
        result.started_at = started_at.isoformat()
        result.finished_at = finished_at.isoformat()
        if result.duration_seconds is None:
            result.duration_seconds = (finished_at - started_at).total_seconds()

        reexec_cmd, is_targeted, reexec_timeout = build_reproduction_command(result.failing_test)
        result.reexecution_command = reexec_cmd
        if state.generated_test and state.generated_test.get("status") == "generated":
            result.reexecution_command = generated_test_replay_command(state.generated_test)
        result.reexecution_is_targeted = is_targeted
        result.reexecution_timeout_seconds = reexec_timeout

        result_dict = result.model_dump(mode="json")
        state.reproduction = result_dict
        # `state.force_draft_pr` is deliberately not set here. A3.5 reports what
        # it observed — the status is in `result` — and `trust_gating` derives
        # the routing consequence from it. See that module's docstring for why
        # three writers of one flag had no single moment at which it was true.

        await self.emit_status(
            state,
            "completed",
            _STATUS_MESSAGES.get(result.status, str(result.status)),
            {
                "status": result.status.value,
                "reproduced": result.reproduced,
                "force_draft_pr": result.force_draft_pr,
                "failing_test": result.failing_test,
                "requested_test": requested_test,
                "report_path": result.report_path,
            },
        )
        return state
