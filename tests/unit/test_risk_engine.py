"""Risk scoring: each signal, the inverted ones, bounds, and explainability."""

import pytest

from backend.models.repository_graph import (
    RepositoryIntelligence,
)
from backend.services.knowledge_graph import build_knowledge_graph
from backend.services.risk_engine import (
    BAND_THRESHOLDS,
    W_BUG_HISTORY,
    W_CHURN,
    assess_file,
    assess_repository,
    explain_risk,
    risk_band,
)
from tests.unit.kg_fixture import (
    build_index,
    full_graph,
    full_index,
    write_repo,
)

AUTH = "pkg/auth.py"
ORPHAN = "pkg/orphan.py"


@pytest.fixture
def graph(tmp_path):
    return full_graph(tmp_path)


@pytest.fixture
def bare_graph(tmp_path):
    """No history, ownership or repairs."""
    write_repo(tmp_path)
    return build_knowledge_graph(build_index(tmp_path))


def signals(assessment) -> dict[str, float]:
    return {e.signal: e.contribution for e in assessment.evidence}


# -- bands -----------------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [(0.0, "low"), (0.19, "low"), (0.2, "moderate"), (0.45, "elevated"), (0.9, "high")],
)
def test_risk_band_thresholds(score, expected):
    assert risk_band(score) == expected


def test_bands_are_ordered_high_to_low():
    thresholds = [t for t, _b in BAND_THRESHOLDS]
    assert thresholds == sorted(thresholds, reverse=True)


# -- individual signals ----------------------------------------------------


def test_bug_history_contributes(graph):
    assert signals(assess_file(graph, AUTH))["bug_history"] > 0


def test_churn_contributes(graph):
    assert signals(assess_file(graph, AUTH))["churn"] > 0


def test_complexity_contributes_and_names_the_worst_function(graph):
    assessment = assess_file(graph, AUTH)
    complexity = next(e for e in assessment.evidence if e.signal == "complexity")
    assert complexity.contribution > 0
    assert "validate" in complexity.detail


def test_trivial_file_has_no_complexity_signal(bare_graph):
    assert "complexity" not in signals(assess_file(bare_graph, "pkg/util.py"))


def test_fan_in_contributes_for_a_called_module(graph):
    assert signals(assess_file(graph, "pkg/util.py"))["fan_in"] > 0


def test_fan_out_contributes_for_a_calling_module(graph):
    assert signals(assess_file(graph, AUTH))["fan_out"] > 0


def test_repair_history_contributes(graph):
    assert signals(assess_file(graph, AUTH))["repair_history"] > 0


def test_mutation_failure_contributes_when_a_mutant_survived(graph):
    """The fixture repair scored 0.5, below a full kill."""
    assert signals(assess_file(graph, AUTH))["mutation_failure"] > 0


def test_full_mutation_kill_produces_no_mutation_signal(tmp_path):
    index = full_index(tmp_path)
    index.repair_memory.records[0].mutation_score = 1.0
    graph = build_knowledge_graph(index)
    assert "mutation_failure" not in signals(assess_file(graph, AUTH))


def test_file_without_repairs_has_no_repair_signals(graph):
    result = signals(assess_file(graph, ORPHAN))
    assert "repair_history" not in result
    assert "mutation_failure" not in result


# -- inverted signals ------------------------------------------------------


def test_low_ownership_is_the_risk_not_high_ownership(graph):
    """Concentrated recent ownership reduces risk; diffuse ownership raises it."""
    owned = signals(assess_file(graph, AUTH))
    unowned = signals(assess_file(graph, ORPHAN))
    assert unowned["low_ownership"] > owned.get("low_ownership", 0.0)


def test_unowned_file_gets_the_full_ownership_penalty(graph):
    evidence = next(e for e in assess_file(graph, ORPHAN).evidence if e.signal == "low_ownership")
    assert evidence.value == 1.0
    assert "no git history" in evidence.detail


def test_absent_documentation_is_the_risk_not_presence(graph):
    documented = signals(assess_file(graph, AUTH))
    undocumented = signals(assess_file(graph, ORPHAN))
    assert "no_documentation" not in documented
    assert undocumented["no_documentation"] > 0


# -- aggregation -----------------------------------------------------------


def test_score_equals_the_sum_of_contributions(graph):
    assessment = assess_file(graph, AUTH)
    assert assessment.risk == pytest.approx(
        min(1.0, sum(e.contribution for e in assessment.evidence)), abs=0.001
    )


def test_score_is_bounded(graph):
    for node in graph.nodes_of_type("file"):
        assert 0.0 <= assess_file(graph, node.file).risk <= 1.0


def test_band_matches_the_score(graph):
    for node in graph.nodes_of_type("file"):
        assessment = assess_file(graph, node.file)
        assert assessment.band == risk_band(assessment.risk)


def test_churned_file_outranks_a_quiet_one(graph):
    assert assess_file(graph, AUTH).risk > assess_file(graph, "pkg/api.py").risk


def test_evidence_is_ordered_by_contribution(graph):
    contributions = [e.contribution for e in assess_file(graph, AUTH).evidence]
    assert contributions == sorted(contributions, reverse=True)


def test_reason_names_the_largest_contributor(graph):
    assessment = assess_file(graph, AUTH)
    assert assessment.reason == assessment.evidence[0].detail


def test_zero_contribution_signals_are_omitted(graph):
    assert all(e.contribution > 0 for e in assess_file(graph, AUTH).evidence)


def test_clean_file_scores_low(bare_graph):
    assert assess_file(bare_graph, "pkg/util.py").risk < 0.2


def test_weights_are_ordered_history_over_structure():
    assert W_BUG_HISTORY > W_CHURN


# -- repository-wide -------------------------------------------------------


def test_assess_repository_ranks_riskiest_first(graph):
    risks = [a.risk for a in assess_repository(graph)]
    assert risks == sorted(risks, reverse=True)


def test_assess_repository_excludes_tests_by_default(graph):
    modules = {a.module for a in assess_repository(graph)}
    assert not any("test" in m for m in modules)


def test_assess_repository_can_include_tests(graph):
    modules = {a.module for a in assess_repository(graph, include_tests=True)}
    assert any("test" in m for m in modules)


def test_assess_repository_respects_the_limit(graph):
    assert len(assess_repository(graph, limit=2)) == 2


def test_assess_repository_is_deterministic(graph):
    first = [(a.module, a.risk) for a in assess_repository(graph)]
    second = [(a.module, a.risk) for a in assess_repository(graph)]
    assert first == second


def test_empty_repository_scores_nothing():
    assert assess_repository(build_knowledge_graph(RepositoryIntelligence())) == []


def test_unknown_file_scores_without_raising(graph):
    assessment = assess_file(graph, "pkg/does_not_exist.py")
    assert 0.0 <= assessment.risk <= 1.0


# -- explainability --------------------------------------------------------


def test_every_assessment_has_an_explanation(graph):
    for assessment in assess_repository(graph):
        assert assessment.explanation.summary
        assert all(e.detail and e.provenance for e in assessment.evidence)


def test_explain_risk_returns_the_mandatory_shape(graph):
    payload = explain_risk(assess_file(graph, AUTH))
    for key in ("module", "risk", "band", "why", "signals", "edges", "evidence"):
        assert key in payload, key
    assert payload["signals"]
    assert all("detail" in e and "contribution" in e for e in payload["evidence"])


def test_explanation_cites_graph_edges(graph):
    """A claim about history must point at the MODIFIED edges behind it."""
    assessment = assess_file(graph, AUTH)
    bug_history = next(e for e in assessment.evidence if e.signal == "bug_history")
    assert bug_history.edges


def test_no_score_is_produced_without_evidence(bare_graph):
    """A black-box score is exactly what this layer must never emit."""
    for node in bare_graph.nodes_of_type("file"):
        assessment = assess_file(bare_graph, node.file)
        if assessment.risk > 0:
            assert assessment.evidence
