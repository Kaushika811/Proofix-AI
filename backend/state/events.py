from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.models.pr import AxisScores


AgentStatus = Literal["started", "progress", "completed", "failed", "retry"]
PRType = Literal["auto_mergeable", "diff_only", "draft"]


class A10CompletedPayload(BaseModel):
    """Payload shape for A10 ``completed`` events (also persisted on ``AgentStatusEvent.payload``)."""

    pr_type: PRType
    axis_scores: AxisScores
    pr_url: str | None = None
    proof_bundle_hash: str | None = None
    reproduction_confidence: str | None = None


class AgentStatusEvent(BaseModel):
    run_id: str
    agent_id: str
    status: AgentStatus
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    message: str = ""
    payload: dict | None = None  # A10 completed: see ``A10CompletedPayload``
    sequence: int = 0


# `run.blocked` is a third terminal outcome, distinct from both. The pipeline
# neither completed a repair nor failed doing one — it determined the target was
# not analysable (dependencies not installed, unsupported language) and declined
# to continue. Reporting that as `run.failed` blamed the repository for the
# environment; reporting it as `run.completed` claimed work that never happened.
RunLifecycleType = Literal["run.started", "run.completed", "run.failed", "run.blocked"]


class RunLifecycleEvent(BaseModel):
    """A run beginning or ending, stated outright.

    Before this existed a client could only infer that a run was over: the last
    agent simply stopped emitting, which is indistinguishable from a dropped
    connection, and inferring completion from A10 never fired for a run that
    failed earlier. These events are the authoritative lifecycle signal.

    Carried on its own Redis list so `get_events()` — and therefore every
    existing client — keeps seeing exactly the agent timeline it always has,
    while both kinds share one pub/sub channel for live delivery.
    """

    type: RunLifecycleType
    run_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    # Present on `run.completed`: the routing outcome the pipeline reached.
    decision: PRType | None = None
    decision_label: str = ""
    # Present on `run.failed`: why it ended.
    reason: str | None = None
    # Monotonic per run, continuing the agent-event sequence so a client can
    # order lifecycle and agent frames on one axis.
    sequence: int = 0
