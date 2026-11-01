"""Mining, outcomes, reviews, organisation, scoring, graph, privacy, performance."""

import time
from datetime import datetime, timedelta

import pytest

from backend.models.learning import (
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    OrganizationProfile,
    RepairKnowledge,
    RepositoryProfile,
    StyleProfile,
)
from backend.learning.knowledge_index import (
    build_index,
    explain,
    prompt_context,
    render_directives,
)
from backend.learning.learning_engine import LearningEngine
from backend.learning.metrics import (
    framework_coverage,
    learning_growth,
    pattern_frequency,
    repair_evolution,
    template_effectiveness,
)
from backend.learning.organization_memory import (
    AGREEMENT_THRESHOLD,
    MIN_REPOSITORIES,
    UBIQUITOUS,
    OrganizationMemory,
)
from backend.learning.outcome_learning import (
    InvalidTransition,
    OutcomeLearner,
    is_valid_transition,
)
from backend.learning.pattern_mining import (
    CATEGORY_APPROACHES,
    MIN_SUPPORT,
    mine_patterns,
    mine_templates,
    select_template,
    template_directives,
)
from backend.learning.review_learning import (
    ReviewLearner,
    categorize_reason,
    classify_decision,
)


def record(**overrides) -> RepairKnowledge:
    base = dict(
        repair_id="r1",
        repository_id="repo-a",
        bug_category="sql-injection",
        root_cause_category="missing-validation",
        issue_signature="sig-a",
        validation_passed=True,
        framework="FastAPI",
    )
    base.update(overrides)
    return RepairKnowledge(**base)


def records(count: int, **overrides) -> list[RepairKnowledge]:
    return [record(repair_id=f"r{i}", **overrides) for i in range(count)]


# ===================================================== pattern mining


def test_template_requires_minimum_support():
    assert mine_templates(records(1)) == []
    assert mine_templates(records(MIN_SUPPORT)) != []


def test_template_carries_approach_and_guardrails():
    template = mine_templates(records(3))[0]
    assert template.approach
    assert template.guardrails
    assert template.validation_hints


def test_template_is_not_a_patch():
    """A template carries the approach, never the code."""
    template = mine_templates(records(3))[0]
    assert not hasattr(template, "patch")
    assert "def " not in template.approach


def test_unknown_category_is_not_templated():
    assert mine_templates(records(5, bug_category="unknown")) == []


def test_template_counts_successes_and_failures():
    pool = records(2, outcome="merged") + [
        record(repair_id="x1", outcome="rejected"),
        record(repair_id="x2", outcome="rejected"),
    ]
    template = mine_templates(pool)[0]
    assert template.successes == 2
    assert template.failures == 2
    assert template.success_rate == 0.5


def test_template_confidence_is_damped_by_support():
    small = mine_templates(records(2, outcome="merged"))[0]
    large = mine_templates(records(10, outcome="merged"))[0]
    assert large.confidence > small.confidence
    assert small.success_rate == large.success_rate == 1.0


def test_failing_template_is_kept_not_deleted():
    """'This has failed 4 of 5 times' is more useful than no template."""
    templates = mine_templates(records(5, outcome="rejected"))
    assert templates
    assert templates[0].success_rate == 0.0


def test_template_tracks_frameworks_and_repositories():
    pool = records(2) + records(2, framework="Django", repository_id="repo-b")
    for i, r in enumerate(pool):
        r.repair_id = f"u{i}"
    template = mine_templates(pool)[0]
    assert set(template.frameworks) == {"FastAPI", "Django"}
    assert set(template.repositories) == {"repo-a", "repo-b"}


def test_reviewer_concerns_become_guardrails():
    pool = records(3)
    for r in pool:
        r.review_categories = ["testing"]
    template = mine_templates(pool)[0]
    assert any("test coverage" in g for g in template.guardrails)


def test_different_root_causes_yield_different_templates():
    pool = records(2) + records(2, root_cause_category="boundary-condition")
    for i, r in enumerate(pool):
        r.repair_id = f"u{i}"
    assert len(mine_templates(pool)) == 2


def test_mining_is_deterministic():
    pool = records(4)
    first = [(t.template_id, t.support) for t in mine_templates(pool)]
    second = [(t.template_id, t.support) for t in mine_templates(pool)]
    assert first == second


def test_every_known_category_has_an_approach():
    for category, (approach, guardrails, validation) in CATEGORY_APPROACHES.items():
        assert approach and guardrails and validation, category


def test_unknown_category_gets_the_default_approach():
    template = mine_templates(records(3, bug_category="exception:Weird"))[0]
    assert "minimum change" in template.approach


# -- patterns --------------------------------------------------------------


def test_pattern_requires_recurrence():
    assert mine_patterns(records(1)) == []
    assert mine_patterns(records(2)) != []


def test_pattern_counts_occurrences():
    assert mine_patterns(records(4))[0].occurrences == 4


def test_pattern_detects_recurrence_in_one_repository():
    """The same defect twice in one repository means the first repair did not hold."""
    assert mine_patterns(records(3, repository_id="repo-a"))[0].recurred == 2


def test_pattern_across_repositories_is_not_recurrence():
    pool = [record(repair_id=f"r{i}", repository_id=f"repo-{i}") for i in range(3)]
    assert mine_patterns(pool)[0].recurred == 0


def test_pattern_recurrence_rate():
    pattern = mine_patterns(records(4, repository_id="repo-a"))[0]
    assert 0 < pattern.recurrence_rate <= 1.0


def test_patterns_are_sorted_by_frequency():
    pool = records(4) + [record(repair_id=f"b{i}", issue_signature="sig-b") for i in range(2)]
    assert [p.occurrences for p in mine_patterns(pool)] == [4, 2]


# -- selection -------------------------------------------------------------


def test_select_template_matches_category():
    templates = mine_templates(records(3))
    assert select_template(templates, "sql-injection") is not None
    assert select_template(templates, "xss") is None


def test_select_prefers_the_framework_match():
    pool = records(3) + [
        record(repair_id=f"d{i}", framework="Django", root_cause_category="boundary-condition")
        for i in range(3)
    ]
    templates = mine_templates(pool)
    chosen = select_template(templates, "sql-injection", framework="Django")
    assert "Django" in chosen.frameworks


def test_select_is_deterministic():
    templates = mine_templates(records(4))
    assert select_template(templates, "sql-injection").template_id == select_template(
        templates, "sql-injection"
    ).template_id


def test_template_directives_state_the_track_record():
    template = mine_templates(records(3, outcome="merged"))[0]
    assert any("succeeded in" in d for d in template_directives(template))


def test_template_directives_admit_no_decisions_yet():
    template = mine_templates(records(3, outcome="suggested", validation_passed=False))[0]
    assert any("none yet decided" in d for d in template_directives(template))


# ===================================================== outcome learning


def test_outcome_is_appended_not_replaced():
    learner = OutcomeLearner()
    learner.record("r1", "accepted")
    learner.record("r1", "merged")
    assert len(learner.timeline("r1")) == 2
    assert learner.current_status("r1") == "merged"


def test_rollback_does_not_erase_acceptance():
    """A merged-then-reverted repair is a distinct, important failure mode."""
    learner = OutcomeLearner()
    for status in ("accepted", "merged", "reverted"):
        learner.record("r1", status)
    assert learner.was_ever("r1", "merged")
    assert learner.current_status("r1") == "reverted"


def test_default_status_is_suggested():
    assert OutcomeLearner().current_status("unknown") == "suggested"


@pytest.mark.parametrize(
    "current,nxt,valid",
    [
        ("suggested", "accepted", True),
        ("accepted", "merged", True),
        ("merged", "reverted", True),
        ("suggested", "merged", False),
        ("rejected", "merged", False),
    ],
)
def test_transition_validity(current, nxt, valid):
    assert is_valid_transition(current, nxt) is valid


def test_strict_mode_refuses_an_impossible_sequence():
    learner = OutcomeLearner()
    with pytest.raises(InvalidTransition):
        learner.record("r1", "merged", strict=True)


def test_lenient_mode_records_out_of_order_signals():
    """An external webhook may arrive out of order; losing it is worse."""
    learner = OutcomeLearner()
    learner.record("r1", "merged")
    assert learner.current_status("r1") == "merged"


def test_statistics_count_current_status_not_transitions():
    learner = OutcomeLearner()
    for status in ("accepted", "merged", "reverted"):
        learner.record("r1", status)
    stats = learner.statistics(["r1"])
    assert stats.total == 1
    assert stats.negative == 1


def test_success_rate_ignores_pending():
    learner = OutcomeLearner()
    learner.record("r1", "merged")
    learner.record("r2", "rejected")
    learner.record("r3", "suggested")
    stats = learner.statistics(["r1", "r2", "r3"])
    assert stats.success_rate == 0.5
    assert stats.pending == 1


def test_confidence_is_damped_by_sample():
    small, large = OutcomeLearner(), OutcomeLearner()
    small.record("a", "merged")
    for i in range(10):
        large.record(f"b{i}", "merged")
    assert large.statistics().confidence > small.statistics().confidence


def test_rollback_rate():
    learner = OutcomeLearner()
    for i in range(4):
        learner.record(f"r{i}", "merged")
    learner.record("r0", "reverted")
    assert learner.rollback_rate() == 0.25


def test_rollback_rate_without_merges_is_zero():
    assert OutcomeLearner().rollback_rate() == 0.0


def test_statistics_by_attribute():
    learner = OutcomeLearner()
    pool = records(2) + records(2, bug_category="xss")
    for i, r in enumerate(pool):
        r.repair_id = f"u{i}"
        learner.record(r.repair_id, "merged")
    grouped = learner.statistics_by(pool, "bug_category")
    assert set(grouped) == {"sql-injection", "xss"}


def test_historical_success_reports_its_reasoning():
    learner = OutcomeLearner()
    pool = records(4)
    for r in pool:
        learner.record(r.repair_id, "merged")
    confidence, explanation = learner.historical_success(pool, "sql-injection")
    assert confidence > 0
    assert "succeeded" in explanation


def test_historical_success_without_history():
    confidence, explanation = OutcomeLearner().historical_success([], "xss")
    assert confidence == 0.0
    assert "no prior" in explanation


def test_historical_success_falls_back_from_framework_to_category():
    """A framework rate from two samples is worse than a category rate from twenty."""
    learner = OutcomeLearner()
    pool = records(20)
    for r in pool:
        learner.record(r.repair_id, "merged")
    confidence, explanation = learner.historical_success(pool, "sql-injection", "Django")
    assert confidence > 0
    assert "Django" not in explanation


def test_outcome_constants_are_disjoint():
    assert not (POSITIVE_OUTCOMES & NEGATIVE_OUTCOMES)


# ===================================================== review learning


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("needs a test for this", "testing"),
        ("this is a security problem", "security"),
        ("wrong layer, belongs in services", "architecture"),
        ("this is slow, n+1 query", "performance"),
        ("the logic is incorrect here", "logic"),
        ("misleading name", "naming"),
        ("please add a docstring", "documentation"),
        ("do not add a new dependency", "dependencies"),
        ("fix the formatting", "formatting"),
    ],
)
def test_reason_categories(reason, expected):
    assert expected in categorize_reason(reason)


def test_security_outranks_testing():
    """The security concern is substantive; the test is how it is shown."""
    assert categorize_reason("needs a security test")[0] == "security"


def test_multiple_categories_are_all_returned():
    categories = categorize_reason("no tests and the naming is confusing")
    assert "testing" in categories and "naming" in categories


def test_unrecognised_reason_yields_nothing():
    assert categorize_reason("hmm") == []


def test_empty_reason_yields_nothing():
    assert categorize_reason("") == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("LGTM", "accepted_immediately"),
        ("just a nit", "minor_edits"),
        ("significant rewrite needed", "major_edits"),
        ("requesting changes", "changes_requested"),
        ("rejecting this", "rejected"),
    ],
)
def test_decision_classification(text, expected):
    assert classify_decision(text) == expected


def test_unknown_verdict_uses_the_default():
    assert classify_decision("hmm") == "pending"


def test_review_records_categories():
    learner = ReviewLearner()
    review = learner.record("r1", "minor_edits", "needs a test")
    assert review.categories == ["testing"]


def test_review_with_unrecognised_reason_is_unknown():
    assert ReviewLearner().record("r1", "minor_edits", "hmm").categories == ["unknown"]


def test_review_without_a_reason_has_no_categories():
    assert ReviewLearner().record("r1", "accepted_immediately").categories == []


def test_review_from_free_text():
    review = ReviewLearner().record_from_text("r1", "LGTM but please add a test")
    assert review.decision == "accepted_immediately"
    assert "testing" in review.categories


def test_reason_is_length_bounded():
    """A reviewer comment can contain a pasted diff."""
    review = ReviewLearner().record("r1", "rejected", "x" * 1000)
    assert len(review.reason_summary) <= 200
    assert "\n" not in review.reason_summary


def test_acceptance_rate():
    learner = ReviewLearner()
    learner.record("r1", "accepted_immediately")
    learner.record("r2", "minor_edits")
    learner.record("r3", "rejected")
    assert learner.statistics().acceptance_rate == pytest.approx(2 / 3, abs=0.01)


def test_review_confidence_is_damped():
    small, large = ReviewLearner(), ReviewLearner()
    small.record("r1", "accepted_immediately")
    for i in range(10):
        large.record(f"r{i}", "accepted_immediately")
    assert large.review_confidence()[0] > small.review_confidence()[0]


def test_review_confidence_without_reviews():
    confidence, explanation = ReviewLearner().review_confidence()
    assert confidence == 0.0
    assert "no reviews" in explanation


def test_top_concerns_drive_guardrails():
    learner = ReviewLearner()
    for i in range(3):
        learner.record(f"r{i}", "minor_edits", "needs a test")
    assert any("testing" in g for g in learner.guardrails())


def test_unknown_rate_tracks_vocabulary_gaps():
    learner = ReviewLearner()
    learner.record("r1", "minor_edits", "needs a test")
    learner.record("r2", "minor_edits", "hmm")
    assert learner.unknown_rate() == 0.5


def test_latest_review_wins():
    learner = ReviewLearner()
    learner.record("r1", "rejected")
    learner.record("r1", "accepted_immediately")
    assert learner.latest_for("r1").decision == "accepted_immediately"


# ===================================================== organization memory


def profile(repository_id: str, **style) -> RepositoryProfile:
    return RepositoryProfile(
        repository_id=repository_id,
        style=StyleProfile(repository_id=repository_id, **style),
    )


def test_convention_requires_multiple_repositories():
    memory = OrganizationMemory()
    memory.register(profile("a", function_naming="snake_case"))
    assert memory.build().naming_conventions.get("function") is None


def test_convention_is_asserted_when_repositories_agree():
    memory = OrganizationMemory()
    for name in ("a", "b", "c"):
        memory.register(profile(name, function_naming="snake_case"))
    assert memory.build().naming_conventions["function"] == "snake_case"


def test_disagreement_yields_no_convention():
    memory = OrganizationMemory()
    memory.register(profile("a", function_naming="snake_case"))
    memory.register(profile("b", function_naming="camelCase"))
    assert "function" not in memory.build().naming_conventions


def test_unknown_and_mixed_are_excluded_from_voting():
    memory = OrganizationMemory()
    memory.register(profile("a", function_naming="snake_case"))
    memory.register(profile("b", function_naming="snake_case"))
    memory.register(profile("c", function_naming="unknown"))
    assert memory.build().naming_conventions["function"] == "snake_case"


def test_thresholds_are_declared():
    assert MIN_REPOSITORIES >= 2
    assert 0.5 < AGREEMENT_THRESHOLD <= 1.0


def test_libraries_exclude_the_standard_library():
    memory = OrganizationMemory()
    libraries = memory.learn_libraries({"a": {"os", "sys", "fastapi"}, "b": {"json", "fastapi"}})
    assert "fastapi" in libraries
    assert "os" not in libraries


def test_libraries_are_counted_by_repository():
    memory = OrganizationMemory()
    assert memory.learn_libraries({"a": {"fastapi"}, "b": {"fastapi"}})["fastapi"] == 2


def test_ubiquitous_set_covers_common_modules():
    assert {"os", "sys", "json", "typing"} <= UBIQUITOUS


def test_architecture_is_inferred_from_directories():
    memory = OrganizationMemory()
    for name in ("a", "b"):
        memory.register(profile(name))
    paths = {
        "a": ("services/x.py", "repositories/y.py", "models/z.py"),
        "b": ("services/p.py", "repositories/q.py", "models/r.py"),
    }
    assert memory.build(file_paths=paths).architecture_style in ("layered", "mvc")


def test_one_shared_directory_is_not_an_architecture():
    memory = OrganizationMemory()
    memory.register(profile("a"))
    assert memory.build(file_paths={"a": ("services/x.py",)}).architecture_style == "unknown"


def test_folder_conventions_are_counted():
    memory = OrganizationMemory()
    memory.register(profile("a"))
    memory.register(profile("b"))
    folders = memory.build(
        file_paths={"a": ("backend/x.py",), "b": ("backend/y.py",)}
    ).folder_conventions
    assert folders["backend"] == 2


def test_maturity_rises_with_evidence():
    thin = OrganizationProfile(repositories=["a"], total_repairs=1)
    thick = OrganizationProfile(
        repositories=[f"r{i}" for i in range(6)],
        total_repairs=60,
        total_reviews=30,
        preferred_libraries={f"lib{i}": 2 for i in range(12)},
    )
    assert thick.maturity > thin.maturity
    assert 0 <= thick.maturity <= 1.0


def test_organization_directives():
    profile_ = OrganizationProfile(
        error_handling_style="custom_hierarchy",
        logging_style="logging_module",
        preferred_libraries={"fastapi": 3},
    )
    directives = " ".join(profile_.prompt_directives())
    assert "custom_hierarchy" in directives
    assert "fastapi" in directives


def test_empty_organization_has_no_directives():
    assert OrganizationProfile().prompt_directives() == []


# ===================================================== knowledge index


def index_with(templates=None, patterns=None, style=None, org=None):
    return build_index(
        repository_profile=RepositoryProfile(
            repository_id="repo-a", style=style or StyleProfile(function_naming="snake_case")
        ),
        organization_profile=org or OrganizationProfile(),
        templates=templates or [],
        patterns=patterns or [],
        recent_repairs=[],
    )


def test_context_includes_style_directives():
    context = prompt_context(index_with())
    assert any("snake_case" in d for d in context["directives"])


def test_context_labels_every_source():
    context = prompt_context(index_with())
    assert len(context["sources"]) == len(context["directives"])
    assert all(context["sources"])


def test_context_includes_a_matching_template():
    context = prompt_context(index_with(templates=mine_templates(records(3))), "sql-injection")
    assert context["template_id"] is not None


def test_context_omits_a_non_matching_template():
    context = prompt_context(index_with(templates=mine_templates(records(3))), "xss")
    assert context["template_id"] is None


def test_context_is_capped():
    style = StyleProfile(
        function_naming="snake_case", class_naming="PascalCase", quote_style="double",
        type_hint_coverage=0.9, docstring_coverage=0.9, docstring_style="google",
        logging_style="logging_module", exception_style="custom_hierarchy", indent=2,
    )
    context = prompt_context(index_with(style=style), "sql-injection", max_directives=3)
    assert len(context["directives"]) == 3
    assert context["truncated"]


def test_render_returns_empty_for_an_empty_index():
    """A heading with nothing under it reads as an instruction the model failed."""
    assert render_directives({"directives": []}) == ""


def test_render_states_that_evidence_wins():
    block = render_directives(prompt_context(index_with()))
    assert "the failure wins" in block


def test_explain_pairs_directives_with_sources():
    explanations = explain(prompt_context(index_with()))
    assert explanations
    assert all(":" in e for e in explanations)


def test_templates_for_category_is_ranked():
    index = index_with(templates=mine_templates(records(4)))
    assert index.templates_for("sql-injection")


# ===================================================== engine and scoring


def test_learn_from_run_records_knowledge():
    engine = LearningEngine()
    knowledge = engine.learn_from_run(
        repair_id="r1",
        repository_id="repo-a",
        reproduction={"exception_type": "KeyError"},
        root_cause={"root_cause": "missing validation"},
        patches=[{"file": "a.py", "original": "x\n", "patched": "y\n"}],
        mutation_result={"pytest_passed": True},
    )
    assert knowledge.bug_category == "missing-key"
    assert engine.state.repairs.get("r1") is not None


def test_learn_from_run_seeds_the_outcome():
    engine = LearningEngine()
    engine.learn_from_run(repair_id="r1", patches=[{"file": "a.py"}])
    assert engine.state.outcomes.current_status("r1") == "suggested"


def test_record_outcome_mirrors_onto_the_record():
    engine = LearningEngine()
    engine.learn_from_run(repair_id="r1", repository_id="repo-a", patches=[{"file": "a.py"}])
    engine.record_outcome("r1", "merged")
    assert engine.state.repairs.get("r1").outcome == "merged"


def test_record_review_mirrors_categories():
    engine = LearningEngine()
    engine.learn_from_run(repair_id="r1", repository_id="repo-a", patches=[{"file": "a.py"}])
    engine.record_review("r1", "minor_edits", "needs a test")
    assert engine.state.repairs.get("r1").review_categories == ["testing"]


def test_score_excludes_unmeasured_components():
    """A repair awaiting review is not a repair that was reviewed badly."""
    engine = LearningEngine()
    knowledge = record(mutation_score=None, reviewer_decision="pending")
    score = engine.score_repair(knowledge)
    assert "review_confidence" not in score.measured
    assert "review_confidence" in score.unmeasured()
    assert score.overall == pytest.approx(
        sum(score.measured_components().values()) / len(score.measured), abs=0.001
    )


def test_score_includes_review_once_decided():
    engine = LearningEngine()
    score = engine.score_repair(record(reviewer_decision="accepted_immediately"))
    assert "review_confidence" in score.measured
    assert score.review_confidence == 1.0


def test_score_penalises_failed_validation():
    engine = LearningEngine()
    passed = engine.score_repair(record(validation_passed=True))
    failed = engine.score_repair(record(validation_passed=False))
    assert passed.validation_quality > failed.validation_quality


def test_score_penalises_retries():
    engine = LearningEngine()
    clean = engine.score_repair(record(retry_count=0))
    retried = engine.score_repair(record(retry_count=2))
    assert clean.validation_quality > retried.validation_quality


def test_score_penalises_security_rejection():
    engine = LearningEngine()
    clean = engine.score_repair(record(security_rejected=False))
    rejected = engine.score_repair(record(security_rejected=True))
    assert clean.repair_confidence > rejected.repair_confidence


def test_score_always_explains_itself():
    score = LearningEngine().score_repair(record())
    assert score.reasons
    assert all(score.reasons)


def test_score_is_bounded():
    score = LearningEngine().score_repair(record())
    assert 0.0 <= score.overall <= 1.0
    assert all(0.0 <= v <= 1.0 for v in score.components().values())


def test_score_is_deterministic():
    engine = LearningEngine()
    knowledge = record()
    assert engine.score_repair(knowledge).overall == engine.score_repair(knowledge).overall


def test_knowledge_index_is_built_for_an_unknown_repository():
    index = LearningEngine().knowledge_index("never-seen")
    assert index.repository_id == "never-seen"
    assert index.templates == []


# ===================================================== metrics


def test_learning_growth_reports_recent_activity():
    growth = learning_growth(records(5))
    assert growth["total_repairs"] == 5
    assert growth["growth_rate"] == 1.0
    assert not growth["plateaued"]


def test_learning_growth_detects_a_plateau():
    old = records(30)
    for r in old:
        r.recorded_at = datetime.utcnow() - timedelta(days=200)
    assert learning_growth(old)["plateaued"]


def test_learning_growth_on_empty_history():
    assert learning_growth([])["total_repairs"] == 0


def test_repair_evolution_uses_equal_count_buckets():
    """Time buckets would produce empty periods that read as quality collapses."""
    evolution = repair_evolution(records(10), buckets=5)
    assert len(evolution) == 5
    assert all(b["repairs"] > 0 for b in evolution)


def test_repair_evolution_on_empty_history():
    assert repair_evolution([]) == []


def test_repair_evolution_reports_undecided_as_none():
    assert repair_evolution(records(4), buckets=2)[0]["success_rate"] is None


def test_pattern_frequency_is_ranked():
    frequencies = pattern_frequency(mine_patterns(records(4)))
    assert frequencies[0]["occurrences"] == 4


def test_template_effectiveness_reports_the_track_record():
    effectiveness = template_effectiveness(mine_templates(records(3, outcome="merged")))
    assert effectiveness[0]["success_rate"] == 1.0
    assert effectiveness[0]["support"] == 3


def test_framework_coverage():
    from backend.models.learning import FrameworkProfile

    profiles = {
        "a": RepositoryProfile(
            repository_id="a",
            framework=FrameworkProfile(primary_framework="FastAPI", confidence=0.8),
        ),
        "b": RepositoryProfile(repository_id="b"),
    }
    coverage = framework_coverage(profiles)
    assert coverage["covered_repositories"] == 1
    assert coverage["unknown_repositories"] == 1
    assert coverage["coverage_rate"] == 0.5


def test_framework_coverage_on_empty_input():
    assert framework_coverage({})["coverage_rate"] == 0.0


def test_dashboard_exposes_every_required_metric():
    engine = LearningEngine()
    for i in range(3):
        engine.learn_from_run(
            repair_id=f"r{i}", repository_id="repo-a",
            reproduction={"exception_type": "KeyError"},
            patches=[{"file": "a.py", "original": "x\n", "patched": "y\n"}],
            mutation_result={"pytest_passed": True},
        )
    dashboard = engine.dashboard()
    for key in (
        "learning_growth", "successful_repairs", "rejected_repairs", "template_reuse",
        "framework_coverage", "repository_maturity", "organization_maturity",
        "style_confidence", "pattern_frequency", "repair_evolution", "reviews",
        "outcomes", "performance",
    ):
        assert key in dashboard, key


# ===================================================== privacy


def test_no_learning_model_can_hold_source():
    """The structural guarantee across every learning model."""
    from backend.models import learning as learning_models

    forbidden = {"source", "patch", "diff", "prompt", "content", "body", "code", "original", "patched"}
    for name in dir(learning_models):
        model = getattr(learning_models, name)
        if isinstance(model, type) and hasattr(model, "model_fields"):
            assert not (set(model.model_fields) & forbidden), name


def test_engine_never_stores_patch_content():
    engine = LearningEngine()
    engine.learn_from_run(
        repair_id="r1",
        repository_id="repo-a",
        patches=[{"file": "a.py", "original": "SECRET_TOKEN = 1\n", "patched": "SECRET_TOKEN = 2\n"}],
    )
    assert "SECRET_TOKEN" not in engine.state.repairs.get("r1").model_dump_json()


def test_dashboard_contains_no_source():
    engine = LearningEngine()
    engine.learn_from_run(
        repair_id="r1", repository_id="repo-a",
        patches=[{"file": "a.py", "original": "UNIQUE_MARKER\n", "patched": "UNIQUE_MARKER2\n"}],
    )
    assert "UNIQUE_MARKER" not in str(engine.dashboard())


def test_no_learning_module_imports_an_llm():
    """Determinism by construction, not by discipline."""
    import backend.learning as package
    import pkgutil
    import importlib

    for module_info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"backend.learning.{module_info.name}")
        source = open(module.__file__).read()
        for banned in ("import anthropic", "import openai", "from anthropic", "from openai",
                       "LLMService", "LLMGateway", "embedding", "SentenceTransformer"):
            assert banned not in source, f"{module_info.name} references {banned}"


# ===================================================== performance


def test_learning_update_is_well_under_budget():
    """Target is <100 ms per repair."""
    engine = LearningEngine()
    for i in range(50):
        engine.learn_from_run(
            repair_id=f"warm{i}", repository_id="repo-a",
            reproduction={"exception_type": "KeyError"},
            patches=[{"file": "a.py", "original": "x\n", "patched": "y\n"}],
        )

    started = time.perf_counter()
    engine.learn_from_run(
        repair_id="measured", repository_id="repo-a",
        reproduction={"exception_type": "KeyError"},
        patches=[{"file": "a.py", "original": "x\n", "patched": "y\n"}],
    )
    assert (time.perf_counter() - started) * 1000 < 100


def test_engine_reports_its_own_update_cost():
    engine = LearningEngine()
    engine.learn_from_run(repair_id="r1", patches=[{"file": "a.py"}])
    assert engine.metrics().learning_updates == 1


def test_mining_scales(benchmark_records=200):
    pool = [
        record(repair_id=f"r{i}", bug_category=f"cat-{i % 10}", issue_signature=f"sig-{i % 10}")
        for i in range(benchmark_records)
    ]
    started = time.perf_counter()
    mine_templates(pool)
    mine_patterns(pool)
    assert (time.perf_counter() - started) * 1000 < 200


# ===================================================== graph integration


def test_learning_nodes_attach_to_the_graph(tmp_path):
    from backend.learning.graph_adapter import attach_learning
    from backend.models.learning import FrameworkProfile
    from tests.unit.kg_fixture import full_graph

    graph = full_graph(tmp_path)
    before = len(graph.nodes)

    attach_learning(
        graph,
        repository_profile=RepositoryProfile(
            repository_id="repo-a",
            style=StyleProfile(function_naming="snake_case", files_analyzed=10),
            framework=FrameworkProfile(primary_framework="FastAPI", confidence=0.8),
        ),
        organization_profile=OrganizationProfile(repositories=["repo-a"]),
        repairs=records(2),
        reviews=[ReviewLearner().record("r0", "minor_edits", "needs a test")],
        templates=mine_templates(records(3)),
        patterns=mine_patterns(records(2)),
    )

    assert len(graph.nodes) > before
    types = {n.type for n in graph.nodes.values()}
    assert {"framework", "style", "organization", "template", "pattern", "outcome", "review"} <= types


def test_learning_edges_are_typed(tmp_path):
    from backend.learning.graph_adapter import attach_learning
    from backend.models.learning import FrameworkProfile
    from tests.unit.kg_fixture import full_graph

    graph = full_graph(tmp_path)
    attach_learning(
        graph,
        repository_profile=RepositoryProfile(
            repository_id="repo-a",
            style=StyleProfile(files_analyzed=5),
            framework=FrameworkProfile(primary_framework="FastAPI", confidence=0.8),
        ),
        organization_profile=OrganizationProfile(),
        repairs=records(2),
        patterns=mine_patterns(records(2)),
    )
    edge_types = {e.type for e in graph.edges}
    assert {"USES_FRAMEWORK", "FOLLOWS_STYLE", "BELONGS_TO", "RESULTED_IN", "INSTANCE_OF"} <= edge_types


def test_attaching_learning_is_idempotent(tmp_path):
    from backend.learning.graph_adapter import attach_learning
    from tests.unit.kg_fixture import full_graph

    graph = full_graph(tmp_path)
    attach_learning(graph, repairs=records(2))
    count = len(graph.edges)
    attach_learning(graph, repairs=records(2))
    assert len(graph.edges) == count


def test_graph_builds_without_any_learning_state(tmp_path):
    """Every Phase 4 query must still work when Phase 6 is disabled."""
    from tests.unit.kg_fixture import full_graph

    graph = full_graph(tmp_path)
    assert graph.nodes
    assert not any(n.type in ("framework", "style", "template") for n in graph.nodes.values())
