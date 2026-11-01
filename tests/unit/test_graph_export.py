"""Export: view selection, capping, and correctness of all four formats."""

import json
import xml.etree.ElementTree as ElementTree

import pytest

from backend.services.architecture_analyzer import analyze_architecture
from backend.services.capability_layer import attach_capabilities, infer_capabilities
from backend.services.graph_export import (
    EXPORTERS,
    NAMED_VIEWS,
    build_view,
    call_map,
    dependency_map,
    export,
    hotspot_map,
    ownership_map,
    repair_history_map,
    repository_map,
    to_dot,
    to_graphml,
    to_json,
    to_mermaid,
)
from backend.services.repository_graph import node_id
from tests.unit.kg_fixture import full_graph


@pytest.fixture
def graph(tmp_path):
    return full_graph(tmp_path)


# -- view selection --------------------------------------------------------


def test_default_view_includes_everything(graph):
    view = build_view(graph, max_nodes=10_000)
    assert len(view.nodes) == len(graph.nodes)
    assert not view.truncated


def test_node_type_filter(graph):
    view = build_view(graph, node_types=("file",))
    assert all(n.type == "file" for n in view.nodes)


def test_edge_type_filter(graph):
    view = build_view(graph, edge_types=("CALLS",))
    assert all(e.type == "CALLS" for e in view.edges)


def test_edges_are_dropped_when_an_endpoint_is_filtered_out(graph):
    view = build_view(graph, node_types=("file",))
    ids = view.node_ids
    assert all(e.source in ids and e.target in ids for e in view.edges)


def test_focus_restricts_to_a_neighbourhood(graph):
    view = build_view(graph, focus=node_id("function", "pkg/auth.py", "login"), hops=1)
    assert 0 < len(view.nodes) < len(graph.nodes)


def test_file_scope_filter(graph):
    view = build_view(graph, files=("pkg/auth.py",))
    assert all(n.file in ("pkg/auth.py", "") for n in view.nodes)


def test_cap_truncates_by_degree(graph):
    view = build_view(graph, max_nodes=5)
    assert len(view.nodes) == 5
    assert view.truncated
    assert view.total_nodes == len(graph.nodes)


def test_truncation_keeps_the_most_connected_nodes(graph):
    view = build_view(graph, max_nodes=3)
    kept = min(graph.degree(n.id) for n in view.nodes)
    dropped = [n for n in graph.nodes if n not in view.node_ids]
    assert kept >= max(graph.degree(i) for i in dropped)


def test_view_selection_is_deterministic(graph):
    first = [n.id for n in build_view(graph, max_nodes=10).nodes]
    second = [n.id for n in build_view(graph, max_nodes=10).nodes]
    assert first == second


# -- named views -----------------------------------------------------------


def test_repository_map_is_structural(graph):
    view = repository_map(graph)
    assert all(e.type == "CONTAINS" for e in view.edges)


def test_dependency_map_shows_imports(graph):
    view = dependency_map(graph)
    assert any(e.type == "IMPORTS" for e in view.edges)


def test_call_map_shows_calls(graph):
    view = call_map(graph)
    assert any(e.type == "CALLS" for e in view.edges)


def test_ownership_map_links_owners_to_files(graph):
    view = ownership_map(graph)
    assert any(n.type == "owner" for n in view.nodes)
    assert all(e.type == "OWNS" for e in view.edges)


def test_repair_history_map_shows_repairs(graph):
    view = repair_history_map(graph)
    assert any(n.type == "repair" for n in view.nodes)
    assert any(e.type in ("FIXED", "AFFECTS") for e in view.edges)


def test_architecture_map_needs_capabilities_attached(graph):
    attach_capabilities(graph, infer_capabilities(graph))
    view = NAMED_VIEWS["architecture"](graph)
    assert any(n.type == "capability" for n in view.nodes)


def test_hotspot_map_restricts_to_flagged_files(graph):
    hotspots = analyze_architecture(graph)
    view = hotspot_map(graph, hotspots)
    targets = {h.target.split("::", 1)[0] for h in hotspots}
    assert all(n.file in targets for n in view.nodes)


def test_all_named_views_build(graph):
    for name, builder in NAMED_VIEWS.items():
        view = builder(graph)
        assert view.name, name


# -- JSON ------------------------------------------------------------------


def test_json_is_valid_and_node_link_shaped(graph):
    payload = json.loads(to_json(dependency_map(graph)))
    assert payload["name"] == "dependency_map"
    assert {"nodes", "edges", "truncated", "total_nodes"} <= set(payload)
    assert all({"id", "type", "label", "color"} <= set(n) for n in payload["nodes"])


def test_json_edges_carry_provenance_and_evidence(graph):
    payload = json.loads(to_json(dependency_map(graph)))
    assert all("provenance" in e and "evidence" in e for e in payload["edges"])


def test_json_reports_truncation(graph):
    payload = json.loads(to_json(build_view(graph, max_nodes=3)))
    assert payload["truncated"] is True
    assert payload["total_nodes"] > 3


# -- GraphML ---------------------------------------------------------------


def test_graphml_is_well_formed_xml(graph):
    root = ElementTree.fromstring(to_graphml(dependency_map(graph)))
    assert root.tag.endswith("graphml")


def test_graphml_node_and_edge_counts_match_the_view(graph):
    view = dependency_map(graph)
    root = ElementTree.fromstring(to_graphml(view))
    namespace = "{http://graphml.graphdrawing.org/xmlns}"
    assert len(root.findall(f".//{namespace}node")) == len(view.nodes)
    assert len(root.findall(f".//{namespace}edge")) == len(view.edges)


def test_graphml_escapes_untrusted_content(graph):
    """Commit messages and paths reach these strings."""
    from backend.models.knowledge_graph import KGNode

    graph.add_node(KGNode(id="evil", type="file", name='<x a="1">&', file="x.py"))
    rendered = to_graphml(build_view(graph, max_nodes=10_000))
    ElementTree.fromstring(rendered)  # must not raise
    assert "&lt;x" in rendered


# -- DOT -------------------------------------------------------------------


def test_dot_has_a_digraph_header_and_closes(graph):
    rendered = to_dot(dependency_map(graph))
    assert rendered.startswith("digraph")
    assert rendered.rstrip().endswith("}")


def test_dot_contains_one_line_per_edge(graph):
    view = dependency_map(graph)
    rendered = to_dot(view)
    assert rendered.count(" -> ") == len(view.edges)


def test_dot_escapes_quotes(graph):
    from backend.models.knowledge_graph import KGNode

    graph.add_node(KGNode(id='q"uote', type="file", name='has "quotes"', file="q.py"))
    rendered = to_dot(build_view(graph, max_nodes=10_000))
    assert '\\"' in rendered


# -- Mermaid ---------------------------------------------------------------


def test_mermaid_starts_with_a_flowchart_directive(graph):
    assert to_mermaid(dependency_map(graph)).startswith("flowchart LR")


def test_mermaid_caps_harder_than_other_formats(graph):
    rendered = to_mermaid(build_view(graph, max_nodes=10_000), max_nodes=5)
    node_lines = [
        line for line in rendered.splitlines()
        if line.strip().startswith("n") and "-->" not in line
    ]
    assert len(node_lines) == 5


def test_mermaid_reports_truncation_as_a_comment(graph):
    rendered = to_mermaid(build_view(graph, max_nodes=10_000), max_nodes=3)
    assert "%% truncated" in rendered


def test_mermaid_uses_safe_aliases_not_raw_ids(graph):
    rendered = to_mermaid(dependency_map(graph), max_nodes=5)
    assert "file:pkg/auth.py" not in rendered.split("classDef")[0].replace('"', "")


def test_mermaid_assigns_type_classes(graph):
    assert "classDef" in to_mermaid(dependency_map(graph), max_nodes=5)


def test_mermaid_edges_only_reference_included_nodes(graph):
    rendered = to_mermaid(build_view(graph, max_nodes=10_000), max_nodes=4)
    aliases = {f"n{i}" for i in range(4)}
    for line in rendered.splitlines():
        if "-->" in line:
            left, right = line.split("-->")
            assert left.strip() in aliases
            assert right.split("|")[-1].strip() in aliases


# -- dispatch --------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["json", "graphml", "dot", "mermaid"])
def test_export_dispatches_every_format(graph, fmt):
    assert export(dependency_map(graph), fmt)


def test_export_is_case_insensitive(graph):
    assert export(dependency_map(graph), "JSON")


def test_unknown_format_raises_with_the_valid_list(graph):
    with pytest.raises(ValueError, match="unknown export format"):
        export(dependency_map(graph), "svg")


def test_all_exporters_are_registered():
    assert set(EXPORTERS) == {"json", "graphml", "dot", "mermaid"}


def test_empty_view_exports_cleanly(graph):
    view = build_view(graph, node_types=("capability",))
    assert view.nodes == []
    for fmt in EXPORTERS:
        assert export(view, fmt)


def test_export_is_deterministic(graph):
    view = dependency_map(graph)
    assert to_json(view) == to_json(view)
    assert to_dot(view) == to_dot(view)
