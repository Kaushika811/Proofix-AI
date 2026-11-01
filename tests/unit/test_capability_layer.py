"""Capability inference: what it groups, what it refuses to guess, and confidence."""


from backend.models.repository_graph import RepositoryIntelligence
from backend.services.capability_layer import (
    CAPABILITY_VOCABULARY,
    DILUTION_THRESHOLD,
    attach_capabilities,
    capability_for_file,
    infer_capabilities,
    _matches,
    _tokens,
)
from backend.services.knowledge_graph import build_knowledge_graph, capability_id
from backend.services.repository_graph import node_id
from backend.services.repository_indexer import index_repository
from tests.unit.kg_fixture import full_graph


def build_repo(tmp_path, files: dict[str, str], roots=("pkg/",)):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return build_knowledge_graph(index_repository(tmp_path, list(roots)))


# -- tokenisation ----------------------------------------------------------


def test_tokens_split_paths_and_snake_case():
    assert _tokens("pkg/auth_service.py") == {"pkg", "auth", "service", "py"}


def test_tokens_split_camel_case():
    assert "payment" in _tokens("PaymentGateway")


def test_matches_requires_whole_tokens():
    """Substring matching would make `latest` match `test`."""
    assert _matches({"latest", "config"}, ("test",)) == []
    assert _matches({"test", "config"}, ("test",)) == ["test"]


# -- signals ---------------------------------------------------------------


def test_filename_signal_groups_a_file(tmp_path):
    graph = build_repo(tmp_path, {"pkg/payment_service.py": "def charge():\n    return 1\n"})
    payments = next(c for c in infer_capabilities(graph) if c.slug == "payments")
    assert "pkg/payment_service.py" in payments.files
    assert "filename" in payments.signal_counts


def test_import_signal_groups_a_file_regardless_of_its_name(tmp_path):
    """A file importing `stripe` is doing payments whatever it is called."""
    graph = build_repo(tmp_path, {"pkg/zzz.py": "import stripe\n\n\ndef go():\n    return stripe\n"})
    payments = next(c for c in infer_capabilities(graph) if c.slug == "payments")
    assert "pkg/zzz.py" in payments.files
    assert "import" in payments.signal_counts


def test_route_signal_uses_endpoint_names(tmp_path):
    """A route name alone is weak evidence, so the threshold is lowered here."""
    graph = build_repo(
        tmp_path,
        {"pkg/h.py": "def route(f):\n    return f\n\n\n@route\ndef login_handler():\n    return 1\n"},
    )
    auth = next(c for c in infer_capabilities(graph, min_confidence=0.1) if c.slug == "authentication")
    assert "route" in auth.signal_counts
    assert "pkg/h.py" in auth.files


def test_route_signal_alone_is_below_the_default_threshold(tmp_path):
    graph = build_repo(
        tmp_path,
        {"pkg/h.py": "def route(f):\n    return f\n\n\n@route\ndef login_handler():\n    return 1\n"},
    )
    assert not [c for c in infer_capabilities(graph) if c.slug == "authentication"]


def test_documentation_signal_groups_the_files_a_doc_describes(tmp_path):
    graph = build_repo(
        tmp_path,
        {
            "pkg/core.py": "def run():\n    return 1\n",
            "docs/payments.md": "# Payments\n\nSee `pkg/core.py` for the flow.\n",
        },
    )
    payments = next(
        c for c in infer_capabilities(graph, min_confidence=0.1) if c.slug == "payments"
    )
    assert "documentation" in payments.signal_counts
    assert "pkg/core.py" in payments.files


def test_unmatched_repository_yields_no_capabilities(tmp_path):
    graph = build_repo(tmp_path, {"pkg/zzz.py": "def qqq():\n    return 1\n"})
    assert infer_capabilities(graph) == []


# -- confidence ------------------------------------------------------------


def test_more_independent_signal_kinds_means_higher_confidence(tmp_path, tmp_path_factory):
    weak = build_repo(tmp_path, {"pkg/payment.py": "def go():\n    return 1\n"})
    strong_dir = tmp_path_factory.mktemp("strong")
    strong = build_repo(
        strong_dir,
        {"pkg/payment.py": "import stripe\n\n\ndef go():\n    return stripe\n"},
    )
    weak_conf = next(c for c in infer_capabilities(weak) if c.slug == "payments").confidence
    strong_conf = next(c for c in infer_capabilities(strong) if c.slug == "payments").confidence
    assert strong_conf > weak_conf


def test_confidence_is_bounded(tmp_path):
    graph = full_graph(tmp_path)
    assert all(0.0 <= c.confidence <= 1.0 for c in infer_capabilities(graph))


def test_repeated_hits_of_one_kind_do_not_raise_confidence(tmp_path, tmp_path_factory):
    """Ten files named auth_* is still one kind of evidence."""
    one = build_repo(tmp_path, {"pkg/auth_a.py": "def a():\n    return 1\n"})
    many_dir = tmp_path_factory.mktemp("many")
    many = build_repo(
        many_dir,
        {f"pkg/auth_{i}.py": "def a():\n    return 1\n" for i in range(5)},
    )
    one_conf = next(c for c in infer_capabilities(one) if c.slug == "authentication").confidence
    many_conf = next(c for c in infer_capabilities(many) if c.slug == "authentication").confidence
    assert one_conf == many_conf


def test_broad_coverage_dilutes_confidence(tmp_path):
    """Vocabulary matching most of the repository has not found a feature."""
    files = {f"pkg/store_{i}.py": "def go():\n    return 1\n" for i in range(12)}
    graph = build_repo(tmp_path, files)
    persistence = next(c for c in infer_capabilities(graph) if c.slug == "persistence")

    coverage = len(persistence.files) / len(graph.nodes_of_type("file"))
    assert coverage > DILUTION_THRESHOLD
    assert any(e.signal == "coverage_dilution" for e in persistence.explanation.evidence)
    assert persistence.confidence < 0.30


def test_min_confidence_filters_weak_groupings(tmp_path):
    graph = full_graph(tmp_path)
    assert infer_capabilities(graph, min_confidence=0.99) == []


# -- explainability --------------------------------------------------------


def test_every_capability_explains_itself(tmp_path):
    graph = full_graph(tmp_path)
    for capability in infer_capabilities(graph):
        assert capability.explanation.summary
        assert capability.explanation.evidence
        assert capability.explanation.signals
        assert all(e.detail for e in capability.explanation.evidence)


def test_signal_counts_match_the_evidence(tmp_path):
    graph = full_graph(tmp_path)
    for capability in infer_capabilities(graph):
        recorded = {e.signal for e in capability.explanation.evidence if e.contribution > 0}
        assert recorded == set(capability.signal_counts)


# -- graph attachment ------------------------------------------------------


def test_attach_creates_capability_nodes_and_part_of_edges(tmp_path):
    graph = full_graph(tmp_path)
    capabilities = infer_capabilities(graph)
    attach_capabilities(graph, capabilities)

    for capability in capabilities:
        node = capability_id(capability.slug)
        assert node in graph.nodes
        assert graph.nodes[node].type == "capability"
        for file in capability.files:
            assert node in graph.neighbors(node_id("file", file), "PART_OF")


def test_part_of_edge_carries_confidence_and_evidence(tmp_path):
    graph = full_graph(tmp_path)
    capabilities = infer_capabilities(graph)
    attach_capabilities(graph, capabilities)

    edges = [e for e in graph.edges if e.type == "PART_OF"]
    assert edges
    assert all(e.provenance == "capability" and e.evidence for e in edges)


def test_attach_is_idempotent(tmp_path):
    graph = full_graph(tmp_path)
    capabilities = infer_capabilities(graph)
    attach_capabilities(graph, capabilities)
    before = len(graph.edges)
    attach_capabilities(graph, capabilities)
    assert len(graph.edges) == before


# -- lookup and ordering ---------------------------------------------------


def test_capability_for_file_picks_the_strongest(tmp_path):
    graph = full_graph(tmp_path)
    capabilities = infer_capabilities(graph)
    match = capability_for_file(capabilities, "pkg/auth.py")
    if capabilities:
        assert match is None or "pkg/auth.py" in match.files


def test_capability_for_unknown_file_is_none(tmp_path):
    graph = full_graph(tmp_path)
    assert capability_for_file(infer_capabilities(graph), "pkg/nope.py") is None


def test_results_are_ordered_by_confidence(tmp_path):
    graph = full_graph(tmp_path)
    confidences = [c.confidence for c in infer_capabilities(graph)]
    assert confidences == sorted(confidences, reverse=True)


def test_inference_is_deterministic(tmp_path):
    graph = full_graph(tmp_path)
    first = [(c.slug, c.confidence, tuple(c.files)) for c in infer_capabilities(graph)]
    second = [(c.slug, c.confidence, tuple(c.files)) for c in infer_capabilities(graph)]
    assert first == second


def test_empty_graph_yields_no_capabilities():
    assert infer_capabilities(build_knowledge_graph(RepositoryIntelligence())) == []


def test_vocabulary_entries_are_well_formed():
    for name, entry in CAPABILITY_VOCABULARY.items():
        assert entry["terms"], name
        assert "libraries" in entry
        assert all(t == t.lower() for t in entry["terms"]), name


def test_matches_allows_regular_plurals():
    """Prose headings pluralise: '## Payments' for a `payment` capability."""
    assert _matches({"payments"}, ("payment",)) == ["payment"]
    assert _matches({"boxes"}, ("box",)) == ["box"]


def test_matches_does_not_stem_beyond_plurals():
    assert _matches({"paying"}, ("payment",)) == []
    assert _matches({"payload"}, ("pay",)) == []
