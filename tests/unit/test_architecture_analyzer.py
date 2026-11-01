"""Architectural detectors: what each fires on, and what it refuses to flag."""

import pytest

from backend.models.repository_graph import RepositoryIntelligence
from backend.services.architecture_analyzer import (
    CENTRAL_UTILITY_FAN_IN,
    DETECTORS,
    GOD_OBJECT_CLASS_METHODS,
    GOD_OBJECT_SYMBOLS,
    analyze_architecture,
    explain_hotspot,
    find_circular_dependencies,
    find_dead_modules,
    find_god_objects,
    find_high_risk_apis,
    find_orphan_files,
    find_over_centralized_utilities,
    find_unowned_modules,
    find_unused_code,
    _canonical_cycle,
)
from backend.services.knowledge_graph import build_knowledge_graph
from backend.services.repository_indexer import index_repository
from tests.unit.kg_fixture import build_index, full_graph, write_repo


def build_repo(tmp_path, files: dict[str, str]):
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return build_knowledge_graph(index_repository(tmp_path, ["pkg/"]))


@pytest.fixture
def graph(tmp_path):
    return full_graph(tmp_path)


# -- god objects -----------------------------------------------------------


def test_god_object_detected_by_symbol_count(tmp_path):
    body = "\n\n".join(f"def fn_{i}():\n    return {i}" for i in range(GOD_OBJECT_SYMBOLS + 5))
    graph = build_repo(tmp_path, {"pkg/big.py": body + "\n"})
    assert any(h.target == "pkg/big.py" for h in find_god_objects(graph))


def test_god_object_detected_by_class_breadth(tmp_path):
    methods = "\n".join(f"    def m_{i}(self):\n        return {i}" for i in range(GOD_OBJECT_CLASS_METHODS + 2))
    graph = build_repo(tmp_path, {"pkg/c.py": f"class Big:\n{methods}\n"})
    assert any("Big" in h.target for h in find_god_objects(graph))


def test_small_module_is_not_a_god_object(graph):
    assert not any(h.target == "pkg/util.py" for h in find_god_objects(graph))


def test_god_object_lists_its_members(tmp_path):
    body = "\n\n".join(f"def fn_{i}():\n    return {i}" for i in range(GOD_OBJECT_SYMBOLS + 5))
    graph = build_repo(tmp_path, {"pkg/big.py": body + "\n"})
    hotspot = next(h for h in find_god_objects(graph) if h.target == "pkg/big.py")
    assert hotspot.members
    assert hotspot.severity > 0


def test_test_files_are_not_god_objects(tmp_path):
    body = "\n\n".join(f"def test_{i}():\n    assert {i} == {i}" for i in range(GOD_OBJECT_SYMBOLS + 5))
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_big.py").write_text(body + "\n")
    graph = build_knowledge_graph(index_repository(tmp_path, ["pkg/"]))
    assert not any("test_big" in h.target for h in find_god_objects(graph))


# -- circular dependencies -------------------------------------------------


def test_import_cycle_is_detected(tmp_path):
    graph = build_repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\n\n\ndef fa():\n    return 1\n",
            "pkg/b.py": "from pkg import a\n\n\ndef fb():\n    return 1\n",
        },
    )
    cycles = find_circular_dependencies(graph)
    assert cycles
    assert set(cycles[0].members) == {"pkg/a.py", "pkg/b.py"}


def test_acyclic_repository_reports_no_cycles(graph):
    assert find_circular_dependencies(graph) == []


def test_cycle_summary_shows_the_loop(tmp_path):
    graph = build_repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\n\n\ndef fa():\n    return 1\n",
            "pkg/b.py": "from pkg import a\n\n\ndef fb():\n    return 1\n",
        },
    )
    summary = find_circular_dependencies(graph)[0].summary
    assert summary.startswith("pkg/a.py")
    assert summary.endswith("pkg/a.py")


def test_canonical_cycle_is_rotation_invariant():
    assert _canonical_cycle(["b", "c", "a"]) == _canonical_cycle(["a", "b", "c"])
    assert _canonical_cycle([]) == ()


def test_cycle_is_reported_once_not_per_member(tmp_path):
    graph = build_repo(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\n\n\ndef fa():\n    return 1\n",
            "pkg/b.py": "from pkg import c\n\n\ndef fb():\n    return 1\n",
            "pkg/c.py": "from pkg import a\n\n\ndef fc():\n    return 1\n",
        },
    )
    assert len(find_circular_dependencies(graph)) == 1


# -- centralised utilities -------------------------------------------------


def test_widely_imported_simple_module_is_flagged(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/util.py": "def helper():\n    return 1\n"}
    for i in range(CENTRAL_UTILITY_FAN_IN + 2):
        files[f"pkg/mod_{i}.py"] = "from pkg.util import helper\n\n\ndef go():\n    return helper()\n"
    graph = build_repo(tmp_path, files)
    assert any(h.target == "pkg/util.py" for h in find_over_centralized_utilities(graph))


def test_rarely_imported_module_is_not_flagged(graph):
    assert find_over_centralized_utilities(graph) == []


def test_complex_central_module_is_left_to_the_god_object_detector(tmp_path):
    complex_body = "def helper(v):\n" + "".join(
        f"    if v == {i}:\n        return {i}\n" for i in range(12)
    ) + "    return 0\n"
    files = {"pkg/__init__.py": "", "pkg/util.py": complex_body}
    for i in range(CENTRAL_UTILITY_FAN_IN + 2):
        files[f"pkg/mod_{i}.py"] = "from pkg.util import helper\n\n\ndef go():\n    return helper()\n"
    graph = build_repo(tmp_path, files)
    assert not any(h.target == "pkg/util.py" for h in find_over_centralized_utilities(graph))


# -- dead modules and orphans ---------------------------------------------


def test_unimported_module_is_a_dead_candidate(graph):
    assert any(h.target == "pkg/orphan.py" for h in find_dead_modules(graph))


def test_imported_module_is_not_dead(graph):
    assert not any(h.target == "pkg/util.py" for h in find_dead_modules(graph))


def test_package_initialiser_is_never_dead(graph):
    assert not any(h.target == "pkg/__init__.py" for h in find_dead_modules(graph))


def test_module_exposing_an_api_is_not_dead(graph):
    """Reachable by route, not by import."""
    assert not any(h.target == "pkg/api.py" for h in find_dead_modules(graph))


def test_documented_dead_module_scores_lower(tmp_path):
    graph = build_repo(
        tmp_path,
        {
            "pkg/lonely.py": "def go():\n    return 1\n",
            "pkg/other.py": "def x():\n    return 1\n",
            "README.md": "# Doc\n\nSee `pkg/lonely.py`.\n",
        },
    )
    hotspots = {h.target: h.severity for h in find_dead_modules(graph)}
    assert hotspots["pkg/lonely.py"] < hotspots["pkg/other.py"]


def test_dead_module_explanation_warns_about_dynamic_imports(graph):
    hotspot = next(h for h in find_dead_modules(graph) if h.target == "pkg/orphan.py")
    assert "dynamic" in hotspot.explanation.summary.lower()


def test_orphan_file_has_no_non_structural_edges(tmp_path):
    graph = build_repo(tmp_path, {"pkg/alone.py": "def solo():\n    return 1\n"})
    assert any(h.target == "pkg/alone.py" for h in find_orphan_files(graph))


def test_connected_file_is_not_an_orphan(graph):
    assert not any(h.target == "pkg/auth.py" for h in find_orphan_files(graph))


# -- ownership -------------------------------------------------------------


def test_unowned_module_is_flagged(graph):
    assert any(h.target == "pkg/orphan.py" for h in find_unowned_modules(graph))


def test_owned_module_is_not_flagged(graph):
    assert not any(h.target == "pkg/auth.py" for h in find_unowned_modules(graph))


def test_unowned_with_history_scores_higher_than_without(tmp_path):
    from tests.unit.kg_fixture import with_history

    write_repo(tmp_path)
    index = with_history(build_index(tmp_path))
    index.ownership.files.clear()  # history exists, ownership does not
    graph = build_knowledge_graph(index)

    severities = {h.target: h.severity for h in find_unowned_modules(graph)}
    assert severities["pkg/auth.py"] > severities["pkg/orphan.py"]


# -- APIs and unused code --------------------------------------------------


def test_high_risk_api_requires_a_risky_module(graph):
    assert find_high_risk_apis(graph, min_risk=0.99) == []


def test_high_risk_api_is_found_at_a_low_threshold(graph):
    hotspots = find_high_risk_apis(graph, min_risk=0.0)
    assert any("login_endpoint" in h.target for h in hotspots)


def test_high_risk_api_carries_the_risk_evidence(graph):
    hotspot = find_high_risk_apis(graph, min_risk=0.0)[0]
    assert hotspot.explanation.evidence


def test_uncalled_function_is_flagged_as_unused(graph):
    assert any("unreferenced" in h.target for h in find_unused_code(graph))


def test_called_function_is_not_unused(graph):
    assert not any("::helper" in h.target for h in find_unused_code(graph))


def test_tested_function_is_not_unused(graph):
    assert not any(h.target.endswith("::validate") for h in find_unused_code(graph))


def test_decorated_function_is_never_unused(graph):
    """Decorators register callables where the AST cannot see."""
    assert not any("login_endpoint" in h.target for h in find_unused_code(graph))


def test_dunder_methods_are_never_unused(tmp_path):
    graph = build_repo(tmp_path, {"pkg/c.py": "class C:\n    def __init__(self):\n        self.x = 1\n"})
    assert not any("__init__" in h.target for h in find_unused_code(graph))


def test_private_unused_function_scores_lower_than_public(tmp_path):
    graph = build_repo(
        tmp_path,
        {"pkg/m.py": "def _hidden():\n    return 1\n\n\ndef public():\n    return 2\n"},
    )
    severities = {h.target: h.severity for h in find_unused_code(graph)}
    assert severities["pkg/m.py::_hidden"] < severities["pkg/m.py::public"]


# -- aggregation and explainability ---------------------------------------


def test_analyze_runs_every_detector(graph):
    kinds = {h.kind for h in analyze_architecture(graph)}
    assert {"dead_module", "unowned_module", "unused_code"} <= kinds


def test_limit_per_kind_is_respected(graph):
    from collections import Counter

    counts = Counter(h.kind for h in analyze_architecture(graph, limit_per_kind=1))
    assert all(c <= 1 for c in counts.values())


def test_results_are_ordered_by_severity(graph):
    severities = [h.severity for h in analyze_architecture(graph)]
    assert severities == sorted(severities, reverse=True)


def test_analysis_is_deterministic(graph):
    first = [(h.kind, h.target, h.severity) for h in analyze_architecture(graph)]
    second = [(h.kind, h.target, h.severity) for h in analyze_architecture(graph)]
    assert first == second


def test_every_hotspot_explains_itself(graph):
    for hotspot in analyze_architecture(graph):
        assert hotspot.summary
        assert hotspot.explanation.evidence
        assert all(e.detail for e in hotspot.explanation.evidence)


def test_explain_hotspot_returns_the_mandatory_shape(graph):
    payload = explain_hotspot(analyze_architecture(graph)[0])
    for key in ("kind", "target", "severity", "why", "signals", "edges", "evidence"):
        assert key in payload, key


def test_empty_repository_yields_no_hotspots():
    assert analyze_architecture(build_knowledge_graph(RepositoryIntelligence())) == []


def test_all_detectors_are_registered():
    assert len(DETECTORS) == 8
