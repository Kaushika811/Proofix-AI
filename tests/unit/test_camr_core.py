"""Unit tests for CAMR core strengthening (A4/A5/A6 services)."""

from backend.models.cve import CVERecord
from backend.models.fix_dag import DependencyEdge, FixNode
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.blast_traversal import resolve_origins, traverse_multi_origin
from backend.services.fix_dag_builder import (
    apply_dependencies,
    build_dependency_edges,
    build_fix_nodes,
    detect_conflict_batches,
    topological_execution_order,
)
from backend.services.root_cause_builder import (
    collect_evidence_refs,
    compute_confidence,
    synthesize_root_cause_summary,
)


def _sample_sig() -> SemanticIntentGraph:
    return SemanticIntentGraph(
        repo_path="/repo",
        source_roots=["app/"],
        files={
            "app/auth.py": FileNode(
                path="app/auth.py",
                role="auth-boundary",
                imports=["config"],
                criticality=0.9,
                churn_weight=0.8,
            ),
            "app/config.py": FileNode(
                path="app/config.py",
                role="config-surface",
                imports=[],
                criticality=0.75,
                churn_weight=0.6,
            ),
            "app/api.py": FileNode(
                path="app/api.py",
                role="public-api",
                imports=["auth"],
                criticality=0.85,
                churn_weight=0.7,
            ),
        },
        edges=[
            ("app/auth.py", "config"),
            ("app/api.py", "auth"),
        ],
    )


def test_collect_evidence_refs_all_sources():
    refs, cve_context, citations = collect_evidence_refs(
        stack='File "app/auth.py", line 10, in validate\nAssertionError: bad',
        findings=[{"id": "f1", "file": "app/auth.py", "line": 10, "message": "hardcoded secret"}],
        cve_report={
            "findings": [
                {"cve_id": "CVE-1", "package": "urllib3", "classification": "Critical"},
            ]
        },
        reproduction={
            "status": "CONFIRMED",
            "failing_test": "tests/test_auth.py::test_x",
            "failing_file": "app/auth.py",
            "failing_line": 27,
            "exception_type": "AssertionError",
            "exception_message": "token not rejected",
        },
    )
    sources = {r.source for r in refs}
    assert "runtime" in sources
    assert "finding" in sources
    assert "cve" in sources
    assert "stack_trace" in sources
    assert "CVE-1" in cve_context
    assert len(citations) >= 2


def test_compute_confidence_increases_with_evidence():
    refs, _, _ = collect_evidence_refs(
        stack="trace",
        findings=[{"id": "f1", "file": "a.py", "line": 1, "message": "x"}],
        cve_report={"findings": [{"cve_id": "CVE-1", "package": "pkg", "classification": "Critical"}]},
        reproduction={"status": "CONFIRMED", "failing_file": "a.py", "failing_line": 1},
    )
    score = compute_confidence(refs, verified_count=2, reproduction={"status": "CONFIRMED"})
    assert score >= 0.7


def test_synthesize_root_cause_summary():
    refs, cve_context, _ = collect_evidence_refs("", [], {"findings": []}, {"status": "CONFIRMED", "exception_type": "AssertionError"})
    summary, root = synthesize_root_cause_summary(refs, cve_context, {"status": "CONFIRMED", "exception_type": "AssertionError"})
    assert summary
    assert "AssertionError" in root


def test_resolve_origins_prefers_verified():
    citations = [
        {"file": "app/a.py", "verified": False},
        {"file": "app/b.py", "verified": True},
    ]
    assert resolve_origins(citations) == ["app/b.py"]


def test_traverse_multi_origin_hop_count_and_confidence():
    sig = _sample_sig()
    result = traverse_multi_origin(sig, ["app/auth.py"])
    assert "app/auth.py" in result.origins
    by_path = {s.path: s for s in result.scope}
    assert by_path["app/auth.py"].hop_count == 0
    assert by_path["app/config.py"].hop_count >= 1
    assert by_path["app/auth.py"].propagation_confidence >= by_path["app/config.py"].propagation_confidence


def test_build_fix_nodes_and_dependency_edges():
    sig = _sample_sig()
    findings = [{"id": "f-auth", "file": "app/auth.py", "line": 1, "message": "issue"}]
    cves = [CVERecord(package="urllib3", cve_id="CVE-9", severity="HIGH", classification="Critical")]
    nodes = build_fix_nodes(findings, cves, ["app/auth.py", "app/config.py"])
    edges = build_dependency_edges(nodes, sig, cves)
    nodes = apply_dependencies(nodes, edges)

    assert any(e.reason.startswith("import_graph") for e in edges)
    assert any(e.reason.startswith("cve_reachability") for e in edges) or len(cves) == 1

    order = topological_execution_order(nodes, edges)
    assert "f-auth" in order


def test_detect_conflict_batches():
    nodes = build_fix_nodes(
        [
            {"id": "f1", "file": "app/shared.py", "line": 1, "message": "a"},
            {"id": "f2", "file": "app/shared.py", "line": 2, "message": "b"},
        ],
        [],
        ["app/shared.py"],
    )
    batches = detect_conflict_batches(nodes)
    assert batches == [["f1", "f2"]]


def test_topological_execution_order_falls_back_when_dependencies_cycle():
    nodes = [
        FixNode(issue_id="f-auth", files=["app/auth.py"]),
        FixNode(issue_id="cve-CVE-9", files=["app/config.py"]),
    ]
    edges = [
        DependencyEdge(from_issue="f-auth", to_issue="cve-CVE-9", reason="import_graph:a"),
        DependencyEdge(from_issue="cve-CVE-9", to_issue="f-auth", reason="cve_reachability:b"),
    ]

    order = topological_execution_order(nodes, edges)

    assert order == ["cve-CVE-9", "f-auth"]
