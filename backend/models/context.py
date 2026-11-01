"""Schemas for the A5.5 Context Engineering layer.

The `ContextPackage` is the sole artifact A5.5 produces and the only repair
context A6 and A7 are meant to consume. It is deterministic: the same run state
against the same repository commit yields a byte-identical package, and every
included function carries the score breakdown that put it there.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RankingSignal = Literal[
    "root_cause_confidence",
    "reproduction_evidence",
    "target_resolution",
    "sig_distance",
    "import_distance",
    "call_graph_distance",
    "static_finding",
    "mutation_relevance",
    "runtime_stack",
    "git_churn",
    "dependency_centrality",
    "recent_modification",
    "shared_utility_penalty",
]

RedactionKind = Literal[
    "REDACTED_SECRET",
    "REDACTED_TOKEN",
    "REDACTED_KEY",
    "REDACTED_PASSWORD",
    "REDACTED_CERTIFICATE",
    "REDACTED_EMAIL",
    "REDACTED_PII",
]

ExtractionKind = Literal[
    "target_function",
    "called_function",
    "caller_function",
    "dependent_function",
    "class_definition",
    "dataclass",
    "typed_model",
    "validation_helper",
    "config_object",
    "constant",
    "utility",
]


class RankedContextFile(BaseModel):
    """One candidate file with the deterministic score that ranked it."""

    file: str
    score: float = 0.0
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    signals: dict[str, float] = Field(default_factory=dict)
    is_target: bool = False


class ExtractedSymbol(BaseModel):
    """A single AST-extracted span of source."""

    name: str
    qualname: str = ""
    file: str = ""
    kind: ExtractionKind = "utility"
    lineno: int = 0
    end_lineno: int = 0
    source: str = ""
    signature_only: bool = False
    docstring: str | None = None
    reason: str = ""

    @property
    def line_count(self) -> int:
        return self.source.count("\n") + 1 if self.source else 0


class Redaction(BaseModel):
    """One masked secret. Enough to audit, never enough to recover the value."""

    file: str = ""
    line: int = 0
    kind: RedactionKind = "REDACTED_SECRET"
    detector: str = ""
    identifier: str = ""


class ContextMetrics(BaseModel):
    """Observability for one context build."""

    files_ranked: int = 0
    files_extracted: int = 0
    context_files: int = 0
    context_functions: int = 0
    context_lines: int = 0

    original_tokens: int = 0
    reduced_tokens: int = 0
    estimated_prompt_tokens: int = 0
    estimated_saved_tokens: int = 0
    token_reduction: float = 0.0

    ranking_time_ms: int = 0
    extraction_time_ms: int = 0
    privacy_time_ms: int = 0
    build_time_ms: int = 0

    cache_hit: bool = False
    privacy_redactions: int = 0
    budget_chars: int = 0
    degraded: bool = False
    #: Why `ContextPackage.prefer_focused` came out the way it did, in the
    #: adoption rule's own terms — derived from the real sizes and redaction
    #: count `build_package` already computed at the point it decides adoption,
    #: never invented after the fact. Empty on a run predating this field.
    adoption_reason: str = ""


class ContextPackage(BaseModel):
    """Minimal, semantically complete repair context for one target file."""

    schema_version: str = "1.0"
    ranking_version: str = "v1"
    cache_key: str = ""

    # -- what is being repaired
    root_cause_summary: str = ""
    runtime_evidence: dict = Field(default_factory=dict)
    target_file: str = ""
    target_function: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)

    # -- extracted code, by kind
    relevant_imports: list[str] = Field(default_factory=list)
    relevant_classes: list[ExtractedSymbol] = Field(default_factory=list)
    relevant_functions: list[ExtractedSymbol] = Field(default_factory=list)
    related_utilities: list[ExtractedSymbol] = Field(default_factory=list)
    constants: list[ExtractedSymbol] = Field(default_factory=list)

    # -- surrounding facts
    dependency_summary: list[str] = Field(default_factory=list)
    contracts: list[str] = Field(default_factory=list)
    validation_requirements: list[str] = Field(default_factory=list)
    patch_constraints: list[str] = Field(default_factory=list)
    expected_output_format: str = ""

    # -- rendered views
    focused_context: str = ""

    # Whether A7 should actually use `focused_context` in place of its own
    # extraction. False means the package was built and measured but did not
    # earn adoption for this file, so A7 keeps its existing path. The package is
    # still stored — its ranking, evidence and metrics remain useful.
    prefer_focused: bool = True

    # Populated at build time for A7's reconstruction contract, but excluded
    # from the stored/cached payload: it is a verbatim copy of a file already on
    # disk, and persisting it would double the Redis footprint of every run.
    original_complete_file: str = Field(default="", exclude=True)

    ranked_files: list[RankedContextFile] = Field(default_factory=list)
    redactions: list[Redaction] = Field(default_factory=list)
    privacy_guard_status: Literal["clean", "masked", "failed"] = "clean"
    metrics: ContextMetrics = Field(default_factory=ContextMetrics)

    def to_storage_dict(self) -> dict:
        """JSON form for Redis. Omits `original_complete_file` by construction."""
        return self.model_dump(mode="json")

    def ranked_paths(self) -> list[str]:
        return [f.file for f in self.ranked_files]
