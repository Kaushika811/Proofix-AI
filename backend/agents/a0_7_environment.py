"""A0.7 — Environment Precheck.

Runs immediately after the repository is cloned and answers one question: **can
this repository's tests actually execute here?**

Before this existed, the answer was discovered by A3.5 failing, after which the
pipeline continued anyway (`graph.py` routed `reproduction_gate → investigate`
unconditionally). Investigation, blast analysis, context engineering, planning,
patch generation and the full retry loop all ran on empty input, spending the
two most expensive LLM calls in the system to produce "Generated 0 patches from
0 plans" four times in eight seconds. The user was then shown seven completed
stages and a set of zeroed axis scores — a composite that read as "we analysed
your repository and it scored badly", when the truth was "we could not run your
test suite".

This agent makes that truth a first-class, early, structured result.

**It never installs anything and never runs `suggested_command`.** Installing
from a cloned repository executes that repository's build hooks on the host, and
the pipeline's subprocesses are not sandboxed. The command is published for a
human to run.
"""

from pathlib import Path

from backend.agents.base import AgentBase
from backend.services.environment_probe import probe_environment
from backend.state.schema import RunStateModel


class A07EnvironmentAgent(AgentBase):
    agent_id = "A0.7"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Checking whether the test suite can run")

        repo = Path(state.repo_clone_path or state.repo_path).resolve()
        report = await probe_environment(repo, timeout=self.settings.environment_probe_timeout)

        state.environment = report.model_dump(mode="json")
        await self.store.save_state(state)

        # The message is the report's own `reason`, not a template. The UI
        # renders it verbatim, so a wrong word here is a wrong word on screen.
        message = (
            f"Environment ready — {report.test_runner} is available"
            if report.status == "ready"
            else report.reason
        )

        await self.emit_status(
            state,
            "completed" if report.status == "ready" else "failed",
            message,
            {
                "status": report.status,
                "language": report.language,
                "blocking": report.blocking,
                "testRunner": report.test_runner,
                "testRunnerAvailable": report.test_runner_available,
                "missingImports": report.missing_imports,
                "testsCollected": report.tests_collected,
                "manifests": [m.model_dump(mode="json") for m in report.manifests],
                "suggestedCommand": report.suggested_command,
                "reason": report.reason,
            },
        )
        return state
