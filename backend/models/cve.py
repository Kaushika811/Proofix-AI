from typing import Literal

from pydantic import BaseModel, Field


CVEClassification = Literal["Critical", "Informational", "Unknown"]


class CVERecord(BaseModel):
    package: str
    cve_id: str
    severity: str
    installed_version: str | None = None
    affected_symbol: str | None = None
    reachable: bool | None = None
    reach_path: list[str] | None = None
    classification: CVEClassification = "Unknown"


class CVEReachabilityReport(BaseModel):
    findings: list[CVERecord] = Field(default_factory=list)
    critical_queue: list[str] = Field(default_factory=list)
    # Every package A2 parsed from the manifest, whether or not OSV reported an
    # advisory for it — `len(findings)` alone cannot answer "how many
    # dependencies did A2 actually look at".
    total_dependencies: int = 0
    manifest: str | None = None
    ecosystem: str | None = None
