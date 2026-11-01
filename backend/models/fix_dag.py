from typing import Literal

from pydantic import BaseModel, Field

#: Which path actually produced `FixDAGPlan.execution_order`.
#:
#: `deterministic` is `topological_execution_order` — a real topological sort
#: over `dependency_edges`, or (when the graph has a cycle) a CVE-first
#: fallback. `llm` means the configured model returned an order and it was
#: used as-is, unvalidated against the dependency edges. Persisted so the UI
#: never has to guess, and never has to invent, which one happened.
OrderingSource = Literal["llm", "deterministic"]


class FixNode(BaseModel):
    issue_id: str
    files: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class DependencyEdge(BaseModel):
    from_issue: str
    to_issue: str
    reason: str = ""


class FixDAGPlan(BaseModel):
    nodes: list[FixNode] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)
    conflict_batches: list[list[str]] = Field(default_factory=list)
    dependency_edges: list[DependencyEdge] = Field(default_factory=list)
    #: `None` on a run predating this field. Never defaulted to
    #: `"deterministic"` for an unknown run — that would fabricate a fact this
    #: model did not actually record at the time.
    ordering_source: OrderingSource | None = None
    #: The LLM's own account of why it chose this order, when it was asked and
    #: it answered. Empty when the deterministic path ran (there is no LLM to
    #: explain itself) or when the LLM returned an order without one.
    ordering_rationale: str = ""
    #: `topological_execution_order(nodes, dependency_edges)`, computed
    #: unconditionally — never gated on `ordering_source`. When the LLM path
    #: produced `execution_order`, this is the independent, dependency-respecting
    #: order the graph itself supports, so a client can show where the two agree
    #: or diverge instead of asking the reader to trust the LLM's order on faith.
    #: Equal to `execution_order` whenever `ordering_source == "deterministic"`.
    deterministic_order: list[str] = Field(default_factory=list)
