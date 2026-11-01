from pydantic import BaseModel, Field


class Finding(BaseModel):
    id: str
    file: str
    line: int
    message: str = ""
    tools: list[str] = Field(default_factory=list)
    severity: float = 0.0
    blast_radius_score: float = 0.0
    consensus: bool = False
    # The two hidden factors of `blast_radius_score` — `_rank_findings` already
    # computes both, this just keeps them instead of discarding them.
    criticality: float = 0.0
    churn_weight: float = 0.0
    # True only when every contributing tool reported a real severity (today:
    # bandit alone). semgrep/ruff assign a constant, not a measurement — a
    # finding whose only tool is one of those must say so, not display a
    # severity band no tool actually assigned.
    severity_measured: bool = False


class StaticAnalysisReport(BaseModel):
    raw_count: int = 0
    prioritized: list[Finding] = Field(default_factory=list)
    baseline_json: dict = Field(default_factory=dict)
    # Per-scanner outcome: "ok" | "ok_no_findings" | "unavailable" | "stubbed".
    # A3 already computes this per run (`_status_label`) but previously only
    # emitted it on the transient event, where it rolls off after 500 events
    # and is invisible to a client that opens the run later.
    scanner_status: dict[str, str] = Field(default_factory=dict)
