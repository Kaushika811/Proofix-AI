from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


FileRole = Literal[
    "auth-boundary",
    "data-access",
    "public-api",
    "config-surface",
    "test-only",
    "internal-util",
]


class FileNode(BaseModel):
    path: str
    role: FileRole = "internal-util"
    imports: list[str] = Field(default_factory=list)
    imported_by: list[str] = Field(default_factory=list)
    churn_weight: float = 0.0
    criticality: float = 0.4


class SemanticIntentGraph(BaseModel):
    repo_path: str
    source_roots: list[str] = Field(default_factory=list)
    files: dict[str, FileNode] = Field(default_factory=dict)
    edges: list[tuple[str, str]] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
