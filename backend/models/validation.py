from typing import Literal

from pydantic import BaseModel, Field


class MutantRecord(BaseModel):
    """One mutant, individually. Only what mutmut actually told us.

    `line`/`before`/`after` are populated only for mutants A8 chose to fetch
    a diff for (survivors, bounded — see `a8_mutation_validator`); every
    other mutant carries `function` alone. There is no line-level attribution
    to invent for the rest — mutmut's per-mutant listing does not carry one.
    """

    mutant_id: str
    status: str
    function: str | None = None
    file: str | None = None
    line: int | None = None
    before: str | None = None
    after: str | None = None


class ValidationFailure(BaseModel):
    pytest_phase: Literal["setup", "call", "teardown", "collection"] | None = None
    exception_type: str | None = None
    failing_test: str | None = None
    assertion_message: str | None = None
    expected_value: str | None = None
    actual_value: str | None = None
    traceback: str | None = None
    pytest_stdout: str = ""
    pytest_stderr: str = ""
    validation_stage: Literal["mutation", "security"] = "mutation"
    mutation_result: dict | None = None
    security_result: dict | None = None
    target_test_passed: bool | None = None
    regression_tests_passed: bool | None = None
    new_failures: list[str] = Field(default_factory=list)
    pre_existing_failures: list[str] = Field(default_factory=list)


class RetryBrief(BaseModel):
    attempt: int = 0
    violated_contract: str | None = None
    assertion_failure: str | None = None
    stack_trace: str | None = None
    security_constraint: str | None = None
    validation_failure: ValidationFailure | None = None
    previous_patch_summary: str | None = None
    expected_behaviour: str | None = None
    actual_behaviour: str | None = None
    retry_instruction: str | None = None


class MutationValidationResult(BaseModel):
    validated_patch_hash: str | None = None
    pytest_passed: bool = False
    patch_causality: dict | None = None
    #: Whether pytest executed at all. `pytest_passed=False` covers both "the
    #: suite ran and failed" and "pytest was never importable in the target
    #: repository"; only this field separates them, and the difference decides
    #: whether `correctness_score` is a measurement or an absence. Defaults True
    #: so states persisted before this existed keep their original meaning.
    pytest_available: bool = True
    mutation_score: float | None = None
    mutant_survived: bool = False
    #: `None` until scoped validation actually ran. A measured 0.0 means the
    #: patch scored zero; absent means nothing scored it.
    correctness_score: float | None = None

    # Real mutation evidence. All additive and defaulted, so states persisted
    # before mutation parsing existed still deserialize. `mutation_status`
    # distinguishes "measured" from "could not be measured" — consumers must not
    # read `mutation_score is None` as a score of zero.
    mutation_status: Literal["scored", "unavailable", "not_run"] = "not_run"
    mutation_unavailable_reason: str | None = None
    killed_mutants: int | None = None
    survived_mutants: int | None = None
    total_mutants: int | None = None
    inconclusive_mutants: int | None = None
    mutants_by_status: dict[str, int] = Field(default_factory=dict)
    #: Individual mutants when mutmut's per-mutant listing was available —
    #: `[]` otherwise (the progress-summary and legacy-section formats carry
    #: only aggregate counts). Only survivors get a `line`/`before`/`after`
    #: diff (see `a8_mutation_validator._enrich_survivor_diffs`) — the rest
    #: carry `function` alone.
    mutants: list[MutantRecord] = Field(default_factory=list)
    failure_brief: RetryBrief | None = None
    validation_failure: ValidationFailure | None = None
    pytest_reexecution_command: str = ""
    reexecution_command: str = ""
    reexecution_timeout_seconds: int = 60
    target_test_passed: bool | None = None
    regression_tests_passed: bool | None = None
    new_failures: list[str] = Field(default_factory=list)
    pre_existing_failures: list[str] = Field(default_factory=list)
    patch_retry_required: bool = False


class SecurityRescanResult(BaseModel):
    new_findings: list = Field(default_factory=list)
    rejected: bool = False
    #: `None` until the re-scan actually ran. A measured 0.0 means four or more
    #: new findings; absent means A9 was skipped and nothing was scanned.
    security_score: float | None = None
    failure_brief: RetryBrief | None = None
    validation_failure: ValidationFailure | None = None
    reexecution_command: str = ""
    reexecution_timeout_seconds: int = 150
    #: Scanners that actually executed this rescan (subset of ["bandit",
    #: "semgrep"]) — durable so a client reading the run later, not just the
    #: live event stream, can tell "clean" from "not measured". Defaults empty
    #: so state persisted before this field existed still deserializes.
    scanners_run: list[str] = Field(default_factory=list)
    #: The full baseline-vs-post reconciliation, one lane per known scanner
    #: (`a9_security_rescan.reconciliation_lanes`): which post-patch findings
    #: paired with a baseline occurrence and by how many lines each moved, which
    #: had no counterpart, and which baseline occurrences are gone. `new_findings`
    #: is the introduced side of exactly this comparison — kept here so a reader
    #: can *see* that a shifted finding was absorbed rather than take it on
    #: trust, which is not recoverable downstream once discarded. Defaults empty
    #: so state persisted before this field existed still deserializes; an empty
    #: list means "not recorded", never "nothing carried" (the same precedent
    #: `scanners_run` set above).
    reconciliation: list = Field(default_factory=list)
