"""`services/blast_traversal.py` — edges, direction accumulation, edge basis.

These are the fields A5's new impact projection depends on: without a real
`BlastEdge` per hop there is nothing to draw a propagation graph from, and
without an honest `basis` per edge a name-collision false positive
(`import os` matching `services/oslo.py`) would render identically to a
precise match.
"""

from backend.models.blast import BlastGraphResult
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.blast_traversal import resolve_origins, traverse_multi_origin
from backend.services.target_resolver import TargetResolution, pin_resolved_target


def _sig(files: dict[str, FileNode], edges: list[tuple[str, str]]) -> SemanticIntentGraph:
    return SemanticIntentGraph(repo_path="/repo", source_roots=["app/"], files=files, edges=edges)


def test_edges_record_the_real_propagation_path():
    sig = _sig(
        files={
            "app/auth.py": FileNode(path="app/auth.py", imports=["config"], criticality=0.9, churn_weight=0.8),
            "app/config.py": FileNode(path="app/config.py", imports=[], criticality=0.5, churn_weight=0.5),
        },
        edges=[("app/auth.py", "config")],
    )
    result = traverse_multi_origin(sig, ["app/auth.py"])

    edge = next(e for e in result.edges if e.to_path == "app/config.py")
    assert edge.from_path == "app/auth.py"
    assert edge.direction == "forward"
    assert edge.hop_count == 1

    config = next(s for s in result.scope if s.path == "app/config.py")
    assert config.reached_via == "app/auth.py"
    assert config.hop_count == 1


def test_origin_itself_has_no_edge():
    sig = _sig(
        files={"app/auth.py": FileNode(path="app/auth.py", criticality=0.9, churn_weight=0.8)},
        edges=[],
    )
    result = traverse_multi_origin(sig, ["app/auth.py"])

    assert result.edges == []
    origin = next(s for s in result.scope if s.path == "app/auth.py")
    assert origin.reached_via is None
    assert origin.edge_basis is None
    assert origin.hop_count == 0


def test_precise_suffix_match_is_labelled_resolved_suffix():
    """`import "config"` resolving to `app/config.py` via `endswith` is precise."""
    sig = _sig(
        files={
            "app/auth.py": FileNode(path="app/auth.py", imports=["config"], criticality=0.9, churn_weight=0.8),
            "app/config.py": FileNode(path="app/config.py", criticality=0.5, churn_weight=0.5),
        },
        edges=[("app/auth.py", "config")],
    )
    result = traverse_multi_origin(sig, ["app/auth.py"])

    edge = next(e for e in result.edges if e.to_path == "app/config.py")
    assert edge.basis == "resolved_suffix"


def test_loose_containment_match_is_labelled_name_contains():
    """A module name that only appears *inside* a path, never at its tail.

    This is exactly the class of false edge the architecture review flagged:
    `import "the"` should not silently read as a verified resolution of
    `app/theme.py`.
    """
    sig = _sig(
        files={
            "app/auth.py": FileNode(path="app/auth.py", imports=["the"], criticality=0.9, churn_weight=0.8),
            "app/theme.py": FileNode(path="app/theme.py", criticality=0.5, churn_weight=0.5),
        },
        edges=[("app/auth.py", "the")],
    )
    result = traverse_multi_origin(sig, ["app/auth.py"])

    edge = next(e for e in result.edges if e.to_path == "app/theme.py")
    assert edge.basis == "name_contains"


def test_precise_match_is_preferred_over_loose_when_both_exist():
    """`the` would loosely match `app/theme.py` too — the precise file must win."""
    sig = _sig(
        files={
            "app/auth.py": FileNode(path="app/auth.py", imports=["the"], criticality=0.9, churn_weight=0.8),
            "app/theme.py": FileNode(path="app/theme.py", criticality=0.5, churn_weight=0.5),
            "app/the.py": FileNode(path="app/the.py", criticality=0.5, churn_weight=0.5),
        },
        edges=[("app/auth.py", "the")],
    )
    result = traverse_multi_origin(sig, ["app/auth.py"])

    resolved = [e for e in result.edges if e.basis == "resolved_suffix"]
    assert len(resolved) == 1
    assert resolved[0].to_path == "app/the.py"
    assert not any(e.to_path == "app/theme.py" for e in result.edges)


def test_bidirectional_reach_is_not_collapsed_to_one_direction():
    """A cyclic import: origin is reachable from B both forward and backward.

    The pre-existing `direction` field only ever kept whichever entry scored
    higher, silently dropping the other direction. `directions` must keep both.
    """
    sig = _sig(
        files={
            "app/a.py": FileNode(path="app/a.py", imports=["b"], criticality=0.9, churn_weight=0.8),
            "app/b.py": FileNode(path="app/b.py", imports=["a"], criticality=0.6, churn_weight=0.6),
        },
        edges=[("app/a.py", "b"), ("app/b.py", "a")],
    )
    result = traverse_multi_origin(sig, ["app/a.py"])

    b = next(s for s in result.scope if s.path == "app/b.py")
    assert set(b.directions) == {"forward", "backward"}


def test_single_direction_file_reports_only_that_direction():
    sig = _sig(
        files={
            "app/a.py": FileNode(path="app/a.py", imports=["b"], criticality=0.9, churn_weight=0.8),
            "app/b.py": FileNode(path="app/b.py", criticality=0.6, churn_weight=0.6),
        },
        edges=[("app/a.py", "b")],
    )
    result = traverse_multi_origin(sig, ["app/a.py"])

    b = next(s for s in result.scope if s.path == "app/b.py")
    assert b.directions == ["forward"]


def test_no_origins_returns_an_empty_result_not_none():
    result = traverse_multi_origin(SemanticIntentGraph(repo_path="/repo"), [])
    assert result == BlastGraphResult()
    assert result.edges == []
    assert result.target_resolution is None


def test_pinning_reports_it_happened_and_the_pinned_file_is_forward_only():
    result = BlastGraphResult()
    target = TargetResolution(
        original_path="app/auth.py",
        normalized_path="app/auth.py",
        resolved_application_path="app/auth.py",
        resolution_source="stack_trace",
        confidence=0.9,
    )

    pinned = pin_resolved_target(result, target, runtime_confirmed=True)

    assert pinned is True
    origin = next(s for s in result.scope if s.path == "app/auth.py")
    assert origin.directions == ["forward"]


def test_pinning_is_a_no_op_without_runtime_confirmation():
    result = BlastGraphResult()
    target = TargetResolution(
        original_path="app/auth.py",
        normalized_path="app/auth.py",
        resolved_application_path="app/auth.py",
        resolution_source="sig_lookup",
        confidence=0.4,
    )

    pinned = pin_resolved_target(result, target, runtime_confirmed=False)

    assert pinned is False
    assert result.scope == []


# -- resolve_origins: vendor/dependency paths must never become blast origins --


def test_resolve_origins_drops_a_verified_citation_inside_venv_site_packages():
    """The bug this pins: a bandit finding inside an installed dependency,
    verified only because the location genuinely exists on disk, must not
    become the blast origin — and therefore never the repair target."""
    citations = [
        {
            "file": "vuln-demo/.venv/Lib/site-packages/httpx/_auth.py",
            "line": 309,
            "verified": True,
        }
    ]
    assert resolve_origins(citations) == []


def test_resolve_origins_drops_vendor_citations_even_unverified():
    citations = [{"file": "node_modules/lodash/lodash.js", "verified": False}]
    assert resolve_origins(citations) == []


def test_resolve_origins_keeps_application_citations_alongside_a_dropped_vendor_one():
    citations = [
        {"file": "app/auth.py", "verified": True},
        {"file": ".venv/Lib/site-packages/httpx/_auth.py", "verified": True},
    ]
    assert resolve_origins(citations) == ["app/auth.py"]


def test_resolve_origins_still_prefers_verified_application_citations():
    citations = [
        {"file": "app/auth.py", "verified": True},
        {"file": "app/legacy.py", "verified": False},
    ]
    assert resolve_origins(citations) == ["app/auth.py"]


def test_resolve_origins_falls_back_to_unverified_application_citations_only():
    citations = [
        {"file": "app/legacy.py", "verified": False},
        {"file": "site-packages/requests/auth.py", "verified": False},
    ]
    assert resolve_origins(citations) == ["app/legacy.py"]
