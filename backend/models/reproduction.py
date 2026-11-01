from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReproductionStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"
    INFRA_ERROR = "INFRA_ERROR"
    NO_TESTS = "NO_TESTS"


# Which parser branch produced the evidence on a CONFIRMED result. The
# pytest-json-report path is a structured read of pytest's own classification;
# the output-text path is a regex fallback over raw stdout/stderr when the
# JSON report could not be produced. Both can conclude CONFIRMED — this is the
# real, already-existing distinction in confidence (0.9 vs 0.7) between them,
# now named instead of only visible as a number.
EvidenceSource = Literal["pytest_report", "output_text"]


class ReproductionResult(BaseModel):
    status: ReproductionStatus = ReproductionStatus.UNCONFIRMED
    reproduced: bool = False
    failing_test: str | None = None
    requested_test: str | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    failing_file: str | None = None
    failing_line: int | None = None
    traceback: str | None = None
    stack_trace: str | None = None
    confidence: float = 0.0
    force_draft_pr: bool = False
    report_path: str | None = None
    infra_detail: str | None = None
    reexecution_command: str = ""
    reexecution_is_targeted: bool = False
    reexecution_timeout_seconds: int = 120
    pre_existing_failures: list[str] = Field(default_factory=list)
    # The command A3.5 actually ran to produce this result — distinct from
    # `reexecution_command`, which is a
    # *targeted* command built for a later stage (A8 validation, proof
    # bundles) and was never executed to produce this evidence.
    command: str = ""
    exit_code: int | None = None
    # `run_command` collapses "timed out" into the same -1 exit code as "could
    # not spawn" — this flag makes the distinction explicit instead of relying
    # on a client parsing `infra_detail` for the literal string "timeout".
    timed_out: bool = False
    stdout: str | None = None
    stderr: str | None = None
    duration_seconds: float | None = None
    started_at: str | None = None
    finished_at: str | None = None
    # From pytest's own summary, when a JSON report exists — absent (None),
    # never zero, when pytest never produced a report at all.
    tests_collected: int | None = None
    tests_passed: int | None = None
    tests_failed: int | None = None
    evidence_source: EvidenceSource | None = None

    @model_validator(mode="after")
    def sync_legacy_fields(self) -> "ReproductionResult":
        if self.status == ReproductionStatus.CONFIRMED:
            self.reproduced = True
            self.force_draft_pr = False
        else:
            self.reproduced = False
            self.force_draft_pr = True

        if self.traceback and not self.stack_trace:
            self.stack_trace = self.traceback
        elif self.stack_trace and not self.traceback:
            self.traceback = self.stack_trace

        return self
