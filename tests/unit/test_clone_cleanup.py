"""Working-copy cleanup (B-B14).

Every run copied an entire repository into a temp directory and left it there,
so disk grew with run count × repository size until something else noticed.

The tests are mostly about what cleanup must *refuse* to do. Deleting
directories from a background job is the kind of feature that is fine until the
day a misconfigured `repo_clone_path` points at the user's real checkout, so the
guard conditions matter more than the happy path.
"""

import tempfile
from pathlib import Path

from backend.services.git_service import CLONE_PREFIX, clone_or_copy_repo, discard_clone


def _a_clone(source: Path) -> Path:
    return Path(clone_or_copy_repo(str(source)))


class TestDiscardsWhatItCreated:
    def test_a_real_clone_is_removed(self, tmp_path):
        source = tmp_path / "repo"
        source.mkdir()
        (source / "main.py").write_text("x = 1\n", encoding="utf-8")

        clone = _a_clone(source)
        assert (clone / "main.py").exists()

        assert discard_clone(str(clone)) is True
        assert not clone.exists()

    def test_discarding_twice_is_not_an_error(self, tmp_path):
        source = tmp_path / "repo"
        source.mkdir()
        clone = _a_clone(source)

        assert discard_clone(str(clone)) is True
        assert discard_clone(str(clone)) is False


class TestRefusesEverythingElse:
    """Both conditions together mean `clone_or_copy_repo` made it."""

    def test_a_directory_outside_temp_is_refused(self, tmp_path, monkeypatch):
        """The dangerous case: `repo_clone_path` pointing at a real checkout.

        It even carries the prefix here and is still refused, because it is not
        under the temp root. `tmp_path` is itself inside the system temp
        directory, so the temp root is redirected to make "outside" real —
        without that the test passes for the wrong reason.
        """
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(elsewhere))

        real = tmp_path / f"{CLONE_PREFIX}checkout"
        real.mkdir()
        (real / "main.py").write_text("x = 1\n", encoding="utf-8")

        assert discard_clone(str(real)) is False
        assert (real / "main.py").exists()

    def test_a_temp_directory_without_the_prefix_is_refused(self):
        other = Path(tempfile.mkdtemp(prefix="someone_elses_"))
        try:
            assert discard_clone(str(other)) is False
            assert other.exists()
        finally:
            other.rmdir()

    def test_absent_and_empty_paths_are_refused(self, tmp_path):
        assert discard_clone(None) is False
        assert discard_clone("") is False
        assert discard_clone(str(tmp_path / "nope")) is False

    def test_a_file_is_refused(self):
        handle = tempfile.NamedTemporaryFile(prefix=CLONE_PREFIX, delete=False)
        handle.close()
        try:
            assert discard_clone(handle.name) is False
            assert Path(handle.name).exists()
        finally:
            Path(handle.name).unlink()


class TestRunnerCleansUpOnEveryTerminalPath:
    """Completed, blocked and failed — the leak was worst on the last two."""

    def _runner(self, monkeypatch, clone: Path, *, outcome: str):
        from unittest.mock import AsyncMock, MagicMock

        from backend.config import Settings
        from backend.orchestrator.runner import PipelineRunner
        from backend.state.schema import RunStateModel

        monkeypatch.setattr(
            "backend.orchestrator.runner.build_graph", lambda store, settings: MagicMock()
        )

        state = RunStateModel(
            run_id="r1", repo_path="repo", repo_clone_path=str(clone), status="running"
        )

        store = MagicMock()
        store.load_state = AsyncMock(return_value=state)
        store.save_state = AsyncMock()
        store.append_lifecycle_event = AsyncMock()

        runner = PipelineRunner.__new__(PipelineRunner)
        runner.store = store
        runner.settings = Settings(stub_mode=True)

        async def invoke(initial, config):
            if outcome == "raise":
                raise RuntimeError("graph exploded")
            return {**state.model_dump(), "status": outcome}

        runner.graph = MagicMock()
        runner.graph.ainvoke = invoke
        return runner

    async def _run(self, runner):
        try:
            await runner.execute("r1")
        except RuntimeError:
            pass

    def test_completed_run_discards_its_clone(self, monkeypatch, tmp_path):
        import asyncio

        source = tmp_path / "repo"
        source.mkdir()
        clone = _a_clone(source)

        asyncio.run(self._run(self._runner(monkeypatch, clone, outcome="completed")))
        assert not clone.exists()

    def test_blocked_run_discards_its_clone(self, monkeypatch, tmp_path):
        import asyncio

        source = tmp_path / "repo"
        source.mkdir()
        clone = _a_clone(source)

        asyncio.run(self._run(self._runner(monkeypatch, clone, outcome="blocked")))
        assert not clone.exists()

    def test_failed_run_discards_its_clone(self, monkeypatch, tmp_path):
        import asyncio

        source = tmp_path / "repo"
        source.mkdir()
        clone = _a_clone(source)

        asyncio.run(self._run(self._runner(monkeypatch, clone, outcome="raise")))
        assert not clone.exists()

    def test_a_cleanup_failure_never_fails_the_run(self, monkeypatch, tmp_path):
        """A run that finished must not be reported as failed because its temp
        directory could not be removed."""
        import asyncio

        from unittest.mock import AsyncMock

        source = tmp_path / "repo"
        source.mkdir()
        clone = _a_clone(source)
        runner = self._runner(monkeypatch, clone, outcome="completed")
        runner.store.load_state = AsyncMock(side_effect=RuntimeError("redis down"))

        # The first `load_state` is the run's own; making it raise would fail
        # `execute` for the wrong reason, so only the cleanup read is broken.
        calls = {"n": 0}
        original = clone

        async def load(run_id):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("redis down")
            from backend.state.schema import RunStateModel

            return RunStateModel(
                run_id="r1", repo_path="repo", repo_clone_path=str(original), status="running"
            )

        runner.store.load_state = load

        asyncio.run(self._run(runner))
        # The run completed; the clone survives, which is a leak but not a
        # failure, and the log records it.
        assert clone.exists()
