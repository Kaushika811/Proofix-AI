"""Schemas for the Repository Knowledge Graph.

The RKG is a *view*, not a store. Its nodes carry identity and a small set of
attributes needed for traversal; the substance stays in the six structures
`RepositoryIntelligence` already holds. A `KGNode` for a function does not copy
the function's source, its span, or its call lists — it points at them.

That is the whole design constraint. Duplicating the underlying graphs would
create two sources of truth that drift the moment one is updated incrementally
and the other is not.

What the RKG genuinely adds is **edges between structures that previously could
not see each other**: a commit to the functions it modified, a document to the
function it describes, a repair to the code it fixed, a test to the function it
validates. Those edges exist nowhere else, so they are materialized here.

Every edge carries `provenance` — which source structure justified it — and
`evidence`, a human-readable reason. Nothing in this layer may produce a score
or a recommendation that cannot name the edges behind it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

NodeType = Literal[
    "repository",
    "package",
    "file",
    "class",
    "function",
    "method",
    "api",
    "test",
    "config",
    "owner",
    "commit",
    "document",
    "repair",
    "capability",
    # Phase 6 learning nodes. Additive: a graph built without the learning
    # layer simply has none of these, and every query degrades to its
    # pre-Phase-6 result.
    "framework",
    "style",
    "review",
    "outcome",
    "organization",
    "pattern",
    "template",
]

EdgeType = Literal[
    # structure
    "CONTAINS",
    "DEFINES",
    "IMPORTS",
    "DEPENDS_ON",
    "INHERITS",
    "IMPLEMENTS",
    "REFERENCES",
    # behaviour
    "CALLS",
    "EXPOSES",
    # verification
    "TESTS",
    "VALIDATES",
    # history
    "MODIFIED",
    "AUTHORED",
    "OWNS",
    "CO_CHANGED",
    # knowledge
    "DESCRIBES",
    "FIXED",
    "AFFECTS",
    "PART_OF",
    # learning
    "USES_FRAMEWORK",
    "FOLLOWS_STYLE",
    "REVIEWED_BY",
    "RESULTED_IN",
    "INSTANCE_OF",
    "APPLIES_TEMPLATE",
    "BELONGS_TO",
]

# Which source structure an edge was adapted from. Recorded per edge so any
# downstream claim can be traced back to the analysis that produced it.
Provenance = Literal[
    "repository_graph",
    "call_graph",
    "ownership",
    "history",
    "documentation",
    "repair_memory",
    "capability",
]


class KGNode(BaseModel):
    """One addressable entity. Identity plus traversal attributes only."""

    id: str
    type: NodeType
    name: str
    file: str = ""
    qualname: str = ""
    attributes: dict = Field(default_factory=dict)


class KGEdge(BaseModel):
    """A typed, attributed, traceable relation."""

    source: str
    target: str
    type: EdgeType
    weight: float = 1.0
    provenance: Provenance = "repository_graph"
    evidence: str = ""


# ------------------------------------------------------------- explainability


class Evidence(BaseModel):
    """One reason behind a recommendation, tied to the graph that produced it."""

    signal: str
    value: float = 0.0
    contribution: float = 0.0
    detail: str = ""
    provenance: Provenance = "repository_graph"
    edges: list[str] = Field(default_factory=list)

    def describe(self) -> str:
        return f"{self.signal}={self.value:g} (+{self.contribution:g}): {self.detail}"


class Explanation(BaseModel):
    """The mandatory answer to "why?" for anything this layer recommends."""

    summary: str = ""
    evidence: list[Evidence] = Field(default_factory=list)

    @property
    def signals(self) -> list[str]:
        return [e.signal for e in self.evidence]

    @property
    def edges(self) -> list[str]:
        return sorted({edge for e in self.evidence for edge in e.edges})

    def reasons(self) -> list[str]:
        return [e.describe() for e in self.evidence]


# ---------------------------------------------------------------- capabilities


class Capability(BaseModel):
    """A logical business grouping inferred from deterministic surface signals.

    `confidence` reports how much independent evidence supports the grouping.
    A capability inferred from a filename alone is reported at low confidence
    rather than being suppressed — the caller decides what threshold to trust.
    """

    name: str
    slug: str
    files: list[str] = Field(default_factory=list)
    entry_points: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    signal_counts: dict[str, int] = Field(default_factory=dict)
    explanation: Explanation = Field(default_factory=Explanation)


# ----------------------------------------------------------------------- risk

RiskBand = Literal["low", "moderate", "elevated", "high"]


class RiskAssessment(BaseModel):
    """Risk for one module, with every contributing signal itemised."""

    module: str
    risk: float = 0.0
    band: RiskBand = "low"
    reason: str = ""
    explanation: Explanation = Field(default_factory=Explanation)

    @property
    def evidence(self) -> list[Evidence]:
        return self.explanation.evidence


# ------------------------------------------------------------------ hotspots

HotspotKind = Literal[
    "god_object",
    "circular_dependency",
    "over_centralized_utility",
    "dead_module",
    "orphan_file",
    "unowned_module",
    "high_risk_api",
    "unused_code",
]


class Hotspot(BaseModel):
    """One architectural finding, always with the evidence that produced it."""

    kind: HotspotKind
    target: str
    severity: float = 0.0
    summary: str = ""
    members: list[str] = Field(default_factory=list)
    explanation: Explanation = Field(default_factory=Explanation)


# ------------------------------------------------------------------- metrics


class KnowledgeGraphMetrics(BaseModel):
    node_count: int = 0
    edge_count: int = 0
    average_degree: float = 0.0
    nodes_by_type: dict[str, int] = Field(default_factory=dict)
    edges_by_type: dict[str, int] = Field(default_factory=dict)

    build_ms: int = 0
    incremental_update_ms: int = 0
    incremental_updates: int = 0
    nodes_rebuilt: int = 0

    query_count: int = 0
    query_total_ms: float = 0.0
    cache_hits: int = 0
    cache_misses: int = 0

    memory_bytes: int = 0
    repository_coverage: float = 0.0
    files_total: int = 0
    files_represented: int = 0

    @property
    def average_query_ms(self) -> float:
        return round(self.query_total_ms / self.query_count, 4) if self.query_count else 0.0

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return round(self.cache_hits / total, 4) if total else 0.0


class KnowledgeGraphSummary(BaseModel):
    """The small, storable projection published alongside the run.

    The adjacency itself is rebuilt on demand from the cached index — it is
    derived data, and storing it would be the duplication this layer exists to
    avoid.
    """

    schema_version: str = SCHEMA_VERSION
    repository_hash: str = ""
    metrics: KnowledgeGraphMetrics = Field(default_factory=KnowledgeGraphMetrics)
    capabilities: list[Capability] = Field(default_factory=list)
    top_risks: list[RiskAssessment] = Field(default_factory=list)
    hotspots: list[Hotspot] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
