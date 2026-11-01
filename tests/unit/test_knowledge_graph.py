"""The knowledge graph: adapters, edge typing, traversal, metrics, consistency.

The tests that matter most assert the *cross-structure* edges — DESCRIBES,
FIXED, VALIDATES, OWNS, MODIFIED — because those are what the RKG adds. Anything
a single source structure could already answer is covered by that structure's
own test file.
"""

import pytest

from backend.models.knowledge_graph import KGNode, KnowledgeGraphMetrics
from backend.services.knowledge_graph import (
    ADAPTERS,
    KnowledgeQueryEngine,
    RepositoryKnowledgeGraph,
    build_knowledge_graph,
    callable_node_id,
    capability_id,
    commit_id,
    document_id,
    owner_id,
    repair_id,
)
from backend.services.repository_graph import node_id
from tests.unit.kg_fixture import build_index, full_graph, full_index, write_repo

AUTH_FILE = node_id("file", "pkg/auth.py")
UTIL_FILE = node_id("file", "pkg/util.py")
VALIDATE = node_id("function", "pkg/auth.py", "validate")
LOGIN = node_id("function", "pkg/auth.py", "login")
HELPER = node_id("function", "pkg/util.py", "helper")
SESSION = node_id("class", "pkg/auth.py", "Session")
REFRESH = node_id("method", "pkg/auth.py", "Session.refresh")


@pytest.fixture
def graph(tmp_path):
    return full_graph(tmp_path)


@pytest.fixture
def bare_graph(tmp_path):
    """No history, ownership or repairs — the structural graph alone."""
    write_repo(tmp_path)
    return build_knowledge_graph(build_index(tmp_path))


# -- identifiers -----------------------------------------------------------


def test_node_id_helpers_are_prefixed():
    assert owner_id("Ada") == "owner:Ada"
    assert commit_id("abc") == "commit:abc"
    assert repair_id("r1") == "repair:r1"
    assert capability_id("auth") == "capability:auth"
    assert document_id("README.md", 3) == "document:README.md#3"


def test_callable_node_id_matches_repository_graph_ids():
    """The whole adapter design depends on these two schemes agreeing."""
    assert callable_node_id("pkg/auth.py", "validate", False) == VALIDATE
    assert callable_node_id("pkg/auth.py", "Session.refresh", True) == REFRESH


# -- structure adapter -----------------------------------------------------


def test_files_functions_and_classes_become_nodes(bare_graph):
    for node in (AUTH_FILE, VALIDATE, LOGIN, SESSION, REFRESH):
        assert node in bare_graph.nodes, node


def test_node_types_are_assigned(bare_graph):
    assert bare_graph.nodes[AUTH_FILE].type == "file"
    assert bare_graph.nodes[VALIDATE].type == "function"
    assert bare_graph.nodes[REFRESH].type == "method"
    assert bare_graph.nodes[SESSION].type == "class"


def test_test_file_gets_the_test_type(bare_graph):
    assert bare_graph.nodes[node_id("file", "tests/test_auth.py")].type == "test"


def test_config_file_becomes_a_config_node(bare_graph):
    assert bare_graph.nodes[node_id("configuration", "pyproject.toml")].type == "config"


def test_module_variables_are_not_nodes(bare_graph):
    """Constants add noise without adding reachability."""
    assert node_id("variable", "pkg/auth.py", "MAX_AGE") not in bare_graph.nodes


def test_line_span_is_carried_into_attributes(bare_graph):
    node = bare_graph.nodes[VALIDATE]
    assert node.attributes["lineno"] > 0
    assert node.attributes["end_lineno"] >= node.attributes["lineno"]


def test_contains_and_defines_edges(bare_graph):
    assert VALIDATE in bare_graph.neighbors(AUTH_FILE, "DEFINES")
    assert REFRESH in bare_graph.neighbors(SESSION, "CONTAINS")


def test_imports_edge_between_files(bare_graph):
    assert UTIL_FILE in bare_graph.neighbors(AUTH_FILE, "IMPORTS")


def test_tests_edge_from_test_module(bare_graph):
    assert AUTH_FILE in bare_graph.neighbors(node_id("file", "tests/test_auth.py"), "TESTS")


# -- API adapter -----------------------------------------------------------


def test_decorated_callable_becomes_an_api_node(bare_graph):
    api = "api:pkg/api.py::login_endpoint"
    assert api in bare_graph.nodes
    assert bare_graph.nodes[api].type == "api"


def test_api_exposes_the_underlying_function(bare_graph):
    api = "api:pkg/api.py::login_endpoint"
    target = node_id("function", "pkg/api.py", "login_endpoint")
    assert target in bare_graph.neighbors(api, "EXPOSES")


def test_undecorated_function_has_no_api_node(bare_graph):
    assert "api:pkg/auth.py::validate" not in bare_graph.nodes


# -- call adapter ----------------------------------------------------------


def test_calls_edge_within_a_module(bare_graph):
    assert VALIDATE in bare_graph.neighbors(LOGIN, "CALLS")


def test_calls_edge_across_modules(bare_graph):
    assert HELPER in bare_graph.neighbors(VALIDATE, "CALLS")


def test_validates_edge_from_a_test(bare_graph):
    """The cross-structure edge that answers "what proves this works?"."""
    test_fn = node_id("function", "tests/test_auth.py", "test_validate_rejects_empty")
    assert VALIDATE in bare_graph.neighbors(test_fn, "VALIDATES")


def test_production_call_does_not_produce_validates(bare_graph):
    assert VALIDATE not in bare_graph.neighbors(LOGIN, "VALIDATES")


def test_call_edge_carries_provenance_and_evidence(bare_graph):
    edge = next(e for e in bare_graph.out_edges(LOGIN, "CALLS") if e.target == VALIDATE)
    assert edge.provenance == "call_graph"
    assert edge.evidence


# -- ownership and history adapters ---------------------------------------


def test_owner_nodes_are_created(graph):
    assert owner_id("Ada") in graph.nodes
    assert graph.nodes[owner_id("Ada")].type == "owner"


def test_owns_edge_is_weighted_by_share(graph):
    edge = next(e for e in graph.out_edges(owner_id("Ada"), "OWNS") if e.target == AUTH_FILE)
    assert edge.weight == 1.0
    assert "primary author" in edge.evidence


def test_commit_nodes_and_modified_edges(graph):
    assert commit_id("aaa111") in graph.nodes
    assert AUTH_FILE in graph.neighbors(commit_id("aaa111"), "MODIFIED")


def test_fix_commit_is_flagged_on_the_node(graph):
    assert graph.nodes[commit_id("aaa111")].attributes["is_fix"] is True
    assert graph.nodes[commit_id("ccc333")].attributes["is_fix"] is False


def test_authored_edge_links_owner_to_commit(graph):
    assert commit_id("aaa111") in graph.neighbors(owner_id("Ada"), "AUTHORED")


def test_co_changed_edge_carries_the_ratio(graph):
    edge = next(e for e in graph.out_edges(AUTH_FILE, "CO_CHANGED") if e.target == UTIL_FILE)
    assert 0 < edge.weight <= 1.0
    assert "changed together" in edge.evidence


# -- documentation and repair adapters ------------------------------------


def test_document_node_and_describes_edge_to_a_file(graph):
    describing = [e.source for e in graph.in_edges(AUTH_FILE, "DESCRIBES")]
    assert describing
    assert all(graph.nodes[s].type == "document" for s in describing)


def test_describes_edge_reaches_a_named_function(graph):
    assert [e for e in graph.in_edges(LOGIN, "DESCRIBES")]


def test_repair_node_and_fixed_edge(graph):
    node = repair_id("run-1:pkg/auth.py:validate")
    assert node in graph.nodes
    assert VALIDATE in graph.neighbors(node, "FIXED")


def test_repair_affects_collateral_files(graph):
    node = repair_id("run-1:pkg/auth.py:validate")
    assert UTIL_FILE in graph.neighbors(node, "AFFECTS")


def test_repair_edge_weight_reflects_validation(graph):
    edge = graph.out_edges(repair_id("run-1:pkg/auth.py:validate"), "FIXED")[0]
    assert edge.weight == 1.0
    assert "validated" in edge.evidence


# -- graph mechanics -------------------------------------------------------


def test_add_edge_ignores_unknown_endpoints(bare_graph):
    before = len(bare_graph.edges)
    bare_graph.add_edge("nope", VALIDATE, "CALLS")
    bare_graph.add_edge(VALIDATE, "nope", "CALLS")
    assert len(bare_graph.edges) == before


def test_add_edge_deduplicates(bare_graph):
    before = len(bare_graph.edges)
    bare_graph.add_edge(LOGIN, VALIDATE, "CALLS")
    assert len(bare_graph.edges) == before


def test_same_pair_with_different_types_are_distinct_edges(bare_graph):
    before = len(bare_graph.edges)
    bare_graph.add_edge(LOGIN, VALIDATE, "REFERENCES")
    assert len(bare_graph.edges) == before + 1


def test_in_and_out_edges_are_consistent(graph):
    for edge in graph.edges:
        assert edge in graph.out_edges(edge.source)
        assert edge in graph.in_edges(edge.target)


def test_every_edge_endpoint_exists_as_a_node(graph):
    """Graph consistency: no dangling references."""
    for edge in graph.edges:
        assert edge.source in graph.nodes, edge.source
        assert edge.target in graph.nodes, edge.target


def test_every_edge_declares_provenance(graph):
    assert all(e.provenance for e in graph.edges)


def test_degree_counts_both_directions(bare_graph):
    expected = len(bare_graph.out_edges(VALIDATE)) + len(bare_graph.in_edges(VALIDATE))
    assert bare_graph.degree(VALIDATE) == expected


def test_nodes_of_type_and_nodes_in_file(bare_graph):
    assert all(n.type == "function" for n in bare_graph.nodes_of_type("function"))
    assert all(n.file == "pkg/auth.py" for n in bare_graph.nodes_in_file("pkg/auth.py"))


def test_node_lookup_of_unknown_id_is_none(bare_graph):
    assert bare_graph.node("absent") is None


# -- traversal -------------------------------------------------------------


def test_traverse_includes_start_at_distance_zero(bare_graph):
    assert bare_graph.traverse(LOGIN, ("CALLS",), 1)[LOGIN] == 0


def test_traverse_respects_hop_limit(bare_graph):
    one_hop = bare_graph.traverse(LOGIN, ("CALLS",), 1)
    two_hops = bare_graph.traverse(LOGIN, ("CALLS",), 2)
    assert VALIDATE in one_hop
    assert HELPER not in one_hop
    assert HELPER in two_hops
    assert two_hops[HELPER] == 2


def test_traverse_reverse_direction(bare_graph):
    assert LOGIN in bare_graph.traverse(VALIDATE, ("CALLS",), 1, "in")


def test_traverse_both_directions(bare_graph):
    reached = bare_graph.traverse(VALIDATE, ("CALLS",), 1, "both")
    assert LOGIN in reached and HELPER in reached


def test_traverse_from_unknown_node_is_empty(bare_graph):
    assert bare_graph.traverse("absent", ("CALLS",), 2) == {}


def test_traverse_terminates_on_a_cycle():
    """A call cycle must not hang the traversal."""
    from backend.models.repository_graph import RepositoryIntelligence

    kg = RepositoryKnowledgeGraph(RepositoryIntelligence())
    for name in ("a", "b", "c"):
        kg.add_node(KGNode(id=name, type="function", name=name))
    kg.add_edge("a", "b", "CALLS")
    kg.add_edge("b", "c", "CALLS")
    kg.add_edge("c", "a", "CALLS")

    assert set(kg.traverse("a", ("CALLS",), 10)) == {"a", "b", "c"}


def test_traverse_with_no_edge_type_filter_follows_everything(bare_graph):
    assert len(bare_graph.traverse(AUTH_FILE, None, 1)) > 1


# -- metrics ---------------------------------------------------------------


def test_metrics_count_nodes_and_edges(graph):
    assert graph.metrics.node_count == len(graph.nodes)
    assert graph.metrics.edge_count == len(graph.edges)


def test_average_degree_is_two_e_over_v(graph):
    expected = round(2 * len(graph.edges) / len(graph.nodes), 4)
    assert graph.metrics.average_degree == expected


def test_metrics_break_down_by_type(graph):
    assert graph.metrics.nodes_by_type["function"] > 0
    assert graph.metrics.edges_by_type["CALLS"] > 0
    assert sum(graph.metrics.nodes_by_type.values()) == graph.metrics.node_count
    assert sum(graph.metrics.edges_by_type.values()) == graph.metrics.edge_count


def test_repository_coverage_is_reported(graph):
    assert 0 < graph.metrics.repository_coverage <= 1.0
    assert graph.metrics.files_total > 0


def test_memory_and_build_time_are_reported(graph):
    assert graph.metrics.memory_bytes > 0
    assert graph.metrics.build_ms >= 0


def test_query_latency_accumulates(graph):
    engine = KnowledgeQueryEngine(graph)
    engine.functions_in_file("pkg/auth.py")
    engine.functions_in_file("pkg/util.py")
    assert graph.metrics.query_count == 2
    assert graph.metrics.average_query_ms >= 0


def test_cache_hit_rate_defaults_to_zero():
    assert KnowledgeGraphMetrics().cache_hit_rate == 0.0


# -- determinism and degradation ------------------------------------------


def test_build_is_deterministic(tmp_path):
    index = full_index(tmp_path)
    first = build_knowledge_graph(index)
    second = build_knowledge_graph(index)
    assert sorted(first.nodes) == sorted(second.nodes)
    assert [(e.source, e.target, e.type) for e in first.edges] == [
        (e.source, e.target, e.type) for e in second.edges
    ]


def test_empty_intelligence_produces_an_empty_graph():
    from backend.models.repository_graph import RepositoryIntelligence

    graph = build_knowledge_graph(RepositoryIntelligence())
    assert graph.nodes == {}
    assert graph.edges == []
    assert graph.metrics.repository_coverage == 0.0


def test_every_adapter_is_registered():
    assert len(ADAPTERS) == 7


def test_graph_holds_a_reference_not_a_copy(graph):
    """The RKG must not duplicate the structures it unifies."""
    assert graph.intelligence.repository_graph is not None
    assert graph.intelligence.parsed_modules


def test_missing_substructure_degrades_without_raising(tmp_path):
    index = full_index(tmp_path)
    index.call_graph.nodes.clear()
    index.call_graph.call_sites.clear()
    graph = build_knowledge_graph(index)
    assert graph.nodes
    assert not [e for e in graph.edges if e.type == "CALLS"]
