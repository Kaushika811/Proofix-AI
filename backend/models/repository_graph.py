"""Schemas for the persistent Repository Intelligence Layer.

Everything here is repository knowledge that outlives a single run. A run
consumes it; a run does not own it. All of it is derived deterministically from
the working tree and local git history — no LLM, no embeddings, no network.

The layer is strictly additive to the pipeline: `RepositoryIntelligence` is
published to the run store under its own key, and every consumer treats its
absence as "carry on exactly as before".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.services.python_ast_parser import ParsedModule
from backend.services.workspace_layout import WorkspaceLayout

SCHEMA_VERSION = "1.0"

NodeKind = Literal[
    "repository",
    "package",
    "directory",
    "file",
    "class",
    "function",
    "method",
    "variable",
    "test",
    "configuration",
]

EdgeKind = Literal[
    "contains",
    "imports",
    "calls",
    "inherits",
    "implements",
    "references",
    "tests",
    "defines",
    "depends_on",
]


# ---------------------------------------------------------------- repository


class RepoNode(BaseModel):
    """One addressable entity in the repository.

    `id` is a stable, human-readable address: ``file:pkg/auth.py``,
    ``function:pkg/auth.py::validate``, ``class:pkg/auth.py::Session``. Stability
    matters — incremental reindexing replaces nodes by id.
    """

    id: str
    kind: NodeKind
    name: str
    file: str = ""
    lineno: int = 0
    end_lineno: int = 0
    parent: str | None = None
    attributes: dict = Field(default_factory=dict)


class RepoEdge(BaseModel):
    source: str
    target: str
    kind: EdgeKind
    attributes: dict = Field(default_factory=dict)


class RepositoryGraph(BaseModel):
    """Structural knowledge: what exists and how it is arranged."""

    repo_path: str = ""
    source_roots: list[str] = Field(default_factory=list)
    nodes: dict[str, RepoNode] = Field(default_factory=dict)
    edges: list[RepoEdge] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def nodes_of(self, kind: NodeKind) -> list[RepoNode]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def edges_of(self, kind: EdgeKind) -> list[RepoEdge]:
        return [e for e in self.edges if e.kind == kind]

    def nodes_in_file(self, file: str) -> list[RepoNode]:
        return [n for n in self.nodes.values() if n.file == file]


# ---------------------------------------------------------------- call graph


class CallSite(BaseModel):
    """One resolved call edge."""

    caller: str
    callee: str
    is_async: bool = False
    via_decorator: bool = False
    via_attribute: bool = False
    is_recursive: bool = False


class CallGraphNode(BaseModel):
    """A callable with its resolved neighbourhood."""

    id: str
    name: str
    qualname: str = ""
    file: str = ""
    is_async: bool = False
    is_method: bool = False
    decorators: list[str] = Field(default_factory=list)

    incoming_calls: list[str] = Field(default_factory=list)
    outgoing_calls: list[str] = Field(default_factory=list)
    call_depth: int = 0
    fan_in: int = 0
    fan_out: int = 0
    is_recursive: bool = False


class CallGraph(BaseModel):
    nodes: dict[str, CallGraphNode] = Field(default_factory=dict)
    call_sites: list[CallSite] = Field(default_factory=list)
    unresolved_calls: dict[str, int] = Field(default_factory=dict)

    def for_file(self, file: str) -> list[CallGraphNode]:
        return [n for n in self.nodes.values() if n.file == file]

    def fan_in_for_file(self, file: str) -> int:
        return sum(n.fan_in for n in self.for_file(file))

    def fan_out_for_file(self, file: str) -> int:
        return sum(n.fan_out for n in self.for_file(file))


# ----------------------------------------------------------------- ownership


class FileOwnership(BaseModel):
    """Who maintains a file, derived from local git history only."""

    file: str
    primary_author: str = ""
    primary_author_share: float = 0.0
    secondary_author: str = ""
    secondary_author_share: float = 0.0
    commit_count: int = 0
    author_counts: dict[str, int] = Field(default_factory=dict)
    recent_modifications: int = 0
    last_modified: datetime | None = None
    days_since_modified: int | None = None
    is_hot: bool = False
    ownership_confidence: float = 0.0


class OwnershipGraph(BaseModel):
    files: dict[str, FileOwnership] = Field(default_factory=dict)
    hot_files: list[str] = Field(default_factory=list)
    authors: dict[str, int] = Field(default_factory=dict)
    window_days: int = 90

    def confidence_for(self, file: str) -> float:
        entry = self.files.get(file)
        return entry.ownership_confidence if entry else 0.0


# --------------------------------------------------------------- git history


class CommitRecord(BaseModel):
    sha: str
    author: str = ""
    committed_at: datetime | None = None
    message_summary: str = ""
    files: list[str] = Field(default_factory=list)
    is_fix: bool = False


class FileEvolution(BaseModel):
    file: str
    commit_count: int = 0
    fix_commit_count: int = 0
    churn: float = 0.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    previous_paths: list[str] = Field(default_factory=list)
    deleted: bool = False


class GitHistoryGraph(BaseModel):
    commits: list[CommitRecord] = Field(default_factory=list)
    evolution: dict[str, FileEvolution] = Field(default_factory=dict)
    renames: dict[str, str] = Field(default_factory=dict)
    deletions: list[str] = Field(default_factory=list)
    co_change: dict[str, dict[str, int]] = Field(default_factory=dict)
    window_days: int = 180

    def churn_for(self, file: str) -> float:
        entry = self.evolution.get(file)
        return entry.churn if entry else 0.0

    def co_changed_with(self, file: str, limit: int = 5) -> list[tuple[str, int]]:
        partners = self.co_change.get(file, {})
        return sorted(partners.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


# ------------------------------------------------------------- documentation


class DocumentEntry(BaseModel):
    """One indexed documentation source."""

    path: str
    kind: Literal["readme", "markdown", "docstring", "comment"] = "markdown"
    title: str = ""
    topics: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    linked_modules: list[str] = Field(default_factory=list)
    referenced_functions: list[str] = Field(default_factory=list)
    excerpt: str = ""


class DocumentationIndex(BaseModel):
    entries: list[DocumentEntry] = Field(default_factory=list)
    by_module: dict[str, list[int]] = Field(default_factory=dict)
    topics: dict[str, int] = Field(default_factory=dict)

    def entries_for_module(self, module: str) -> list[DocumentEntry]:
        return [self.entries[i] for i in self.by_module.get(module, []) if i < len(self.entries)]

    def relevance_for(self, module: str) -> float:
        """0..1 — how documented a module is. A ranking signal, nothing more."""
        count = len(self.by_module.get(module, []))
        if count <= 0:
            return 0.0
        return min(1.0, count / 3.0)


# --------------------------------------------------------------- repair memory


class RepairRecord(BaseModel):
    """One historical repair outcome. Deterministic metadata, never prose."""

    repair_id: str
    repository_hash: str = ""
    file: str = ""
    file_hash: str = ""
    function: str | None = None
    function_hash: str = ""

    bug_type: str = "unknown"
    root_cause_summary: str = ""
    affected_files: list[str] = Field(default_factory=list)

    validation_passed: bool = False
    mutation_score: float | None = None
    mutation_status: str = "not_run"
    security_score: float | None = None
    retry_count: int = 0
    pr_type: str = ""

    accepted_patch_diff: str = ""
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class RepairMemory(BaseModel):
    repository_id: str = ""
    records: list[RepairRecord] = Field(default_factory=list)

    def for_file(self, file: str) -> list[RepairRecord]:
        return [r for r in self.records if r.file == file]

    def successful(self) -> list[RepairRecord]:
        return [r for r in self.records if r.validation_passed]


class RepairQuery(BaseModel):
    """What the current run knows about the repair it is attempting."""

    repository_hash: str = ""
    file: str = ""
    file_hash: str = ""
    function: str | None = None
    function_hash: str = ""
    bug_type: str = "unknown"


class RepairMatch(BaseModel):
    """A historical repair judged similar, with the reasons it matched.

    `similarity` is a sum of named, fixed weights — never a learned score — so a
    match is always explainable from `matched_on` alone.
    """

    record: RepairRecord
    similarity: float = 0.0
    matched_on: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ envelope


class IndexDelta(BaseModel):
    """What changed since the cached index was built."""

    added: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    renamed: dict[str, str] = Field(default_factory=dict)
    unchanged: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.deleted or self.modified or self.renamed)

    @property
    def touched(self) -> list[str]:
        return sorted({*self.added, *self.modified, *self.renamed.values()})


class RepositoryIntelligenceMetrics(BaseModel):
    repository_nodes: int = 0
    repository_edges: int = 0
    call_graph_nodes: int = 0
    call_graph_edges: int = 0
    ownership_entries: int = 0
    documentation_entries: int = 0
    repair_memory_entries: int = 0
    git_commits_indexed: int = 0

    cache_hits: int = 0
    cache_misses: int = 0
    incremental_updates: int = 0
    files_added: int = 0
    files_deleted: int = 0
    files_modified: int = 0
    files_renamed: int = 0
    full_rebuild: bool = False

    graph_build_ms: int = 0
    call_graph_ms: int = 0
    ownership_ms: int = 0
    history_ms: int = 0
    documentation_ms: int = 0
    total_ms: int = 0
    index_size: int = 0


class RepositoryIntelligence(BaseModel):
    """The published artifact. Consumers must treat its absence as normal."""

    schema_version: str = SCHEMA_VERSION
    repository_hash: str = ""
    repository_id: str = ""
    head_sha: str = ""
    source_roots: list[str] = Field(default_factory=list)

    # Enterprise layout: monorepo packages, nested repositories excluded from
    # this index, and the language mix. Defaulted, so an index written before
    # this existed still deserializes.
    workspace: WorkspaceLayout = Field(default_factory=WorkspaceLayout)

    repository_graph: RepositoryGraph = Field(default_factory=RepositoryGraph)
    call_graph: CallGraph = Field(default_factory=CallGraph)
    ownership: OwnershipGraph = Field(default_factory=OwnershipGraph)
    history: GitHistoryGraph = Field(default_factory=GitHistoryGraph)
    documentation: DocumentationIndex = Field(default_factory=DocumentationIndex)
    repair_memory: RepairMemory = Field(default_factory=RepairMemory)

    # Reserved. Deliberately unpopulated — Phase 4 territory.
    embedding_index: dict = Field(default_factory=dict)

    # Parsed AST metadata per file, carried so an incremental update can skip
    # re-parsing untouched files and A1 can reuse the parse instead of repeating
    # it. This is the bulk of `index_size`; `repository_index_max_files` bounds it.
    parsed_modules: dict[str, ParsedModule] = Field(default_factory=dict)

    file_hashes: dict[str, str] = Field(default_factory=dict)
    delta: IndexDelta = Field(default_factory=IndexDelta)
    metrics: RepositoryIntelligenceMetrics = Field(default_factory=RepositoryIntelligenceMetrics)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
