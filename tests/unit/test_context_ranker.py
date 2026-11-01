"""Ranking: deterministic, evidence-weighted, and explainable."""

from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.context_ranker import (
    RankingInputs,
    parse_stack_files,
    rank_files,
)


def sig(**files: FileNode) -> SemanticIntentGraph:
    return SemanticIntentGraph(repo_path="/repo", source_roots=["pkg/"], files=dict(files))


def node(path: str, **kwargs) -> FileNode:
    return FileNode(path=path, **kwargs)


BASE_SIG = sig(
    **{
        "pkg/auth.py": node("pkg/auth.py", role="auth-boundary", churn_weight=0.5),
        "pkg/api.py": node("pkg/api.py", role="public-api"),
        "pkg/util.py": node("pkg/util.py", role="internal-util"),
    }
)


def scored(ranked, path):
    return next(f for f in ranked if f.file == path)


# -- stack parsing ---------------------------------------------------------


def test_parses_application_frames_in_order():
    trace = (
        'File "/repo/pkg/api.py", line 10, in handler\n'
        'File "/repo/pkg/auth.py", line 42, in validate\n'
    )
    assert parse_stack_files(trace) == ["/repo/pkg/api.py", "/repo/pkg/auth.py"]


def test_drops_library_frames():
    trace = (
        'File "/venv/lib/python3.12/site-packages/flask/app.py", line 1, in x\n'
        'File "/repo/pkg/auth.py", line 42, in validate\n'
    )
    assert parse_stack_files(trace) == ["/repo/pkg/auth.py"]


def test_drops_pytest_frames():
    trace = 'File "/venv/lib/_pytest/python.py", line 1, in call\nFile "/repo/pkg/a.py", line 2, in t\n'
    assert parse_stack_files(trace) == ["/repo/pkg/a.py"]


def test_empty_traceback():
    assert parse_stack_files("") == []
    assert parse_stack_files("no frames here") == []


# -- signals ---------------------------------------------------------------


def test_resolved_target_scores_highest():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py", "pkg/api.py"],
            resolved_target="pkg/auth.py",
            target_confidence=1.0,
        )
    )
    assert ranked[0].file == "pkg/auth.py"
    assert ranked[0].is_target is True
    assert "target_resolution" in ranked[0].signals


def test_runtime_stack_beats_static_finding():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py", "pkg/api.py"],
            stack_frames=["/repo/pkg/auth.py"],
            static_findings=[{"file": "pkg/api.py", "severity": 1.0}],
        )
    )
    assert ranked[0].file == "pkg/auth.py"


def test_innermost_stack_frame_outranks_outer():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py", "pkg/api.py"],
            stack_frames=["/repo/pkg/api.py", "/repo/pkg/auth.py"],
        )
    )
    assert scored(ranked, "pkg/auth.py").score > scored(ranked, "pkg/api.py").score


def test_verified_citation_outweighs_unverified():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py", "pkg/api.py"],
            citations=[
                {"file": "pkg/auth.py", "line": 1, "verified": True},
                {"file": "pkg/api.py", "line": 1, "verified": False},
            ],
        )
    )
    assert scored(ranked, "pkg/auth.py").score > scored(ranked, "pkg/api.py").score


def test_reproduction_failing_file_scores():
    ranked = rank_files(
        RankingInputs(sig=BASE_SIG, auto_patch_scope=["pkg/auth.py"], failing_file="pkg/auth.py")
    )
    assert "reproduction_evidence" in scored(ranked, "pkg/auth.py").signals


def test_static_finding_scales_with_severity():
    high = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/api.py"],
            static_findings=[{"file": "pkg/api.py", "severity": 1.0}],
        )
    )
    low = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/api.py"],
            static_findings=[{"file": "pkg/api.py", "severity": 0.1}],
        )
    )
    assert high[0].score > low[0].score


def test_previously_patched_file_gains_recency_weight():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py"],
            previously_patched=["pkg/auth.py"],
        )
    )
    assert "recent_modification" in scored(ranked, "pkg/auth.py").signals


def test_mutation_relevance_signal():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py"],
            mutation_files=["pkg/auth.py"],
        )
    )
    assert "mutation_relevance" in scored(ranked, "pkg/auth.py").signals


def test_blast_propagation_and_import_distance():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/api.py"],
            blast_scope=[
                {"path": "pkg/api.py", "propagation_confidence": 0.9, "hop_count": 2}
            ],
        )
    )
    signals = scored(ranked, "pkg/api.py").signals
    assert "sig_distance" in signals
    assert "import_distance" in signals


def test_git_churn_and_centrality_from_sig():
    graph = sig(
        **{
            "pkg/auth.py": node(
                "pkg/auth.py", role="auth-boundary", churn_weight=0.8, imported_by=["a", "b"]
            )
        }
    )
    ranked = rank_files(RankingInputs(sig=graph, auto_patch_scope=["pkg/auth.py"]))
    signals = ranked[0].signals
    assert signals["git_churn"] > 0
    assert signals["dependency_centrality"] > 0


def test_shared_utility_is_penalised_without_runtime_evidence():
    graph = sig(
        **{
            "pkg/util.py": node(
                "pkg/util.py", role="internal-util", imported_by=[f"m{i}" for i in range(12)]
            )
        }
    )
    ranked = rank_files(RankingInputs(sig=graph, auto_patch_scope=["pkg/util.py"]))
    assert ranked[0].signals["shared_utility_penalty"] < 0


def test_shared_utility_penalty_waived_when_runtime_implicates_it():
    graph = sig(
        **{
            "pkg/util.py": node(
                "pkg/util.py", role="internal-util", imported_by=[f"m{i}" for i in range(12)]
            )
        }
    )
    ranked = rank_files(
        RankingInputs(
            sig=graph,
            auto_patch_scope=["pkg/util.py"],
            stack_frames=["/repo/pkg/util.py"],
        )
    )
    assert "shared_utility_penalty" not in ranked[0].signals


# -- output contract -------------------------------------------------------


def test_results_are_sorted_descending():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py", "pkg/api.py", "pkg/util.py"],
            stack_frames=["/repo/pkg/auth.py"],
            static_findings=[{"file": "pkg/api.py", "severity": 0.9}],
        )
    )
    assert [f.score for f in ranked] == sorted((f.score for f in ranked), reverse=True)


def test_ranking_is_deterministic():
    inputs = RankingInputs(
        sig=BASE_SIG,
        auto_patch_scope=["pkg/auth.py", "pkg/api.py", "pkg/util.py"],
        stack_frames=["/repo/pkg/auth.py"],
    )
    assert [f.file for f in rank_files(inputs)] == [f.file for f in rank_files(inputs)]


def test_equal_scores_break_ties_on_path():
    ranked = rank_files(RankingInputs(sig=BASE_SIG, auto_patch_scope=["pkg/b.py", "pkg/a.py"]))
    assert [f.file for f in ranked] == ["pkg/a.py", "pkg/b.py"]


def test_every_file_carries_its_evidence():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py"],
            stack_frames=["/repo/pkg/auth.py"],
            failing_file="pkg/auth.py",
        )
    )
    entry = ranked[0]
    assert entry.reason
    assert len(entry.evidence) >= 2
    assert 0.0 <= entry.confidence <= 1.0
    assert entry.signals


def test_no_candidates_yields_empty_ranking():
    assert rank_files(RankingInputs()) == []


def test_works_without_a_sig():
    ranked = rank_files(RankingInputs(auto_patch_scope=["pkg/auth.py"], failing_file="pkg/auth.py"))
    assert ranked[0].file == "pkg/auth.py"


def test_absolute_stack_path_matches_repo_relative_candidate():
    ranked = rank_files(
        RankingInputs(
            sig=BASE_SIG,
            auto_patch_scope=["pkg/auth.py"],
            stack_frames=["/tmp/build/repo/pkg/auth.py"],
        )
    )
    assert "runtime_stack" in ranked[0].signals


# -- vendor paths never reach the repair context ---------------------------


def test_vendor_path_in_auto_patch_scope_is_never_ranked():
    """Belt-and-suspenders: even if a vendor path reaches `rank_files`
    directly (bypassing `resolve_origins`'s own filter), it must not be
    scored or returned as a candidate."""
    ranked = rank_files(
        RankingInputs(auto_patch_scope=[".venv/Lib/site-packages/httpx/_auth.py"])
    )
    assert ranked == []


def test_vendor_path_does_not_survive_alongside_a_real_candidate():
    ranked = rank_files(
        RankingInputs(
            auto_patch_scope=["pkg/auth.py", "node_modules/lodash/lodash.js"],
            failing_file="pkg/auth.py",
        )
    )
    assert [f.file for f in ranked] == ["pkg/auth.py"]


def test_vendor_resolved_target_is_dropped_even_though_it_would_score_highest():
    ranked = rank_files(
        RankingInputs(
            resolved_target=".venv/site-packages/httpx/_auth.py",
            target_confidence=1.0,
            auto_patch_scope=["pkg/auth.py", ".venv/site-packages/httpx/_auth.py"],
        )
    )
    assert [f.file for f in ranked] == ["pkg/auth.py"]
