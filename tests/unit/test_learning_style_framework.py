"""Style and framework learning — deterministic, from counting only."""

import pytest

from backend.models.learning import StyleProfile
from backend.learning.framework_learning import (
    CONFIDENCE_SATURATION,
    FRAMEWORKS,
    detect_frameworks,
    framework_match_score,
    learn_framework,
    read_manifest_packages,
)
from backend.learning.style_learning import (
    DOMINANCE_THRESHOLD,
    MIN_OBSERVATIONS,
    StyleLearner,
    classify_name,
    detect_docstring_style,
    learn_style,
    style_match_score,
)
from backend.services.python_ast_parser import parse_source


def parsed(source: str):
    module = parse_source(source)
    assert module is not None
    return module


def learn(sources: dict[str, str]) -> StyleProfile:
    learner = StyleLearner("repo")
    for path, source in sources.items():
        learner.observe(path, parsed(source), source)
    return learner.profile()


# ===================================================== naming classification


@pytest.mark.parametrize(
    "name,expected",
    [
        ("do_thing", "snake_case"),
        ("doThing", "camelCase"),
        ("DoThing", "PascalCase"),
        ("MAX_SIZE", "SCREAMING_SNAKE"),
        ("_private_helper", "snake_case"),
        ("x", "snake_case"),
    ],
)
def test_classify_name(name, expected):
    assert classify_name(name) == expected


def test_dunder_is_not_classified():
    """Dunders are named by the language, not by the repository."""
    assert classify_name("__init__") == "unknown"


def test_empty_name_is_unknown():
    assert classify_name("") == "unknown"
    assert classify_name("_") == "unknown"


# ===================================================== docstring style


@pytest.mark.parametrize(
    "docstring,expected",
    [
        ("Summary.\n\nArgs:\n    x: a value\n\nReturns:\n    something\n", "google"),
        ("Summary.\n\nParameters\n----------\nx : int\n", "numpy"),
        ("Summary.\n\n:param x: a value\n:returns: something\n", "sphinx"),
        ("Just a sentence.", "plain"),
        ("", "none"),
    ],
)
def test_docstring_styles(docstring, expected):
    assert detect_docstring_style(docstring) == expected


def test_google_wins_over_sphinx_when_both_shapes_appear():
    text = "Summary.\n\nArgs:\n    x: a value\n\n:param y: other\n"
    assert detect_docstring_style(text) == "google"


# ===================================================== style learning


def test_dominant_naming_is_detected():
    sources = {
        f"pkg/m{i}.py": "\n\n".join(f"def do_thing_{j}():\n    return {j}" for j in range(3))
        for i in range(3)
    }
    assert learn(sources).function_naming == "snake_case"


def test_camel_case_repository_is_detected():
    sources = {
        f"pkg/m{i}.py": "\n\n".join(f"def doThing{j}():\n    return {j}" for j in range(3))
        for i in range(3)
    }
    assert learn(sources).function_naming == "camelCase"


def test_class_naming_is_detected():
    source = "\n\n".join(f"class Thing{i}:\n    pass" for i in range(6))
    assert learn({"pkg/a.py": source}).class_naming == "PascalCase"


def test_constants_are_detected():
    source = "\n".join(f"MAX_{i} = {i}" for i in range(6))
    assert learn({"pkg/a.py": source}).constant_naming == "SCREAMING_SNAKE"


def test_mixed_conventions_report_mixed():
    """Reporting `mixed` tells the generator not to impose a convention."""
    source = (
        "def do_thing():\n    pass\n\n\ndef doOther():\n    pass\n\n\n"
        "def MoreThing():\n    pass\n\n\ndef another_one():\n    pass\n\n\n"
        "def yetAnother():\n    pass\n\n\ndef andMore():\n    pass\n"
    )
    assert learn({"pkg/a.py": source}).function_naming == "mixed"


def test_small_sample_reports_mixed():
    """One function is not a convention."""
    assert learn({"pkg/a.py": "def doThing():\n    pass\n"}).function_naming == "mixed"


def test_thresholds_are_declared():
    assert 0.5 < DOMINANCE_THRESHOLD <= 1.0
    assert MIN_OBSERVATIONS >= 2


def test_test_files_are_excluded():
    """Test naming differs systematically and would shift every distribution."""
    learner = StyleLearner("repo")
    learner.observe("tests/test_a.py", parsed("def test_should_do_thing():\n    pass\n"), "")
    assert learner.files_analyzed == 0


def test_quote_style_is_detected():
    source = "\n".join(f'X{i} = "value{i}"' for i in range(8))
    assert learn({"pkg/a.py": source}).quote_style == "double"


def test_single_quote_style_is_detected():
    source = "\n".join(f"X{i} = 'value{i}'" for i in range(8))
    assert learn({"pkg/a.py": source}).quote_style == "single"


def test_docstring_coverage_is_measured():
    source = (
        'def a():\n    """Doc."""\n    pass\n\n\n'
        'def b():\n    """Doc."""\n    pass\n\n\n'
        "def c():\n    pass\n"
    )
    profile = learn({"pkg/a.py": source})
    assert 0.5 < profile.docstring_coverage < 1.0


def test_type_hint_coverage_is_measured():
    typed = learn({"pkg/a.py": "def a(x: int) -> int:\n    return x\n"})
    untyped = learn({"pkg/b.py": "def a(x):\n    return x\n"})
    assert typed.type_hint_coverage > untyped.type_hint_coverage


def test_async_ratio_is_measured():
    source = "async def a():\n    pass\n\n\nasync def b():\n    pass\n\n\ndef c():\n    pass\n"
    assert 0 < learn({"pkg/a.py": source}).async_ratio < 1.0


def test_logging_module_is_detected():
    sources = {f"pkg/m{i}.py": "import logging\n\n\ndef go():\n    pass\n" for i in range(6)}
    assert learn(sources).logging_style == "logging_module"


def test_structlog_is_detected():
    sources = {f"pkg/m{i}.py": "import structlog\n\n\ndef go():\n    pass\n" for i in range(6)}
    assert learn(sources).logging_style == "structlog"


def test_custom_exception_hierarchy_is_detected():
    source = "\n\n".join(f"class Thing{i}Error(Exception):\n    pass" for i in range(6))
    assert learn({"pkg/a.py": source}).exception_style in ("custom_hierarchy", "mixed")


def test_indent_is_detected():
    source = "def a():\n  return 1\n\n\ndef b():\n  return 2\n"
    assert learn({"pkg/a.py": source}).indent == 2


def test_max_line_length_is_recorded():
    assert learn({"pkg/a.py": "x = " + "1" * 120 + "\n"}).max_line_length >= 120


def test_files_analyzed_is_counted():
    assert learn({f"pkg/m{i}.py": "x = 1\n" for i in range(4)}).files_analyzed == 4


def test_observations_carry_distributions():
    source = "\n\n".join(f"def do_thing_{i}():\n    pass" for i in range(6))
    profile = learn({"pkg/a.py": source})
    observation = next(o for o in profile.observations if o.property == "function_naming")
    assert observation.observations == 6
    assert observation.distribution["snake_case"] == 6
    assert "snake_case" in observation.describe()


def test_empty_repository_yields_unknown_profile():
    profile = learn({})
    assert profile.function_naming == "unknown"
    assert profile.confidence == 0.0


def test_confidence_rises_with_sample_size():
    small = learn({"pkg/a.py": "\n\n".join(f"def do_{i}():\n    pass" for i in range(6))})
    large = learn({
        f"pkg/m{i}.py": "\n\n".join(f"def do_{j}():\n    pass" for j in range(6))
        for i in range(25)
    })
    assert large.confidence > small.confidence


def test_learning_is_deterministic():
    sources = {f"pkg/m{i}.py": f"def do_thing_{i}():\n    pass\n" for i in range(6)}
    assert learn(sources).model_dump(exclude={"observations"}) == learn(sources).model_dump(
        exclude={"observations"}
    )


def test_learn_style_without_a_repo_path():
    modules = {f"pkg/m{i}.py": parsed(f"def do_thing_{i}():\n    pass\n") for i in range(6)}
    profile = learn_style(None, modules, "repo", read_sources=False)
    assert profile.function_naming == "snake_case"


# -- directives ------------------------------------------------------------


def test_directives_reflect_the_profile():
    sources = {
        f"pkg/m{i}.py": "import logging\n\n\n" + "\n\n".join(
            f'def do_thing_{j}(x: int) -> int:\n    """Doc."""\n    return {j}' for j in range(3)
        )
        for i in range(4)
    }
    directives = " ".join(learn(sources).prompt_directives())
    assert "snake_case" in directives
    assert "logging" in directives


def test_unknown_profile_yields_no_naming_directive():
    assert not any("Name functions" in d for d in StyleProfile().prompt_directives())


def test_mixed_convention_is_not_imposed():
    profile = StyleProfile(function_naming="mixed")
    assert not any("Name functions" in d for d in profile.prompt_directives())


# -- conformance -----------------------------------------------------------


def test_style_match_rewards_conformance():
    profile = StyleProfile(function_naming="snake_case")
    assert style_match_score(profile, parsed("def do_thing():\n    pass\n")) == 1.0


def test_style_match_penalises_divergence():
    profile = StyleProfile(function_naming="snake_case")
    assert style_match_score(profile, parsed("def doThing():\n    pass\n")) == 0.0


def test_style_match_without_a_convention_is_zero():
    assert style_match_score(StyleProfile(), parsed("def a():\n    pass\n")) == 0.0


# ===================================================== framework learning


def test_import_detects_framework():
    profile = detect_frameworks(None, {"fastapi"})
    assert profile.primary_framework == "FastAPI"
    assert profile.confidence > 0


def test_markers_alone_do_not_establish_a_framework():
    """`models.py` and `routes/` are ordinary names."""
    profile = detect_frameworks(None, set(), ("app/models.py", "app/urls.py"))
    assert profile.primary_framework == "unknown"


def test_markers_corroborate_an_import():
    bare = detect_frameworks(None, {"django"})
    with_markers = detect_frameworks(None, {"django"}, ("manage.py", "urls.py", "settings.py"))
    assert with_markers.confidence > bare.confidence


def test_manifest_dependency_contributes(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.110\nuvicorn\n")
    profile = detect_frameworks(tmp_path, {"fastapi"})
    assert profile.confidence > detect_frameworks(None, {"fastapi"}).confidence


def test_package_json_is_parsed(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4"}}')
    packages, read = read_manifest_packages(tmp_path)
    assert "express" in packages
    assert "package.json" in read


def test_malformed_package_json_is_survived(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    packages, _read = read_manifest_packages(tmp_path)
    assert isinstance(packages, set)


def test_requirements_are_parsed(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask>=2.0\n# comment\npytest\n")
    packages, _read = read_manifest_packages(tmp_path)
    assert "flask" in packages


def test_no_manifest_yields_nothing(tmp_path):
    assert read_manifest_packages(tmp_path) == (set(), [])


def test_competing_frameworks_are_both_reported():
    """A migration in progress is a real state."""
    profile = detect_frameworks(None, {"flask", "fastapi"})
    assert len(profile.frameworks) == 2


def test_primary_is_the_best_supported(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    profile = detect_frameworks(tmp_path, {"flask", "fastapi"})
    assert profile.primary_framework == "FastAPI"


def test_no_evidence_yields_unknown():
    profile = detect_frameworks(None, {"os", "sys"})
    assert profile.primary_framework == "unknown"
    assert profile.conventions == []


def test_conventions_are_attached():
    profile = detect_frameworks(None, {"fastapi"})
    aspects = {c.aspect for c in profile.conventions}
    assert {"routing", "validation", "testing"} <= aspects


def test_convention_lookup():
    profile = detect_frameworks(None, {"django"})
    assert profile.convention_for("orm") is not None
    assert profile.convention_for("nonexistent") is None


def test_conventions_carry_evidence():
    profile = detect_frameworks(None, {"fastapi"})
    assert all(c.evidence for c in profile.conventions)


def test_directives_mention_the_framework():
    directives = detect_frameworks(None, {"fastapi"}).prompt_directives()
    assert any("FastAPI" in d for d in directives)


def test_unknown_framework_yields_no_directives():
    assert detect_frameworks(None, set()).prompt_directives() == []


@pytest.mark.parametrize("spec", FRAMEWORKS, ids=lambda s: s.name)
def test_every_framework_is_detectable(spec):
    root = spec.imports[0].split(".")[0]
    assert detect_frameworks(None, {root}).primary_framework == spec.name


def test_confidence_saturation_is_reachable():
    """An import plus a manifest should read as near-certain."""
    assert CONFIDENCE_SATURATION <= 1.6 + 0.3


def test_learn_framework_from_parsed_modules():
    modules = {"app/main.py": parsed("import fastapi\n\n\ndef go():\n    pass\n")}
    assert learn_framework(None, modules, "repo").primary_framework == "FastAPI"


def test_detection_is_deterministic():
    first = detect_frameworks(None, {"fastapi", "flask"})
    second = detect_frameworks(None, {"fastapi", "flask"})
    assert first.primary_framework == second.primary_framework
    assert first.frameworks == second.frameworks


# -- conformance -----------------------------------------------------------


def test_framework_match_rewards_the_primary():
    profile = detect_frameworks(None, {"fastapi"})
    assert framework_match_score(profile, {"fastapi"}) == 1.0


def test_framework_match_penalises_a_competitor():
    """Introducing Flask into a FastAPI repository is what a reviewer flags."""
    profile = detect_frameworks(None, {"fastapi"})
    assert framework_match_score(profile, {"flask"}) == 0.0


def test_framework_agnostic_change_is_neutral():
    profile = detect_frameworks(None, {"fastapi"})
    assert framework_match_score(profile, {"os", "json"}) == 0.5


def test_framework_match_without_a_profile_is_zero():
    from backend.models.learning import FrameworkProfile

    assert framework_match_score(FrameworkProfile(), {"fastapi"}) == 0.0
