"""The query engine: every named query, its ordering, and its empty cases.

Each query is asserted for what it returns, what it excludes, and that it is
deterministic — a query engine whose ordering wobbles cannot be used as evidence.
"""

import pytest

from backend.services.knowledge_graph import KnowledgeQueryEngine
from tests.unit.kg_fixture import full_graph

AUTH = "pkg/auth.py"
UTIL = "pkg/util.py"


@pytest.fixture
def engine(tmp_path):
    return KnowledgeQueryEngine(full_graph(tmp_path))


# -- structure -------------------------------------------------------------


def test_functions_in_file_returns_callables_only(engine):
    names = {n.qualname for n in engine.functions_in_file(AUTH)}
    assert {"validate", "login", "Session.refresh"} <= names
    assert "Session" not in names


def test_functions_in_file_is_in_source_order(engine):
    linenos = [n.attributes["lineno"] for n in engine.functions_in_file(AUTH)]
    assert linenos == sorted(linenos)


def test_functions_in_unknown_file_is_empty(engine):
    assert engine.functions_in_file("pkg/nope.py") == []


def test_classes_in_file(engine):
    assert [n.name for n in engine.classes_in_file(AUTH)] == ["Session"]


def test_api_surface_lists_decorated_endpoints(engine):
    assert [n.qualname for n in engine.api_surface()] == ["login_endpoint"]


# -- behaviour -------------------------------------------------------------


def test_functions_called_by_a_named_function(engine):
    assert "validate" in {n.qualname for n in engine.functions_called_by(AUTH, "login")}


def test_functions_called_by_unknown_function_is_empty(engine):
    assert engine.functions_called_by(AUTH, "nonexistent") == []


def test_functions_called_by_follows_multiple_hops(engine):
    one = {n.qualname for n in engine.functions_called_by(AUTH, "login", hops=1)}
    two = {n.qualname for n in engine.functions_called_by(AUTH, "login", hops=2)}
    assert "helper" not in one
    assert "helper" in two


def test_functions_called_by_whole_file(engine):
    called = {n.qualname for n in engine.functions_called_by(AUTH)}
    assert "helper" in called


def test_functions_called_by_excludes_the_starting_functions(engine):
    result = engine.functions_called_by(AUTH, "login")
    assert all(n.qualname != "login" for n in result)


def test_callers_of_reverses_the_direction(engine):
    assert "login" in {n.qualname for n in engine.callers_of(AUTH, "validate")}


def test_callers_of_a_leaf_includes_its_caller(engine):
    assert "validate" in {n.qualname for n in engine.callers_of(UTIL, "helper")}


def test_call_chain_returns_hop_distances(engine):
    chain = engine.call_chain(AUTH, "login", hops=2)
    assert chain
    distances = [d for _n, d in chain]
    assert distances == sorted(distances)
    assert min(distances) == 1


def test_call_chain_of_unknown_function_is_empty(engine):
    assert engine.call_chain(AUTH, "nonexistent") == []


def test_related_functions_returns_callables_only(engine):
    related = engine.related_functions(AUTH, "validate")
    assert related
    assert all(n.type in ("function", "method") for n in related)


def test_related_functions_excludes_the_start(engine):
    assert all(n.qualname != "validate" for n in engine.related_functions(AUTH, "validate"))


def test_supporting_tests_finds_the_validating_test(engine):
    tests = engine.supporting_tests(AUTH, "validate")
    assert any(n.qualname == "test_validate_rejects_empty" for n in tests)


def test_supporting_tests_falls_back_to_file_level(engine):
    """No VALIDATES edge for this function, but the test module imports the file."""
    tests = engine.supporting_tests(AUTH, "login")
    assert tests
    assert all("test" in n.file for n in tests)


def test_supporting_tests_for_untested_file_is_empty(engine):
    assert engine.supporting_tests("pkg/orphan.py") == []


def test_method_resolves_by_its_bare_name(engine):
    """`refresh` should find `Session.refresh` without the class prefix."""
    assert engine.call_chain(AUTH, "refresh")


# -- people ----------------------------------------------------------------


def test_files_owned_by_returns_weighted_files(engine):
    owned = engine.files_owned_by("Ada")
    assert owned
    assert all(0 < w <= 1.0 for _n, w in owned)


def test_files_owned_by_is_sorted_by_share(engine):
    weights = [w for _n, w in engine.files_owned_by("Ada")]
    assert weights == sorted(weights, reverse=True)


def test_files_owned_by_unknown_author_is_empty(engine):
    assert engine.files_owned_by("Nobody") == []


def test_owners_of_a_file(engine):
    assert "Ada" in {n.name for n, _w in engine.owners_of(AUTH)}


def test_unowned_files_excludes_owned_ones(engine):
    unowned = {n.file for n in engine.unowned_files()}
    assert AUTH not in unowned
    assert "pkg/orphan.py" in unowned


# -- history ---------------------------------------------------------------


def test_commits_touching_a_file(engine):
    shas = {n.name for n in engine.commits_touching(AUTH)}
    assert "aaa111"[:8] in shas


def test_commits_touching_respects_the_limit(engine):
    assert len(engine.commits_touching(AUTH, limit=1)) == 1


def test_commits_touching_unknown_file_is_empty(engine):
    assert engine.commits_touching("pkg/nope.py") == []


def test_co_changed_files_returns_partners_with_ratios(engine):
    partners = engine.co_changed_files(AUTH)
    assert partners
    assert partners[0][0].file == UTIL
    assert 0 < partners[0][1] <= 1.0


def test_co_changed_is_sorted_by_coupling(engine):
    weights = [w for _n, w in engine.co_changed_files(AUTH)]
    assert weights == sorted(weights, reverse=True)


def test_historical_bug_hotspots_ranks_by_churn(engine):
    hotspots = engine.historical_bug_hotspots()
    assert hotspots[0][0].file == AUTH
    assert [w for _n, w in hotspots] == sorted([w for _n, w in hotspots], reverse=True)


def test_historical_bug_hotspots_excludes_files_without_fixes(engine):
    assert "pkg/api.py" not in {n.file for n, _w in engine.historical_bug_hotspots()}


def test_recent_high_churn_requires_both_churn_and_recency(engine):
    modules = {n.file for n, _w in engine.recent_high_churn_modules()}
    assert AUTH in modules
    assert "pkg/api.py" not in modules


def test_recent_high_churn_respects_the_threshold(engine):
    assert engine.recent_high_churn_modules(min_churn=0.99)
    assert not engine.recent_high_churn_modules(min_churn=1.01)


# -- knowledge -------------------------------------------------------------


def test_documentation_for_a_file(engine):
    docs = engine.documentation_for(AUTH)
    assert docs
    assert all(n.type == "document" for n in docs)


def test_documentation_for_a_function(engine):
    assert engine.documentation_for(AUTH, "login")


def test_documentation_for_undocumented_file_is_empty(engine):
    assert engine.documentation_for("pkg/orphan.py") == []


def test_validated_repairs_returns_passing_repairs(engine):
    repairs = engine.validated_repairs()
    assert repairs
    assert all(n.attributes["validation_passed"] for n in repairs)


def test_validated_repairs_can_filter_by_file(engine):
    assert engine.validated_repairs(file=AUTH)
    assert engine.validated_repairs(file=UTIL) == []


def test_repairs_touching_includes_collateral_files(engine):
    """AFFECTS, not just FIXED — the repair changed util.py too."""
    assert engine.repairs_touching(UTIL)
    assert engine.repairs_touching(AUTH)


def test_repairs_touching_unrelated_file_is_empty(engine):
    assert engine.repairs_touching("pkg/orphan.py") == []


# -- determinism and instrumentation --------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda e: e.functions_in_file(AUTH),
        lambda e: e.functions_called_by(AUTH, "login", hops=2),
        lambda e: e.related_functions(AUTH, "validate"),
        lambda e: e.historical_bug_hotspots(),
        lambda e: e.api_surface(),
        lambda e: e.unowned_files(),
    ],
)
def test_queries_are_deterministic(engine, call):
    first = [getattr(n, "id", n) for n in call(engine)]
    second = [getattr(n, "id", n) for n in call(engine)]
    assert first == second


def test_every_query_records_latency(engine):
    engine.functions_in_file(AUTH)
    assert engine.graph.metrics.query_count == 1
    assert engine.graph.metrics.query_total_ms >= 0


def test_latency_is_recorded_even_when_a_query_returns_nothing(engine):
    engine.functions_in_file("pkg/nope.py")
    assert engine.graph.metrics.query_count == 1
