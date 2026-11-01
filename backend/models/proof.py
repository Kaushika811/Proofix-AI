from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


VerificationStepName = Literal[
    "reproduction_before",
    "reproduction_after",
    "mutation_test",
    "security_delta",
]

ReproductionConfidence = Literal["exact_test", "full_suite"]


class VerificationStep(BaseModel):
    name: VerificationStepName
    command: str
    base_commit: str
    patch_commit: str
    expected_result: str
    timeout_seconds: int
    is_targeted: bool = True


class VerificationBundle(BaseModel):
    issue_id: str
    steps: list[VerificationStep] = Field(default_factory=list)
    bundle_hash: str = ""
    llm_involved_in_verification: Literal[False] = False
    reproduction_confidence: ReproductionConfidence = "full_suite"
    created_at: datetime = Field(default_factory=datetime.utcnow)
