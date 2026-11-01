from backend.services.repair_step_evidence import patch_fingerprint
from pathlib import Path
import shutil
import tempfile
from configparser import Error as ConfigError

from backend.agents.base import AgentBase
from backend.models.validation import MutantRecord, MutationValidationResult, ValidationFailure
from backend.services.mutation_parser import (
    MutationOutcome,
    parse_mutation_output,
    parse_mutmut_show_diff,
)
from backend.services.retry_brief_builder import build_retry_brief
from backend.services.evidence_recovery import record_attempt
from backend.services.scoped_validation import run_scoped_validation
from backend.services.patch_causality import evaluate_patch_causality
from backend.services.subprocess_runner import PYTHON, run_command
from backend.state.schema import RunStateModel

# Correctness rubric. Named so the merge threshold in `a10_routing` can be read
# against the values that feed it.
CORRECTNESS_TESTS_FAILED = 0.0
CORRECTNESS_MUTANT_SURVIVED = 40.0
CORRECTNESS_MUTATION_UNAVAILABLE = 70.0
CORRECTNESS_MUTATION_BASE = 60.0
CORRECTNESS_MUTATION_RANGE = 40.0

MUTANT_SURVIVED_ASSERTION = (
    "Mutant survived — test passes coincidentally without validating fix"
)

# `mutmut show <id>` is one extra subprocess call per mutant — real work, not
# free. Every survivor is worth the call (it is the finding A8 exists to
# produce); killed/inconclusive mutants are worth it only to give the gutter
# real per-line density instead of a function-level bar, so they fill
# whatever budget survivors did not use. A run with more mutants than the cap
# degrades honestly (see `mutation_parser.MutantRecord` — the un-enriched
# remainder keeps its `function` attribution, never a guessed line).
MAX_MUTANT_DIFFS = 40


class A8MutationValidatorAgent(AgentBase):
    agent_id = "A8"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Running mutation validation gauntlet")
        repo = Path(state.repo_clone_path or state.repo_path)
        patch_bundle = state.patch_bundle or {}
        contracts = patch_bundle.get("contracts", [])
        reproduction = state.reproduction or {}
        target_test = reproduction.get("failing_test")
        baseline_failures = reproduction.get("pre_existing_failures") or []

        scoped = await run_scoped_validation(
            repo,
            state.run_id,
            target_test=target_test,
            baseline_failures=baseline_failures,
            timeout=120,
            **({"generated_test": state.generated_test} if state.generated_test else {}),
            acceptance_test=state.acceptance_test,
        )

        causality = None
        # A still-failing candidate needs the normal bounded repair loop.
        # Causality is an acceptance gate for a candidate that passes tests.
        if scoped.pytest_passed and state.acceptance_test and patch_bundle.get("patches"):
            target_symbol = next((p.get("target_symbol") for p in patch_bundle["patches"]
                                  if p.get("target_symbol")), None)
            if not target_symbol:
                target_symbol = (state.issue_contract or {}).get("target_symbol")
            causality = await evaluate_patch_causality(
                repo, patch_bundle["patches"], state.acceptance_test,
                target_symbol=target_symbol,
                expected_failure=reproduction.get("exception_message"),
                expected_failure_file=reproduction.get("failing_file"),
                expected_failure_line=reproduction.get("failing_line"), timeout=120,
            )

        failure_brief = None
        validation_failure = scoped.validation_failure
        mutmut_cmd = ""
        mutmut_timeout = self.settings.mutmut_timeout_seconds
        causality_unestablished = bool(causality and causality.get("causal_effect_supported") is not True)
        pytest_passed = scoped.pytest_passed and not causality_unestablished
        patch_retry_required = scoped.patch_retry_required
        failure_brief_needed = scoped.failure_brief_needed

        outcome = MutationOutcome(status="not_run", unavailable_reason=None)

        # Correctness scores a *patch*. With no patch there is nothing to score:
        # pytest still runs and still reports, but it is describing the
        # repository, not a repair. Scoring it anyway produced the "correctness
        # 0 / 80" seen on runs that generated zero patches — a failing grade for
        # work that was never attempted.
        has_patch = bool(patch_bundle.get("patches"))
        if state.generated_test:
            state.generated_test["after_passed"] = scoped.target_test_passed if has_patch else None
            state.generated_test["validation_detail"] = (
                scoped.validation_failure.assertion_message if scoped.validation_failure else None
            )
        # `pytest_passed` is False both when the suite ran and failed and when
        # pytest never ran at all — a cloned repository whose dependencies were
        # never installed cannot execute a single test. Scoring the second case
        # as CORRECTNESS_TESTS_FAILED published a hard 0 for a patch nothing had
        # examined, and the report then printed "correctness 0 / 80" directly
        # above its own sentence explaining that nothing had been measured.
        pytest_available = scoped.pytest_available
        correctness_score: float | None
        if not has_patch or not pytest_available or causality_unestablished:
            correctness_score = None
        else:
            correctness_score = 100.0 if pytest_passed else CORRECTNESS_TESTS_FAILED

        if pytest_passed:
            # Still called with no patches: `_run_mutmut` reports *why* mutation
            # produced nothing, and that reason is worth publishing. Only the
            # score assignments below are gated on `has_patch` — the status
            # contract is unchanged.
            outcome, mutmut_cmd = await self._run_mutmut(
                repo, patch_bundle, target_test=target_test,
                baseline_failures=baseline_failures,
            )

            if outcome.mutant_survived:
                correctness_score = CORRECTNESS_MUTANT_SURVIVED
                contract = contracts[0]["assertion"] if contracts else "unknown contract"
                validation_failure = ValidationFailure(
                    failing_test=target_test,
                    assertion_message=(
                        f"{MUTANT_SURVIVED_ASSERTION} "
                        f"({outcome.survived_mutants} of {outcome.total_mutants} mutants survived)"
                    ),
                    validation_stage="mutation",
                    target_test_passed=True,
                    regression_tests_passed=True,
                    pre_existing_failures=baseline_failures,
                )
                patch_retry_required = True
                failure_brief_needed = True
                pytest_passed = False
                failure_brief = build_retry_brief(
                    validation_failure,
                    state.retry_count + 1,
                    patch_bundle=patch_bundle,
                    reproduction=reproduction,
                    violated_contract=contract,
                )
            elif outcome.status == "scored" and outcome.mutation_score is not None:
                correctness_score = min(
                    100.0,
                    CORRECTNESS_MUTATION_BASE + outcome.mutation_score * CORRECTNESS_MUTATION_RANGE,
                )
            elif has_patch:
                # Mutation ran against a real patch and produced no verdict.
                # Score below the auto-merge threshold so the absence of proof
                # forces manual review rather than passing on a substituted
                # value — that is a judgement *about a patch*, so it needs one.
                correctness_score = CORRECTNESS_MUTATION_UNAVAILABLE
            # With no patch, `correctness_score` stays `None`. There is nothing
            # to judge, and 70 would be a grade for work never attempted.
        elif failure_brief_needed and validation_failure:
            failure_brief = build_retry_brief(
                validation_failure,
                state.retry_count + 1,
                patch_bundle=patch_bundle,
                reproduction=reproduction,
            )

        result = MutationValidationResult(
            patch_causality=causality,
            validated_patch_hash=patch_fingerprint(patch_bundle),
            pytest_passed=pytest_passed,
            pytest_available=pytest_available,
            mutation_score=outcome.mutation_score,
            mutant_survived=outcome.mutant_survived,
            mutation_status=outcome.status,
            mutation_unavailable_reason=outcome.unavailable_reason,
            killed_mutants=outcome.killed_mutants,
            survived_mutants=outcome.survived_mutants,
            total_mutants=outcome.total_mutants,
            inconclusive_mutants=outcome.inconclusive_mutants,
            mutants_by_status=outcome.by_status,
            mutants=outcome.mutants,
            correctness_score=correctness_score,
            failure_brief=failure_brief,
            validation_failure=validation_failure,
            pytest_reexecution_command=scoped.pytest_reexecution_command,
            reexecution_command=mutmut_cmd,
            reexecution_timeout_seconds=mutmut_timeout,
            target_test_passed=scoped.target_test_passed,
            regression_tests_passed=scoped.regression_tests_passed,
            new_failures=scoped.new_failures,
            pre_existing_failures=scoped.pre_existing_failures,
            patch_retry_required=patch_retry_required,
        )
        result_dict = result.model_dump(mode="json")
        if validation_failure:
            validation_failure = validation_failure.model_copy(
                update={"mutation_result": result_dict}
            )
            result.validation_failure = validation_failure
            result_dict = result.model_dump(mode="json")
            if failure_brief:
                failure_brief = failure_brief.model_copy(
                    update={"validation_failure": validation_failure}
                )
                result.failure_brief = failure_brief
                result_dict["failure_brief"] = failure_brief.model_dump(mode="json")

        state.mutation_result = result_dict
        if failure_brief and patch_retry_required:
            state.retry_brief = failure_brief.model_dump(mode="json")
        elif not patch_retry_required:
            state.retry_brief = None
        if validation_failure:
            state.validation_failure = validation_failure.model_dump(mode="json")
        else:
            state.validation_failure = None
        record_attempt(state, self.settings.max_retries)
        if causality and causality.get("causal_effect_supported") is not True:
            reason = causality.get("reason") or "Acceptance test did not depend on the selected repair"
            state.recovery_decision = {
                "route": "recovery_stop", "category": "insufficient_evidence", "reason": reason,
            }
            state.recovery_blocked_reason = reason
            state.recovery_attempts[-1]["recovery_decision"] = "recovery_stop"
            state.recovery_attempts[-1]["failure_category"] = "insufficient_evidence"
            state.recovery_attempts[-1]["reason"] = reason
        recovery = state.recovery_decision or {}
        if recovery.get("category") == "environment_failure":
            correctness_score = None
            result_dict["correctness_score"] = None
            state.mutation_result = result_dict
        if (recovery.get("route") == "generate_code" and validation_failure
                and not state.retry_brief):
            state.retry_brief = build_retry_brief(
                validation_failure, state.retry_count + 1,
                patch_bundle=patch_bundle, reproduction=reproduction,
            ).model_dump(mode="json")

        payload = {
            "correctness_score": correctness_score,
            # Per-attempt scores. `mutation_score` stays None when pytest failed,
            # because mutmut never ran — the UI must say "not scored" rather than
            # render a 0.00 that looks like a measurement. `mutation_status`
            # tells the UI which of those two cases it is.
            "mutation_score": outcome.mutation_score,
            "mutant_survived": outcome.mutant_survived,
            "mutation_status": outcome.status,
            "mutation_unavailable_reason": outcome.unavailable_reason,
            "killed_mutants": outcome.killed_mutants,
            "survived_mutants": outcome.survived_mutants,
            "total_mutants": outcome.total_mutants,
            "pytest_passed": pytest_passed,
            "target_test_passed": scoped.target_test_passed,
            "regression_tests_passed": scoped.regression_tests_passed,
            "new_failures": scoped.new_failures,
            "pre_existing_failures": scoped.pre_existing_failures,
            "patch_retry_required": patch_retry_required,
        }
        if validation_failure and patch_retry_required:
            payload.update(
                {
                    "failing_test": validation_failure.failing_test,
                    "assertion_message": validation_failure.assertion_message,
                    "expected_value": validation_failure.expected_value,
                    "actual_value": validation_failure.actual_value,
                }
            )

        await self.emit_status(
            state,
            "completed",
            f"pytest={'pass' if pytest_passed else 'fail'}, "
            f"mutation={outcome.status}, mutant_survived={outcome.mutant_survived}",
            payload,
        )
        return state

    @staticmethod
    def _configure_mutmut_paths(
        repo: Path, patch_file: str | list[str], target_test: str | None = None,
        baseline_failures: list[str] | None = None,
    ) -> None:
        """Configure only the isolated copy, retaining project test settings."""
        from configparser import ConfigParser
        import tomllib
        import tomli_w

        files = [patch_file] if isinstance(patch_file, str) else patch_file
        roots = sorted({file.split("/", 1)[0] for file in files})
        # Flat modules and other packages may be imported by the test suite.
        # Copy them without mutating them; only_mutate pins the repair scope.
        companions = sorted(p.name for p in repo.iterdir() if (
            p.suffix == ".py" or (p.is_dir() and (p / "__init__.py").is_file())
            or p.name in {"pytest.ini", "tox.ini"}
        ) and p.name not in roots)
        selection = [f"--deselect={nodeid}" for nodeid in sorted({
            nodeid for nodeid in (baseline_failures or [])
            if nodeid != target_test and "\n" not in nodeid and "\r" not in nodeid
        })]
        overrides = {
            "source_paths": roots, "only_mutate": files,
            "paths_to_mutate": files,
            "pytest_add_cli_args_test_selection": selection,
        }
        pyproject = repo / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            if "mutmut" in data.get("tool", {}):
                config = data["tool"]["mutmut"]
                config.update(overrides)
                config["also_copy"] = sorted(set(config.get("also_copy", []) + companions))
                pyproject.write_text(tomli_w.dumps(data), encoding="utf-8")
                return

        config_path = repo / "setup.cfg"
        parser = ConfigParser(interpolation=None)
        parser.read(config_path)
        if not parser.has_section("mutmut"):
            parser.add_section("mutmut")
        existing_copy = parser.get("mutmut", "also_copy", fallback="").splitlines()
        overrides["also_copy"] = sorted(set(filter(None, existing_copy + companions)))
        for key, values in overrides.items():
            parser.set("mutmut", key, "\n".join(values))
        with config_path.open("w", encoding="utf-8") as handle:
            parser.write(handle)

    async def _run_mutmut(
        self, repo: Path, patch_bundle: dict, *, target_test: str | None = None,
        baseline_failures: list[str] | None = None,
    ) -> tuple[MutationOutcome, str]:
        """Run in a fresh copy so caches/configuration cannot taint publication.

        A fresh workspace also prevents a retry from scoring cached mutants
        belonging to the preceding patch.
        """
        with tempfile.TemporaryDirectory(prefix="proofix-mutation-") as directory:
            work = Path(directory) / "repo"
            try:
                shutil.copytree(repo, work, ignore=shutil.ignore_patterns(
                    ".git", ".venv", "venv", "node_modules", "__pycache__",
                    ".pytest_cache", ".mutmut-cache", "mutants",
                ))
                return await self._run_mutmut_in_workspace(
                    work, patch_bundle, target_test=target_test,
                    baseline_failures=baseline_failures,
                )
            except (OSError, ValueError, ConfigError) as exc:
                return MutationOutcome(
                    status="unavailable",
                    unavailable_reason=f"Mutation workspace could not be prepared ({type(exc).__name__})",
                ), ""

    async def _run_mutmut_in_workspace(
        self, repo: Path, patch_bundle: dict, *, target_test: str | None = None,
        baseline_failures: list[str] | None = None,
    ) -> tuple[MutationOutcome, str]:
        """Execute and collect evidence before the temporary workspace is removed."""
        patches = patch_bundle.get("patches", [])
        if not patches:
            return (
                MutationOutcome(
                    status="unavailable",
                    unavailable_reason="no patches to mutate",
                ),
                "",
            )

        patch_files = sorted({p.get("file", "") for p in patches if p.get("file", "").endswith(".py")})
        if not patch_files:
            return (
                MutationOutcome(
                    status="unavailable",
                    unavailable_reason="patch bundle has no target file",
                ),
                "",
            )

        # mutmut 3 reads its target scope from project configuration.
        self._configure_mutmut_paths(repo, patch_files, target_test, baseline_failures)

        mutmut_cmd = "python -m mutmut run && python -m mutmut results --all true"

        run_code, run_stdout, run_stderr = await run_command(
            [PYTHON, "-m", "mutmut", "run"],
            cwd=repo,
            timeout=self.settings.mutmut_timeout_seconds,
        )

        results_code: int | None = None
        results_stdout = ""
        results_stderr = ""
        if run_code != -1:
            # `--all true` includes killed mutants, which the default listing
            # omits — without them there is no denominator to score against.
            results_code, results_stdout, results_stderr = await run_command(
                [PYTHON, "-m", "mutmut", "results", "--all", "true"],
                cwd=repo,
                timeout=30,
            )

        outcome = parse_mutation_output(
            run_exit_code=run_code,
            run_stdout=run_stdout,
            run_stderr=run_stderr,
            results_exit_code=results_code,
            results_stdout=results_stdout,
            results_stderr=results_stderr,
        )
        if outcome.mutants:
            outcome = outcome.model_copy(
                update={"mutants": await self._enrich_mutant_diffs(repo, outcome.mutants)}
            )
        return outcome, mutmut_cmd

    @staticmethod
    async def _enrich_mutant_diffs(repo: Path, mutants: list[MutantRecord]) -> list[MutantRecord]:
        """Fetch the real changed line for as many mutants as the budget allows.

        Survivors go first — every one of them is worth the call, since a
        survivor is the finding A8 exists to produce. Killed, then
        inconclusive, mutants fill whatever budget survivors did not use, so
        the gutter gets real per-line density rather than only lighting up on
        survivor lines. `mutmut show` failing or emitting an unparseable diff
        leaves that mutant's `line`/`before`/`after` at `None` — attribution
        degrades to `function` rather than guessing a line.
        """
        priority = {"survived": 0, "killed": 1}
        order = sorted(range(len(mutants)), key=lambda i: priority.get(mutants[i].status, 2))

        enriched = list(mutants)
        for idx in order[:MAX_MUTANT_DIFFS]:
            record = mutants[idx]
            show_code, show_stdout, _show_stderr = await run_command(
                [PYTHON, "-m", "mutmut", "show", record.mutant_id],
                cwd=repo,
                timeout=15,
            )
            if show_code == -1:
                continue
            parsed = parse_mutmut_show_diff(show_stdout)
            if parsed:
                enriched[idx] = record.model_copy(update=parsed)
        return enriched
