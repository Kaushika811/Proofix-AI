"""AST extraction: pull the target and what it reaches, leave the rest behind."""

from backend.services.context_extractor import (
    extract_from_file,
    extract_from_source,
    find_target_span,
    render_focused_context,
)
from backend.services.python_ast_parser import parse_source

MODULE = '''"""Module docstring."""

import os
import json
from dataclasses import dataclass

MAX_RETRIES = 3
UNUSED_CONSTANT = "not referenced"


@dataclass
class Config:
    """Configuration."""

    retries: int = MAX_RETRIES


class Unrelated:
    def method_a(self):
        return 1

    def method_b(self):
        return 2


def helper(value):
    """Helper docstring."""
    return value * 2


def unrelated_function():
    return "nothing to do with the target"


def target(value):
    """The function under repair."""
    limit = MAX_RETRIES
    return helper(value) + limit


def caller():
    return target(5)
'''


def extract(**kwargs):
    return extract_from_source(MODULE, "m.py", **kwargs)


# -- target resolution -----------------------------------------------------


def test_finds_top_level_target():
    parsed = parse_source(MODULE)
    assert find_target_span(parsed, "target").name == "target"


def test_finds_method_by_qualname():
    parsed = parse_source(MODULE)
    span = find_target_span(parsed, "Unrelated.method_a")
    assert span.name == "method_a"
    assert span.parent_class == "Unrelated"


def test_prefers_top_level_over_method_for_bare_name():
    source = "def run():\n    return 1\n\nclass C:\n    def run(self):\n        return 2\n"
    parsed = parse_source(source)
    assert find_target_span(parsed, "run").is_method is False


def test_ambiguous_method_name_refuses_to_guess():
    source = (
        "class A:\n    def run(self):\n        return 1\n"
        "class B:\n    def run(self):\n        return 2\n"
    )
    parsed = parse_source(source)
    assert find_target_span(parsed, "run") is None


def test_unknown_target_returns_none():
    assert find_target_span(parse_source(MODULE), "does_not_exist") is None


# -- what gets included ----------------------------------------------------


def test_target_is_extracted_in_full():
    extraction = extract(target_function="target")
    assert extraction.target is not None
    assert extraction.target.kind == "target_function"
    assert "def target(value):" in extraction.target.source
    assert "return helper(value) + limit" in extraction.target.source
    assert extraction.target.signature_only is False


def test_callee_is_included():
    names = {f.name for f in extract(target_function="target").functions}
    assert "helper" in names


def test_caller_is_included_as_signature_only():
    extraction = extract(target_function="target")
    caller = next(f for f in extraction.functions if f.name == "caller")
    assert caller.kind == "caller_function"
    assert caller.signature_only is True
    assert "def caller():" in caller.source


def test_unrelated_function_is_excluded():
    names = {f.name for f in extract(target_function="target").functions}
    assert "unrelated_function" not in names


def test_unrelated_class_is_excluded():
    names = {c.name for c in extract(target_function="target").classes}
    assert "Unrelated" not in names


def test_referenced_constant_is_included():
    names = {c.name for c in extract(target_function="target").constants}
    assert "MAX_RETRIES" in names
    assert "UNUSED_CONSTANT" not in names


def test_only_referenced_imports_are_kept():
    """`os` and `json` are unused by the target and must not be carried along."""
    imports = extract(target_function="target").imports
    assert not any("import os" in i for i in imports)
    assert not any("import json" in i for i in imports)


def test_extra_symbols_are_force_included():
    names = {f.name for f in extract(target_function="target", extra_symbols=("unrelated_function",)).functions}
    assert "unrelated_function" in names


def test_callers_can_be_disabled():
    names = {f.name for f in extract(target_function="target", include_callers=False).functions}
    assert "caller" not in names


# -- classes ---------------------------------------------------------------


def test_dataclass_is_labelled_and_extracted_whole():
    source = "from dataclasses import dataclass\n\n@dataclass\nclass Cfg:\n    a: int = 1\n\ndef target():\n    return Cfg()\n"
    extraction = extract_from_source(source, "m.py", target_function="target")
    cfg = next(c for c in extraction.classes if c.name == "Cfg")
    assert cfg.kind == "dataclass"
    assert "@dataclass" in cfg.source
    assert "a: int = 1" in cfg.source


def test_typed_model_is_labelled():
    source = "from pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str\n\ndef target():\n    return User()\n"
    extraction = extract_from_source(source, "m.py", target_function="target")
    assert next(c for c in extraction.classes if c.name == "User").kind == "typed_model"


def test_config_object_is_labelled():
    source = "class AppSettings:\n    debug = False\n\ndef target():\n    return AppSettings()\n"
    extraction = extract_from_source(source, "m.py", target_function="target")
    assert next(c for c in extraction.classes if c.name == "AppSettings").kind == "config_object"


def test_validation_helper_is_labelled():
    source = "def validate_input(v):\n    return bool(v)\n\ndef target(v):\n    return validate_input(v)\n"
    extraction = extract_from_source(source, "m.py", target_function="target")
    assert next(f for f in extraction.functions if f.name == "validate_input").kind == "validation_helper"


def test_enclosing_class_is_a_shell_not_the_whole_body():
    """Splicing the whole class would re-emit the target and every sibling."""
    source = (
        "class Service:\n"
        '    """Service docstring."""\n\n'
        "    limit = 10\n\n"
        "    def target(self):\n"
        "        return self.limit\n\n"
        "    def sibling_one(self):\n"
        "        return 1\n\n"
        "    def sibling_two(self):\n"
        "        return 2\n"
    )
    extraction = extract_from_source(source, "m.py", target_function="Service.target")
    shell = next(c for c in extraction.classes if c.name == "Service").source

    assert "class Service:" in shell
    assert "limit = 10" in shell
    assert "return 1" not in shell
    assert "return 2" not in shell
    assert shell.count("def target") == 0
    assert "other methods omitted" in shell


def test_nested_class_definitions_are_indexed():
    source = "class Outer:\n    class Inner:\n        pass\n\n    def target(self):\n        return 1\n"
    parsed = parse_source(source)
    assert find_target_span(parsed, "Outer.target") is not None


# -- decorators ------------------------------------------------------------


def test_decorators_are_kept_with_the_function():
    source = (
        "def deco(f):\n    return f\n\n"
        "@deco\n"
        "@deco\n"
        "def target():\n"
        "    return 1\n"
    )
    extraction = extract_from_source(source, "m.py", target_function="target")
    assert extraction.target.source.count("@deco") == 2
    assert extraction.target.lineno < extraction.target.end_lineno


def test_async_function_target():
    source = "async def target():\n    return 1\n"
    extraction = extract_from_source(source, "m.py", target_function="target")
    assert "async def target():" in extraction.target.source


# -- outline mode ----------------------------------------------------------


def test_outline_mode_when_no_target_resolves():
    extraction = extract(target_function=None)
    assert extraction.degraded is True
    assert all(f.signature_only for f in extraction.functions)
    names = {f.name for f in extraction.functions}
    assert {"target", "helper", "unrelated_function"} <= names


def test_outline_is_smaller_than_the_whole_file():
    rendered = render_focused_context([extract(target_function=None)])
    assert len(rendered) < len(MODULE)


def test_outline_includes_all_constants_and_imports():
    extraction = extract(target_function=None)
    assert {"MAX_RETRIES", "UNUSED_CONSTANT"} <= {c.name for c in extraction.constants}
    assert len(extraction.imports) == 3


# -- degraded paths --------------------------------------------------------


def test_unparseable_source_degrades_cleanly():
    extraction = extract_from_source("def broken(\n", "m.py", target_function="broken")
    assert extraction.degraded is True
    assert "could not be parsed" in extraction.degraded_reason
    assert extraction.functions == []


def test_missing_file_degrades_cleanly(tmp_path):
    extraction = extract_from_file(tmp_path, "nope.py", target_function="x")
    assert extraction.degraded is True
    assert "unreadable" in extraction.degraded_reason


def test_empty_source():
    extraction = extract_from_source("", "m.py", target_function="x")
    assert extraction.functions == []


# -- rendering -------------------------------------------------------------


def test_rendering_marks_the_repair_target():
    rendered = render_focused_context([extract(target_function="target")])
    assert "# >>> REPAIR TARGET" in rendered
    assert "m.py" in rendered


def test_rendering_places_target_before_helpers():
    """Truncation eats the tail, so the target must never be in it."""
    rendered = render_focused_context([extract(target_function="target")])
    assert rendered.index("def target(value):") < rendered.index("def caller():")


def test_rendering_is_deterministic():
    first = render_focused_context([extract(target_function="target")])
    second = render_focused_context([extract(target_function="target")])
    assert first == second


def test_rendering_skips_empty_extractions():
    assert render_focused_context([]) == ""
