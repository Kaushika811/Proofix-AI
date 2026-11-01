"""`services/ui_projection.py::build_blast_impact` — A5's projection.

Every assertion traces to a field on `BlastGraphResult` (`models/blast.py`) or
a read-only join against `state.sig` / `state.static_report`. The point of this
suite is to catch the projection inventing a role, a "risk" claim stronger than
the backend can support, or collapsing a fact (like a file being reachable both
forward and backward) that the traversal now records honestly.
"""

from backend.services.ui_projection import build_blast_impact
from backend.state.schema import RunStateModel


def _state(**overrides) -> RunStateModel:
    base = dict(run_id="r", repo_path="/repo")
    base.update(overrides)
    return RunStateModel(**base)


def _blast(**overrides) -> dict:
    base = {
        "scope": [
            {
                "path": "app/auth.py",
                "direction": "forward",
                "directions": ["forward"],
                "propagation_confidence": 1.0,
                "risk_score": 0.0,
                "hop_count": 0,
                "origin": "app/auth.py",
                "reached_via": None,
                "edge_basis": None,
            },
            {
                "path": "app/middleware.py",
                "direction": "backward",
                "directions": ["backward"],
                "propagation_confidence": 0.42,
                "risk_score": 0.18,
                "hop_count": 1,
                "origin": "app/auth.py",
                "reached_via": "app/auth.py",
                "edge_basis": "resolved_suffix",
            },
        ],
        "human_review_required": ["app/middleware.py"],
        "auto_patch_scope": ["app/auth.py"],
        "origins": ["app/auth.py"],
        "edges": [
            {
                "from_path": "app/auth.py",
                "to_path": "app/middleware.py",
                "direction": "backward",
                "basis": "resolved_suffix",
                "hop_count": 1,
            }
        ],
        "target_resolution": {
            "original_path": "/clone/app/auth.py",
            "normalized_path": "app/auth.py",
            "resolved_path": "app/auth.py",
            "source": "stack_trace",
            "confidence": 0.9,
            "runtime_confirmed": True,
            "pinned": True,
        },
    }
    base.update(overrides)
    return base


def _sig() -> dict:
    return {
        "files": {
            "app/auth.py": {
                "role": "auth-boundary",
                "criticality": 0.9,
                "churn_weight": 0.0,
                "imports": [],
                "imported_by": ["app/middleware.py"],
            },
            "app/middleware.py": {
                "role": "internal-util",
                "criticality": 0.5,
                "churn_weight": 0.0,
                "imports": ["auth"],
                "imported_by": [],
            },
        }
    }


def test_returns_none_before_a5_has_run():
    assert build_blast_impact(_state()) is None


def test_empty_scope_is_a_real_answer_not_a_missing_one():
    """A5 ran and had nothing to traverse — distinct from A5 never running."""
    result = build_blast_impact(_state(blast_graph={"scope": [], "origins": []}))
    assert result is not None
    assert result["scope"] == []
    assert result["origin"] is None


def test_origin_carries_full_resolution_provenance():
    state = _state(blast_graph=_blast(), sig=_sig())
    result = build_blast_impact(state)

    origin = result["origin"]
    assert origin["resolvedPath"] == "app/auth.py"
    assert origin["source"] == "stack_trace"
    assert origin["confidence"] == 0.9
    assert origin["runtimeConfirmed"] is True
    assert origin["pinned"] is True


def test_falls_back_to_origins_list_when_resolution_is_absent():
    """A run predating `target_resolution`, or one with no resolved target."""
    blast = _blast(target_resolution=None)
    result = build_blast_impact(_state(blast_graph=blast))

    origin = result["origin"]
    assert origin["resolvedPath"] == "app/auth.py"
    assert origin["source"] is None
    assert origin["confidence"] is None


def test_scope_is_joined_with_sig_role_and_criticality():
    state = _state(blast_graph=_blast(), sig=_sig())
    result = build_blast_impact(state)

    middleware = next(s for s in result["scope"] if s["path"] == "app/middleware.py")
    assert middleware["role"] == "internal-util"
    assert middleware["criticality"] == 0.5
    assert middleware["hopCount"] == 1
    assert middleware["directions"] == ["backward"]
    assert middleware["reachedVia"] == "app/auth.py"
    assert middleware["edgeBasis"] == "resolved_suffix"


def test_scope_join_degrades_gracefully_without_a_sig():
    """A5 can run even when the SIG could not be loaded; role/criticality are absent, not fabricated."""
    result = build_blast_impact(_state(blast_graph=_blast()))
    middleware = next(s for s in result["scope"] if s["path"] == "app/middleware.py")
    assert middleware["role"] is None
    assert middleware["criticality"] is None


def test_edges_are_projected_verbatim():
    state = _state(blast_graph=_blast(), sig=_sig())
    result = build_blast_impact(state)

    assert result["edges"] == [
        {
            "from": "app/auth.py",
            "to": "app/middleware.py",
            "direction": "backward",
            "basis": "resolved_suffix",
            "hopCount": 1,
        }
    ]


def test_patch_authority_overlap_is_reported_not_hidden():
    """`auth.py` is pinned auto-patchable and, independently, in human review."""
    blast = _blast(
        auto_patch_scope=["app/auth.py"],
        human_review_required=["app/auth.py", "app/middleware.py"],
    )
    result = build_blast_impact(_state(blast_graph=blast))

    assert result["patchAuthorityOverlap"] == ["app/auth.py"]


def test_no_overlap_is_an_empty_list():
    result = build_blast_impact(_state(blast_graph=_blast()))
    assert result["patchAuthorityOverlap"] == []


def test_static_findings_are_joined_by_file():
    state = _state(
        blast_graph=_blast(),
        static_report={"prioritized": [{"file": "app/middleware.py", "message": "x"}]},
    )
    result = build_blast_impact(state)

    by_path = {s["path"]: s for s in result["scope"]}
    assert by_path["app/middleware.py"]["hasStaticFinding"] is True
    assert by_path["app/auth.py"]["hasStaticFinding"] is False


def test_max_hop_is_the_true_maximum_not_a_constant():
    result = build_blast_impact(_state(blast_graph=_blast()))
    assert result["maxHop"] == 1


def test_max_hop_is_zero_for_an_empty_scope():
    result = build_blast_impact(_state(blast_graph={"scope": [], "origins": []}))
    assert result["maxHop"] == 0


def test_risk_is_never_labelled_as_certain():
    """The caveat is present and the field name avoids the word 'risk'."""
    result = build_blast_impact(_state(blast_graph=_blast()))
    assert "priorityScore" in result["scope"][0]
    assert "riskScore" not in result["scope"][0]
    assert "churn" in result["riskMeasurementCaveat"].lower()
    assert "not" in result["riskMeasurementCaveat"].lower()


def test_directions_preserve_bidirectional_reach():
    blast = _blast()
    blast["scope"][1]["directions"] = ["forward", "backward"]
    result = build_blast_impact(_state(blast_graph=blast))

    middleware = next(s for s in result["scope"] if s["path"] == "app/middleware.py")
    assert middleware["directions"] == ["forward", "backward"]


def test_scope_is_sorted_by_hop_then_path():
    blast = _blast(
        scope=[
            {"path": "z.py", "direction": "forward", "directions": ["forward"], "hop_count": 2, "propagation_confidence": 0.1, "risk_score": 0.0, "origin": "a.py"},
            {"path": "a.py", "direction": "forward", "directions": ["forward"], "hop_count": 0, "propagation_confidence": 1.0, "risk_score": 0.0, "origin": "a.py"},
            {"path": "b.py", "direction": "forward", "directions": ["forward"], "hop_count": 1, "propagation_confidence": 0.5, "risk_score": 0.0, "origin": "a.py"},
        ],
        origins=["a.py"],
    )
    result = build_blast_impact(_state(blast_graph=blast))
    assert [s["path"] for s in result["scope"]] == ["a.py", "b.py", "z.py"]
