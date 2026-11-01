from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from backend.models.proof import ReproductionConfidence


# `blocked` is distinct from `failed`: the pipeline worked correctly and
# declined to continue. A repository whose dependencies are not installed has
# not failed analysis — it was never analysable, and reporting that as a failure
# blames the code for the environment.
RunStatus = Literal[
    "pending", "running", "validation_retry", "completed", "failed", "blocked"
]


class RunStateModel(BaseModel):
    run_id: str
    repo_path: str = ""
    repo_clone_path: str = ""
    status: RunStatus = "pending"
    current_agent: str = "A0"
    retry_count: int = 0
    ws_sequence: int = 0
    issue_hint: str | None = None
    issue_contract: dict | None = None
    source_roots: list[str] = Field(default_factory=list)

    sig: dict | None = None
    cve_report: dict | None = None
    static_report: dict | None = None
    #: `EnvironmentReport` from the precheck. `None` before it runs, or when
    #: the precheck is disabled.
    environment: dict | None = None
    reproduction: dict | None = None
    generated_test: dict | None = None
    acceptance_test: dict | None = None
    root_cause: dict | None = None
    #: A4's `InvestigationReport` — the audit of the root-cause brief above:
    #: which sources it consulted, what each said, and how the confidence was
    #: arrived at. `None` before A4 runs.
    investigation: dict | None = None
    blast_graph: dict | None = None
    fix_dag: dict | None = None
    patch_bundle: dict | None = None
    retry_brief: dict | None = None
    validation_failure: dict | None = None
    recovery_attempts: list[dict] = Field(default_factory=list)
    recovery_decision: dict | None = None
    recovery_contract_hash: str | None = None
    recovery_blocked_reason: str | None = None
    mutation_result: dict | None = None
    security_result: dict | None = None
    pr_decision: dict | None = None
    base_commit_sha: str = ""
    proof_bundle: dict | None = None
    publication_result: dict | None = None
    reproduction_confidence: ReproductionConfidence = "full_suite"

    force_draft_pr: bool = False
    validation_exhausted: bool = False
    reinvestigation_exhausted: bool = False
    human_review_files: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RunState(TypedDict, total=False):
    run_id: str
    repo_path: str
    repo_clone_path: str
    status: RunStatus
    current_agent: str
    retry_count: int
    ws_sequence: int
    issue_hint: str | None
    issue_contract: dict | None
    source_roots: list[str]
    sig: dict
    cve_report: dict
    static_report: dict
    environment: dict
    reproduction: dict
    generated_test: dict | None
    acceptance_test: dict | None
    root_cause: dict
    investigation: dict
    blast_graph: dict
    fix_dag: dict
    patch_bundle: dict
    retry_brief: dict | None
    validation_failure: dict | None
    recovery_attempts: list[dict]
    recovery_decision: dict | None
    recovery_contract_hash: str | None
    recovery_blocked_reason: str | None
    mutation_result: dict
    security_result: dict
    pr_decision: dict
    base_commit_sha: str
    proof_bundle: dict
    publication_result: dict
    reproduction_confidence: ReproductionConfidence
    force_draft_pr: bool
    validation_exhausted: bool
    reinvestigation_exhausted: bool
    human_review_files: list[str]
    errors: list[dict]


def model_to_state(model: RunStateModel) -> RunState:
    return model.model_dump(exclude_none=False)


def state_to_model(state: RunState) -> RunStateModel:
    return RunStateModel(**{k: v for k, v in state.items() if k in RunStateModel.model_fields})
