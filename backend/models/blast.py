from typing import Literal

from pydantic import BaseModel, Field

#: How a traversal edge's target file was resolved from an import string.
#:
#: Both traversal directions in `blast_traversal.py` resolve a raw import
#: string (e.g. `"app.auth"`) against SIG file paths — there is no import
#: resolver, only string matching. `resolved_suffix` is the precise branch
#: (`path.endswith(f"{module}.py")` / `stem.endswith(imp)`): the import string
#: names the end of a real file path, which is unambiguous for any
#: non-relative import. `name_contains` is the loose branch (plain
#: containment): `import os` naming `services/oslo.py` is exactly the false
#: edge this label exists to flag. Neither branch is "verified" against a real
#: import resolver — that distinction does not exist in this codebase, and
#: pretending backward edges are more trustworthy than forward ones would be
#: fabricating a precision the traversal does not have.
EdgeBasis = Literal["resolved_suffix", "name_contains"]

#: Where A5 got the file it treats as the origin of the blast. Mirrors
#: `target_resolver.ResolutionSource` as a plain string so this model has no
#: import-time dependency on that module.
ResolutionSource = Literal[
    "issue_contract", "stack_trace", "root_cause", "sig_lookup", "import_mapping", "fallback"
]


class BlastEdge(BaseModel):
    """One real traversal step: `from_path` reached `to_path` at `hop_count`.

    This is the propagation *path* the rest of the graph discards — `scope`
    below keeps only the winning hop count and confidence per file, not how it
    was reached. Recording edges is what makes it possible to draw the impact
    graph honestly instead of a distance-only ring.
    """

    from_path: str
    to_path: str
    direction: Literal["forward", "backward"]
    basis: EdgeBasis
    hop_count: int


class TargetResolutionSummary(BaseModel):
    """A5's own account of where the blast starts, carried through to state.

    Previously computed in `a5_blast_graph.py` and attached only to the
    transient WS event payload, where it rolls off after 500 events and is
    invisible to a client opening a finished run. This is the same data,
    persisted.
    """

    original_path: str
    normalized_path: str
    resolved_path: str | None
    source: ResolutionSource
    confidence: float
    runtime_confirmed: bool
    #: True when `target_resolver.pin_resolved_target` forced this path into
    #: `auto_patch_scope` regardless of its propagation confidence — the reason
    #: a file can legitimately appear in both `auto_patch_scope` and
    #: `human_review_required`.
    pinned: bool = False


class ScopedFile(BaseModel):
    path: str
    #: The direction this file's *winning* entry was scored under — unchanged
    #: from the original field, since `pin_resolved_target` and existing
    #: callers construct it directly.
    direction: Literal["forward", "backward"]
    #: Every direction this file was actually reached from, forward and/or
    #: backward, across every origin. A file reached both ways is a real,
    #: distinct fact from being reached one way — `direction` alone collapses
    #: that. Additive: defaults to `[direction]` when unset.
    directions: list[Literal["forward", "backward"]] = Field(default_factory=list)
    propagation_confidence: float = 0.0
    risk_score: float = 0.0
    hop_count: int = 0
    origin: str = ""
    #: The file this one was reached through, for its winning entry. `None`
    #: for an origin itself (hop 0) or a pinned target with no traversal edge.
    reached_via: str | None = None
    #: How `reached_via` resolved to this path. `None` exactly when
    #: `reached_via` is `None`.
    edge_basis: EdgeBasis | None = None


class BlastGraphResult(BaseModel):
    scope: list[ScopedFile] = Field(default_factory=list)
    human_review_required: list[str] = Field(default_factory=list)
    auto_patch_scope: list[str] = Field(default_factory=list)
    origins: list[str] = Field(default_factory=list)
    #: Every traversal step recorded during the BFS — the propagation path
    #: `scope` alone cannot reconstruct. Empty for a run predating this field.
    edges: list[BlastEdge] = Field(default_factory=list)
    #: `None` before A5 has resolved a target, or when it has none (no SIG, no
    #: citations).
    target_resolution: TargetResolutionSummary | None = None
