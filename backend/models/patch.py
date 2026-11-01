from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BehavioralContract(BaseModel):
    assertion: str
    location: str


class PatchPlan(BaseModel):
    """Evidence-driven repair plan for a single file — input to LLM patch generation."""

    file: str
    root_cause: str
    required_behavior_change: str
    security_constraints: list[str] = Field(default_factory=list)
    validation_goals: list[str] = Field(default_factory=list)
    stack_evidence: str = ""
    blast_context: str = ""
    target_file: str = ""
    target_function: str | None = None
    current_behavior: str = ""
    expected_behavior: str = ""
    acceptance_criteria: str = ""
    runtime_evidence: str = ""
    failing_test: str = ""
    stack_summary: str = ""

    @model_validator(mode="after")
    def default_target_file(self) -> "PatchPlan":
        if not self.target_file:
            self.target_file = self.file
        return self


class PatchCandidate(BaseModel):
    file: str
    original: str
    patched: str
    method: Literal["ast_validated_write", "libcst"] = "ast_validated_write"
    target_symbol: str | None = None
    changed_symbols: list[str] = Field(default_factory=list)


class PatchBundle(BaseModel):
    application_evidence: dict | None = None
    issue_id: str = ""
    patches: list[PatchCandidate] = Field(default_factory=list)
    contracts: list[BehavioralContract] = Field(default_factory=list)
    style_exemplar_commit: str | None = None
    diff_text: str = ""
