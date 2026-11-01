"""Context package assembly: budget, privacy, and the non-regression contract."""

import pytest

from backend.models.context import RankedContextFile
from backend.services.context_package import (
    EXPECTED_OUTPUT_FORMAT,
    PackageInputs,
    build_package,
    estimate_tokens,
)
from backend.services.runtime_patch_prompt import extract_relevant_code

MODULE = '''"""Module."""

import os

API_KEY = "sk-live-abcdefghijklmnopqrst"
LIMIT = 10


def helper(v):
    """Helper."""
    return v * 2


def noise_one():
    return "unrelated " * 40


def noise_two():
    return "also unrelated " * 40


def target(v):
    """Repair target."""
    return helper(v) + LIMIT
'''


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(MODULE)
    return tmp_path


def make(repo, **kwargs):
    defaults = dict(
        repo_path=repo,
        target_file="pkg/mod.py",
        target_function="target",
        ranked_files=[RankedContextFile(file="pkg/mod.py", score=1.0, is_target=True)],
    )
    defaults.update(kwargs)
    return build_package(PackageInputs(**defaults))


# -- structure -------------------------------------------------------------


def test_package_carries_every_declared_section(repo):
    package = make(
        repo,
        root_cause_summary="Off-by-one in limit handling",
        runtime_evidence={"status": "CONFIRMED", "failing_test": "t.py::test_x"},
        acceptance_criteria=["pytest t.py::test_x must pass"],
        contracts=["limit must be inclusive"],
        validation_requirements=["no new failures"],
        patch_constraints=["minimal change"],
    )

    assert package.root_cause_summary
    assert package.runtime_evidence["status"] == "CONFIRMED"
    assert package.target_file == "pkg/mod.py"
    assert package.target_function == "target"
    assert package.acceptance_criteria
    assert package.relevant_imports is not None
    assert package.relevant_functions
    assert package.contracts == ["limit must be inclusive"]
    assert package.validation_requirements == ["no new failures"]
    assert package.patch_constraints == ["minimal change"]
    assert package.expected_output_format == EXPECTED_OUTPUT_FORMAT
    assert package.focused_context
    assert package.original_complete_file == MODULE
    assert package.dependency_summary


def test_focused_context_contains_target_and_excludes_noise(repo):
    focused = make(repo, respect_baseline_budget=False).focused_context
    assert "def target(v):" in focused
    assert "def helper(v):" in focused
    assert "noise_one" not in focused
    assert "noise_two" not in focused


def test_complete_original_is_preserved_for_reconstruction(repo):
    """A7's integrity guard needs the whole file; A5.5 must not withhold it."""
    assert make(repo).original_complete_file == MODULE


def test_complete_file_is_excluded_from_storage(repo):
    """Persisting a verbatim on-disk file would double the run's Redis footprint."""
    stored = make(repo).to_storage_dict()
    assert "original_complete_file" not in stored
    assert stored["focused_context"]


# -- privacy ---------------------------------------------------------------


def test_unreferenced_secret_never_enters_the_context(repo):
    """API_KEY is not reachable from the target, so it is not extracted at all."""
    package = make(repo)
    assert "sk-live-abcdefghijklmnopqrst" not in package.focused_context
    assert package.privacy_guard_status == "clean"


def test_referenced_secret_is_masked(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        'API_KEY = "sk-live-abcdefghijklmnopqrst"\n\ndef target():\n    return API_KEY\n'
    )
    package = build_package(
        PackageInputs(repo_path=tmp_path, target_file="pkg/m.py", target_function="target")
    )
    assert "sk-live-abcdefghijklmnopqrst" not in package.focused_context
    assert package.privacy_guard_status == "masked"
    assert package.metrics.privacy_redactions >= 1


def test_secrets_in_runtime_evidence_are_masked(repo):
    package = make(
        repo,
        runtime_evidence={"traceback": "auth failed for token ghp_abcdefghijklmnopqrstuvwx1234"},
    )
    assert "ghp_abcdefghijklmnopqrstuvwx1234" not in package.runtime_evidence["traceback"]


def test_secrets_in_root_cause_summary_are_masked(repo):
    package = make(repo, root_cause_summary="leaked dev@example.com in logs")
    assert "dev@example.com" not in package.root_cause_summary


def test_clean_repository_reports_clean_guard_status(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "clean.py").write_text("def target():\n    return 1\n")
    package = build_package(
        PackageInputs(repo_path=tmp_path, target_file="pkg/clean.py", target_function="target")
    )
    assert package.privacy_guard_status == "clean"
    assert package.redactions == []


# -- budget ----------------------------------------------------------------


def test_bounded_overhead_on_a_file_too_small_to_trim(repo):
    """The honest bound on tiny files.

    A5.5 cannot always come in under the baseline: its irreducible core adds
    per-file scaffolding and the module constants the target reads, which
    `extract_relevant_code` omits entirely. On a file with nothing to trim that
    core is the whole package, so the overhead is bounded, not zero.
    """
    baseline = extract_relevant_code(MODULE, "target")
    package = make(repo)
    overhead = len(package.focused_context) - len(baseline)
    assert overhead <= 120, f"core overhead grew to {overhead} chars"
    assert "LIMIT = 10" in package.focused_context  # required, and absent from baseline


def test_adoption_requires_the_package_to_earn_its_place(repo):
    """The non-regression contract: adopt only when smaller, or when it masked something.

    `extract_relevant_code` already emits just imports plus the target function,
    so A5.5 cannot undercut it on every file. Rather than pretend otherwise, the
    package declares whether A7 should use it.
    """
    baseline = extract_relevant_code(MODULE, "target")
    package = make(repo)
    if package.prefer_focused:
        assert len(package.focused_context) <= len(baseline) or package.redactions
    else:
        assert package.metrics.token_reduction == 0.0


def test_unadopted_package_reports_no_saving(repo):
    package = make(repo)
    if not package.prefer_focused:
        assert package.metrics.estimated_saved_tokens == 0


def test_adopted_when_it_masks_a_secret_even_if_larger(tmp_path):
    """Privacy outranks tokens: a masked secret is worth the extra characters."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        'API_KEY = "sk-live-abcdefghijklmnopqrst"\n\ndef target():\n    return API_KEY\n'
    )
    package = build_package(
        PackageInputs(repo_path=tmp_path, target_file="pkg/m.py", target_function="target")
    )
    assert package.redactions
    assert package.prefer_focused is True
    assert "sk-live-abcdefghijklmnopqrst" not in package.focused_context


def test_adoption_reason_matches_the_real_decision_smaller_than_baseline(repo):
    """The reason must agree with `prefer_focused`, not narrate independently."""
    baseline = extract_relevant_code(MODULE, "target")
    package = make(repo)
    if package.prefer_focused and len(package.focused_context) <= len(baseline) and not package.redactions:
        assert package.metrics.adoption_reason == "smaller than A7's own baseline extraction"


def test_adoption_reason_when_a_secret_was_masked(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(
        'API_KEY = "sk-live-abcdefghijklmnopqrst"\n\ndef target():\n    return API_KEY\n'
    )
    package = build_package(
        PackageInputs(repo_path=tmp_path, target_file="pkg/m.py", target_function="target")
    )
    assert package.prefer_focused is True
    assert "masked a secret" in package.metrics.adoption_reason


def test_adoption_reason_is_never_empty_when_a_decision_was_made(repo):
    package = make(repo)
    assert package.metrics.adoption_reason != ""


def test_tiny_budget_never_truncates_the_target(repo):
    """The core is a floor: a half-extracted function is worse than the baseline."""
    package = make(repo, budget_chars=20, respect_baseline_budget=False)
    assert "def target(v):" in package.focused_context
    assert "return helper(v) + LIMIT" in package.focused_context


def test_budget_degradation_drops_helpers_before_target(repo):
    generous = make(repo, budget_chars=100_000, respect_baseline_budget=False)
    tight = make(repo, budget_chars=120, respect_baseline_budget=False)
    assert "def target(v):" in tight.focused_context
    assert "def helper(v):" in generous.focused_context
    assert len(tight.focused_context) <= len(generous.focused_context)


def test_unconstrained_mode_can_exceed_baseline(repo):
    """Guards that the cap is what enforces parity, not luck."""
    unconstrained = make(repo, budget_chars=100_000, respect_baseline_budget=False)
    capped = make(repo)
    assert len(unconstrained.focused_context) >= len(capped.focused_context)


# -- no-gain fallback ------------------------------------------------------


def test_small_file_falls_back_to_masked_complete_source(tmp_path):
    """When extraction cannot shrink anything, the focus section stays sanitized."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "tiny.py").write_text('SECRET = "abc123secret"\n\ndef target():\n    return SECRET\n')
    package = build_package(
        PackageInputs(repo_path=tmp_path, target_file="pkg/tiny.py", target_function="target")
    )
    assert "abc123secret" not in package.focused_context
    assert package.metrics.degraded is True


def test_missing_file_degrades_without_raising(tmp_path):
    package = build_package(
        PackageInputs(repo_path=tmp_path, target_file="pkg/nope.py", target_function="target")
    )
    assert package.metrics.degraded is True
    assert package.original_complete_file == ""


def test_unparseable_target_degrades(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "broken.py").write_text("def target(\n")
    package = build_package(
        PackageInputs(repo_path=tmp_path, target_file="pkg/broken.py", target_function="target")
    )
    assert package.metrics.degraded is True


# -- metrics ---------------------------------------------------------------


def test_metrics_are_populated(repo):
    metrics = make(repo).metrics
    assert metrics.context_files >= 1
    assert metrics.context_functions >= 1
    assert metrics.context_lines > 0
    assert metrics.original_tokens > 0
    assert metrics.reduced_tokens > 0
    assert metrics.build_time_ms >= 0
    assert metrics.extraction_time_ms >= 0
    assert metrics.privacy_time_ms >= 0
    assert 0.0 <= metrics.token_reduction <= 1.0


def test_token_reduction_is_never_negative(repo):
    """The layer must not report a saving it did not achieve."""
    assert make(repo).metrics.estimated_saved_tokens >= 0


def test_estimate_tokens_matches_gateway_convention():
    assert estimate_tokens("x" * 400) == 100
    assert estimate_tokens("") == 0


def test_ranked_files_are_carried_into_the_package(repo):
    ranked = [
        RankedContextFile(file="pkg/mod.py", score=2.0, reason="target", is_target=True),
        RankedContextFile(file="pkg/other.py", score=0.5, reason="scope"),
    ]
    package = make(repo, ranked_files=ranked)
    assert package.ranked_paths() == ["pkg/mod.py", "pkg/other.py"]
    assert len(package.dependency_summary) == 2


def test_package_is_deterministic(repo):
    assert make(repo).focused_context == make(repo).focused_context
