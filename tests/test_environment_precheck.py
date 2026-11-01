"""Environment precheck: probe classification and the pipeline gate.

Two things under test:

  1. The probe answers "can this repository's tests run here" — and answers it
     from whether the runner actually starts, not from whether a manifest
     exists. Every GitHub repository that failed before had a perfectly good
     `pyproject.toml`; what it lacked was an installed `pytest`.
  2. A blocking verdict stops reproduction and everything downstream, while a
     non-blocking or absent verdict leaves the pipeline exactly as it was.
"""

import pytest

from backend.models.environment import DetectedManifest, EnvironmentReport
from backend.orchestrator.edges import after_environment
from backend.services.environment_probe import (
    detect_manifests,
    primary_language,
    python_manifests,
)


class TestManifestDetection:
    def test_finds_root_python_manifests(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        (tmp_path / "requirements.txt").write_text("pytest\n")

        found = detect_manifests(tmp_path)
        kinds = {m.kind for m in found}

        assert "pyproject.toml" in kinds
        assert "requirements.txt" in kinds
        assert all(m.language == "python" for m in found)

    def test_finds_one_level_down(self, tmp_path):
        pkg = tmp_path / "packages"
        pkg.mkdir()
        (pkg / "Cargo.toml").write_text("[package]\n")

        found = detect_manifests(tmp_path)
        assert any(m.path == "packages/Cargo.toml" for m in found)

    def test_skips_vendored_directories(self, tmp_path):
        vendored = tmp_path / "node_modules"
        vendored.mkdir()
        (vendored / "package.json").write_text("{}")

        assert detect_manifests(tmp_path) == []

    def test_empty_repository(self, tmp_path):
        assert detect_manifests(tmp_path) == []

    def test_root_manifest_decides_language(self, tmp_path):
        """A Python project with a JS frontend is still a Python project."""
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text("{}")

        assert primary_language(detect_manifests(tmp_path)) == "python"

    def test_language_of_non_python_root(self, tmp_path):
        (tmp_path / "go.mod").write_text("module x\n")
        assert primary_language(detect_manifests(tmp_path)) == "go"

    def test_python_is_found_when_no_manifest_sits_at_the_root(self, tmp_path):
        """`frontend/package.json` + `backend/requirements.txt`, nothing at the root.

        `primary_language` calls this node — both manifests are nested, so the
        tie breaks on manifest priority. The repository is still repairable, and
        the decision must not be made from that label.
        """
        for directory, manifest, body in (
            ("frontend", "package.json", "{}"),
            ("backend", "requirements.txt", "fastapi\n"),
        ):
            (tmp_path / directory).mkdir()
            (tmp_path / directory / manifest).write_text(body)

        found = detect_manifests(tmp_path)
        assert primary_language(found) == "node"
        assert [m.path for m in python_manifests(found)] == ["backend/requirements.txt"]

    def test_root_python_manifest_outranks_a_nested_one(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("pytest\n")
        (tmp_path / "services").mkdir()
        (tmp_path / "services" / "pyproject.toml").write_text("[project]\n")

        assert python_manifests(detect_manifests(tmp_path))[0].path == "requirements.txt"


class TestProbeClassification:
    @pytest.mark.asyncio
    async def test_no_manifest_blocks(self, tmp_path):
        from backend.services.environment_probe import probe_environment

        report = await probe_environment(tmp_path)
        assert report.status == "no_manifest"
        assert report.blocking is True
        assert report.suggested_command is None
        assert report.reason  # must explain itself

    @pytest.mark.asyncio
    async def test_unsupported_language_blocks_and_names_itself(self, tmp_path):
        from backend.services.environment_probe import probe_environment

        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
        report = await probe_environment(tmp_path)

        assert report.status == "unsupported"
        assert report.language == "rust"
        assert report.blocking is True
        assert "rust" in report.reason.lower()
        # Never suggests a command for a language it cannot drive.
        assert report.suggested_command is None

    @pytest.mark.asyncio
    async def test_nested_python_is_not_rejected_as_unsupported(self, tmp_path):
        """The `llm-shield` shape: JS frontend, Python backend, bare root.

        This was reported as "This is a node project" and halted the run, while
        A1 went on to index `backend/` and build a SIG from it — the pipeline
        declining to look at Python it could already see.
        """
        from backend.services.environment_probe import probe_environment

        (tmp_path / "frontend").mkdir()
        (tmp_path / "frontend" / "package.json").write_text("{}")
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")

        report = await probe_environment(tmp_path)

        assert report.status != "unsupported"
        assert report.language == "python"
        # The command names the Python manifest, not the JS one beside it.
        if report.suggested_command:
            assert report.suggested_command == "pip install -r backend/requirements.txt"
        # `reason` is rendered verbatim; Python 3.14 colours its tracebacks.
        assert "\x1b[" not in report.reason


class TestSuggestedCommandIsNeverExecuted:
    def test_report_carries_command_as_data(self):
        """The command is a string for a human. Nothing in the model runs it."""
        report = EnvironmentReport(
            status="not_prepared",
            language="python",
            manifests=[DetectedManifest(path="pyproject.toml", kind="pyproject.toml", language="python")],
            test_runner="pytest",
            test_runner_available=False,
            missing_imports=["pytest"],
            reason="deps not installed",
            suggested_command='pip install -e ".[dev]"',
            blocking=True,
        )
        assert report.suggested_command == 'pip install -e ".[dev]"'
        assert isinstance(report.suggested_command, str)


class TestPipelineGate:
    """`after_environment` decides whether reproduction runs at all."""

    def test_blocking_verdict_halts(self):
        state = {"environment": {"blocking": True, "status": "not_prepared"}}
        assert after_environment(state) == "halt_environment"

    def test_ready_verdict_proceeds(self):
        state = {"environment": {"blocking": False, "status": "ready"}}
        assert after_environment(state) == "reproduction_gate"

    def test_absent_environment_proceeds(self):
        """Precheck disabled, or the probe itself errored."""
        assert after_environment({}) == "reproduction_gate"
        assert after_environment({"environment": None}) == "reproduction_gate"

    def test_a_probe_that_could_not_conclude_does_not_block(self):
        """A diagnostic must never become a new failure mode."""
        state = {"environment": {"status": "no_test_runner"}}  # no `blocking` key
        assert after_environment(state) == "reproduction_gate"


class TestCurrentAgentOnHalt:
    """B-B20: `current_agent` must name the agent that actually stopped the
    run, not whatever ran last in `parallel_intel`/`layer1_fan_in`."""

    @pytest.mark.asyncio
    async def test_halt_environment_sets_current_agent_to_the_probe(self):
        import fakeredis.aioredis

        from backend.config import Settings
        from backend.orchestrator.nodes import GraphNodes
        from backend.state.redis_store import RedisStore
        from backend.state.schema import RunStateModel

        client = fakeredis.aioredis.FakeRedis(decode_responses=False)
        store = RedisStore(client, Settings(redis_url="redis://localhost:6379/0"))
        nodes = GraphNodes(store, Settings(redis_url="redis://localhost:6379/0"))

        state = RunStateModel(run_id="r", repo_path="/tmp/x", current_agent="fan-in")
        result = await nodes.halt_environment(state)

        assert result.current_agent == "A0.7"
        await client.aclose()


class TestBlockedRunProjection:
    def test_blocked_is_not_failed_and_not_pending(self):
        from backend.services.ui_projection import run_decision
        from backend.state.schema import RunStateModel

        state = RunStateModel(run_id="r", repo_path="/tmp/x", status="blocked")
        decision, label = run_decision(state)

        assert decision == "blocked"
        assert label == "Environment not prepared"
        # The old fallthrough said "Pending", implying work still in progress.
        assert label != "Pending"

    def test_blocked_is_terminal(self):
        from backend.services.ui_projection import RUN_TERMINAL_STATES

        assert "blocked" in RUN_TERMINAL_STATES

    def test_blocked_run_reports_no_trust_score(self):
        """Nothing was measured, so nothing is averaged."""
        from backend.services.ui_projection import _trust_score
        from backend.state.schema import RunStateModel

        state = RunStateModel(run_id="r", repo_path="/tmp/x", status="blocked")
        assert _trust_score(state) is None


class TestSchemaCarriesEnvironment:
    def test_both_schemas_have_the_field(self):
        """T4: a field added to only one of the two is silently dropped."""
        from backend.state.schema import RunState, RunStateModel

        assert "environment" in RunStateModel.model_fields
        assert "environment" in RunState.__annotations__

    def test_defaults_to_none_so_stored_runs_deserialize(self):
        from backend.state.schema import RunStateModel

        state = RunStateModel(run_id="r", repo_path="/tmp/x")
        assert state.environment is None


class TestProjectDependenciesAreChecked:
    """An importable pytest is necessary but not sufficient.

    Two production defects shaped this. First, a fixture declaring `fastapi`
    and `redis` with neither installed passed the precheck and ran the whole
    pipeline, because the host happened to have pytest. Then the fix for *that*
    — sampling the repository's own imports — blocked Flask, a fully prepared
    checkout whose suite collects 483 tests, on `_typeshed` (a TYPE_CHECKING-only
    module), on example apps inside the repo, and on docs extras the suite never
    imports.

    Sampling was asking the wrong question. `pytest --collect-only` imports every
    test module and its transitive dependencies, so it answers the real one.
    """

    @pytest.mark.asyncio
    async def test_missing_test_dependency_blocks(self, tmp_path):
        from backend.services.environment_probe import probe_environment

        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_x.py").write_text(
            "import definitely_not_a_real_module_xyz\n\ndef test_a():\n    assert True\n"
        )

        report = await probe_environment(tmp_path, timeout=120)

        # No skip guard. These tests once began with
        # `if not report.test_runner_available: pytest.skip(...)`, because the
        # probe shelled out to a bare `python` from PATH which frequently had no
        # pytest. The probe now uses `sys.executable`, so a test executing under
        # pytest is proof that the probed interpreter has pytest — the guard
        # became unreachable, and an unreachable guard around an assertion is
        # just a way to stop asserting.

        assert report.blocking is True
        assert report.status == "not_prepared"
        assert "definitely_not_a_real_module_xyz" in report.missing_imports
        assert report.suggested_command == 'pip install -e ".[dev]"'

    @pytest.mark.asyncio
    async def test_type_checking_only_imports_do_not_block(self, tmp_path):
        """`_typeshed` is never importable at runtime and never a dependency.

        The exact false positive that blocked Flask.
        """
        from backend.services.environment_probe import probe_environment

        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        (tmp_path / "app.py").write_text(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from _typeshed import StrPath\n\n"
            "def go():\n    return 1\n"
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_app.py").write_text("from app import go\n\ndef test_go():\n    assert go()\n")

        report = await probe_environment(tmp_path, timeout=120)

        # No skip guard. These tests once began with
        # `if not report.test_runner_available: pytest.skip(...)`, because the
        # probe shelled out to a bare `python` from PATH which frequently had no
        # pytest. The probe now uses `sys.executable`, so a test executing under
        # pytest is proof that the probed interpreter has pytest — the guard
        # became unreachable, and an unreachable guard around an assertion is
        # just a way to stop asserting.

        assert report.blocking is False
        assert report.status == "ready"
        assert report.missing_imports == []

    @pytest.mark.asyncio
    async def test_empty_suite_is_not_an_environment_fault(self, tmp_path):
        """Exit 5 = no tests collected. The environment is fine; A3.5 reports it."""
        from backend.services.environment_probe import probe_environment

        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        (tmp_path / "lib.py").write_text("def add(a, b):\n    return a + b\n")

        report = await probe_environment(tmp_path, timeout=120)

        # No skip guard. These tests once began with
        # `if not report.test_runner_available: pytest.skip(...)`, because the
        # probe shelled out to a bare `python` from PATH which frequently had no
        # pytest. The probe now uses `sys.executable`, so a test executing under
        # pytest is proof that the probed interpreter has pytest — the guard
        # became unreachable, and an unreachable guard around an assertion is
        # just a way to stop asserting.

        assert report.blocking is False
        assert report.status == "ready"


class TestMissingModuleParsing:
    def test_extracts_top_level_package(self):
        from backend.services.environment_probe import _missing_modules_from_output

        out = "ModuleNotFoundError: No module named 'dirty_equals'"
        assert _missing_modules_from_output(out) == ["dirty_equals"]

    def test_collapses_submodules_to_the_installable_name(self):
        """`pip install foo.bar.baz` is not a thing; `pip install foo` is."""
        from backend.services.environment_probe import _missing_modules_from_output

        out = "ModuleNotFoundError: No module named 'foo.bar.baz'"
        assert _missing_modules_from_output(out) == ["foo"]

    def test_deduplicates_in_first_seen_order(self):
        from backend.services.environment_probe import _missing_modules_from_output

        out = (
            "ModuleNotFoundError: No module named 'b'\n"
            "ModuleNotFoundError: No module named 'a'\n"
            "ModuleNotFoundError: No module named 'b'\n"
        )
        assert _missing_modules_from_output(out) == ["b", "a"]

    def test_empty_output(self):
        from backend.services.environment_probe import _missing_modules_from_output

        assert _missing_modules_from_output("") == []
        assert _missing_modules_from_output("all good") == []

    @pytest.mark.asyncio
    async def test_reason_names_the_real_problem(self, tmp_path):
        """When pytest is present, the reason must not blame pytest."""
        from backend.services.environment_probe import probe_environment

        (tmp_path / "requirements.txt").write_text("something\n")
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_m.py").write_text(
            "import definitely_not_a_real_module_xyz\n\ndef test_a():\n    assert True\n"
        )

        report = await probe_environment(tmp_path, timeout=120)

        # No skip guard. These tests once began with
        # `if not report.test_runner_available: pytest.skip(...)`, because the
        # probe shelled out to a bare `python` from PATH which frequently had no
        # pytest. The probe now uses `sys.executable`, so a test executing under
        # pytest is proof that the probed interpreter has pytest — the guard
        # became unreachable, and an unreachable guard around an assertion is
        # just a way to stop asserting.

        assert "pytest is not importable" not in report.reason
        assert "definitely_not_a_real_module_xyz" in report.reason


class TestTestsCollectedParsing:
    def test_extracts_collected_count(self):
        from backend.services.environment_probe import _tests_collected_from_output

        assert _tests_collected_from_output("3 tests collected in 0.01s") == 3

    def test_single_test_is_still_parsed(self):
        from backend.services.environment_probe import _tests_collected_from_output

        assert _tests_collected_from_output("1 test collected in 0.00s") == 1

    def test_no_tests_collected_is_zero_not_none(self):
        from backend.services.environment_probe import _tests_collected_from_output

        assert _tests_collected_from_output("no tests collected in 0.00s") == 0

    def test_absent_summary_is_none(self):
        from backend.services.environment_probe import _tests_collected_from_output

        assert _tests_collected_from_output("") is None
        assert _tests_collected_from_output("some unrelated output") is None

    def test_collected_with_errors_still_parses_count(self):
        from backend.services.environment_probe import _tests_collected_from_output

        assert _tests_collected_from_output("3 tests collected, 1 error in 0.01s") == 3

    @pytest.mark.asyncio
    async def test_ready_environment_reports_a_real_count(self, tmp_path):
        from backend.services.environment_probe import probe_environment

        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_x.py").write_text("def test_a():\n    assert True\n\ndef test_b():\n    assert True\n")

        report = await probe_environment(tmp_path, timeout=120)

        assert report.status == "ready"
        assert report.tests_collected == 2

    @pytest.mark.asyncio
    async def test_empty_suite_reports_zero_not_none(self, tmp_path):
        from backend.services.environment_probe import probe_environment

        (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
        (tmp_path / "lib.py").write_text("def add(a, b):\n    return a + b\n")

        report = await probe_environment(tmp_path, timeout=120)

        assert report.status == "ready"
        assert report.tests_collected == 0

    def test_not_prepared_environment_leaves_count_unset(self):
        """Collection never ran, so there is nothing to report — not a zero."""
        from backend.models.environment import EnvironmentReport

        report = EnvironmentReport(status="not_prepared", blocking=True)
        assert report.tests_collected is None


class TestHeaderPublishesProbeError:
    def test_no_error_recorded_reads_false(self):
        from backend.services.ui_projection import build_workspace_header
        from backend.state.schema import RunStateModel

        state = RunStateModel(run_id="r", repo_path="/tmp/x")
        header = build_workspace_header(state, [])

        assert header["environmentProbeError"] is False

    def test_a07_error_is_surfaced(self):
        from backend.services.ui_projection import build_workspace_header
        from backend.state.schema import RunStateModel

        state = RunStateModel(
            run_id="r",
            repo_path="/tmp/x",
            errors=[{"agent": "A0.7", "error": "boom"}],
        )
        header = build_workspace_header(state, [])

        assert header["environmentProbeError"] is True
        # The probe erroring does not mean the environment is known bad —
        # `environment` itself stays whatever the pipeline last recorded.
        assert header["environment"] is None

    def test_an_unrelated_agent_error_does_not_trip_the_flag(self):
        from backend.services.ui_projection import build_workspace_header
        from backend.state.schema import RunStateModel

        state = RunStateModel(
            run_id="r",
            repo_path="/tmp/x",
            errors=[{"agent": "A4", "error": "boom"}],
        )
        header = build_workspace_header(state, [])

        assert header["environmentProbeError"] is False


class TestHeaderPublishesEnvironment:
    def test_workspace_header_carries_the_report(self):
        """Without this the client knows a run stopped but not why."""
        from backend.services.ui_projection import build_workspace_header
        from backend.state.schema import RunStateModel

        state = RunStateModel(
            run_id="r",
            repo_path="/tmp/x",
            status="blocked",
            environment={"status": "not_prepared", "blocking": True, "reason": "deps missing"},
        )
        header = build_workspace_header(state, [])

        assert header["environment"]["status"] == "not_prepared"
        assert header["environment"]["reason"] == "deps missing"

    def test_absent_environment_is_null_not_omitted(self):
        from backend.services.ui_projection import build_workspace_header
        from backend.state.schema import RunStateModel

        header = build_workspace_header(RunStateModel(run_id="r", repo_path="/tmp/x"), [])
        assert header["environment"] is None
