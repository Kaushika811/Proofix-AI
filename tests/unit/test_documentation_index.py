"""Documentation indexing: extraction, module linking, and the reverse index."""

import pytest

from backend.services.documentation_index import (
    build_documentation_index,
    discover_documentation_files,
    documentation_signal,
    extract_entities,
    extract_linked_modules,
    extract_referenced_functions,
    extract_topics,
    _module_lookup,
)
from backend.services.python_ast_parser import parse_source

README = """# ProoFix

An autonomous repair system.

## Architecture

The `pkg/auth.py` module owns session handling. Call `login()` to start one.

## Usage

```python
from pkg.auth import login
login("user")
```
"""

GUIDE = """# Auth guide

`Session` is the core type. See `pkg.auth` for details.
"""

MODULE = '''"""Auth module.

Handles sessions.
"""

# Ownership: platform team


class Session:
    """A session."""


def login(user):
    """Start a session."""
    return Session()


def _private():
    return None
'''


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "README.md").write_text(README)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "auth.md").write_text(GUIDE)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "auth.py").write_text(MODULE)
    return tmp_path


def test_discovery_finds_readme_and_docs_directory(repo):
    found = discover_documentation_files(repo)
    assert "README.md" in found
    assert "docs/auth.md" in found


def test_discovery_on_empty_repo_returns_nothing(tmp_path):
    assert discover_documentation_files(tmp_path) == []


def test_extract_topics_from_headings():
    assert extract_topics(README) == ["proofix", "architecture", "usage"]


def test_extract_topics_handles_rst_underlines():
    assert "overview" in extract_topics("Overview\n========\n\ntext\n")


def test_extract_entities_uses_code_spans_only():
    entities = extract_entities("The `Session` type. Repository is capitalised prose.")
    assert "Session" in entities
    assert "Repository" not in entities


def test_extract_entities_drops_common_noise():
    assert extract_entities("Run `pip` and `bash` and `Session`") == ["Session"]


def test_extract_referenced_functions_from_code_spans_and_fences():
    names = extract_referenced_functions(README)
    assert "login" in names


def test_referenced_functions_ignore_prose_parentheses():
    assert extract_referenced_functions("We call foo() in prose.") == []


def test_module_lookup_resolves_paths_dotted_names_and_basenames():
    lookup = _module_lookup(["pkg/auth.py", "pkg/util.py"])
    assert lookup["pkg/auth.py"] == "pkg/auth.py"
    assert lookup["pkg.auth"] == "pkg/auth.py"
    assert lookup["auth"] == "pkg/auth.py"


def test_ambiguous_basename_resolves_to_nothing():
    """`auth` naming two files identifies neither."""
    lookup = _module_lookup(["a/auth.py", "b/auth.py"])
    assert "auth" not in lookup
    assert lookup["a.auth"] == "a/auth.py"


def test_extract_linked_modules_resolves_against_repository_files():
    lookup = _module_lookup(["pkg/auth.py"])
    assert extract_linked_modules(README, lookup) == ["pkg/auth.py"]


def test_unknown_module_is_not_linked():
    lookup = _module_lookup(["pkg/auth.py"])
    assert extract_linked_modules("See `requests.get`", lookup) == []


def test_index_entry_kinds(repo):
    index = build_documentation_index(repo, ["pkg/auth.py"], {})
    kinds = {e.kind for e in index.entries}
    assert kinds == {"readme", "markdown"}
    assert next(e for e in index.entries if e.kind == "readme").title == "ProoFix"


def test_docstring_and_comment_entries_are_produced(repo):
    parsed = {"pkg/auth.py": parse_source(MODULE)}
    index = build_documentation_index(repo, ["pkg/auth.py"], parsed)
    kinds = {e.kind for e in index.entries}
    assert "docstring" in kinds
    assert "comment" in kinds

    doc = next(e for e in index.entries if e.kind == "docstring")
    assert "Session" in doc.entities
    assert "login" in doc.referenced_functions


def test_undocumented_module_produces_no_docstring_entry(repo):
    parsed = {"pkg/bare.py": parse_source("def f():\n    return 1\n")}
    index = build_documentation_index(repo, ["pkg/bare.py"], parsed)
    assert not any(e.kind == "docstring" and e.path == "pkg/bare.py" for e in index.entries)


def test_reverse_index_maps_modules_to_entries(repo):
    index = build_documentation_index(repo, ["pkg/auth.py"], {})
    assert index.by_module["pkg/auth.py"]
    assert index.entries_for_module("pkg/auth.py")


def test_topic_counts_are_aggregated(repo):
    index = build_documentation_index(repo, ["pkg/auth.py"], {})
    assert index.topics["architecture"] == 1


def test_relevance_saturates_and_defaults_to_zero(repo):
    parsed = {"pkg/auth.py": parse_source(MODULE)}
    index = build_documentation_index(repo, ["pkg/auth.py"], parsed)
    assert 0 < documentation_signal(index, "pkg/auth.py") <= 1.0
    assert documentation_signal(index, "pkg/undocumented.py") == 0.0


def test_unreadable_document_is_skipped(repo):
    (repo / "docs" / "binary.md").write_bytes(b"\xff\xfe\x00broken")
    index = build_documentation_index(repo, ["pkg/auth.py"], {})
    assert not any(e.path == "docs/binary.md" for e in index.entries)


def test_build_is_deterministic(repo):
    parsed = {"pkg/auth.py": parse_source(MODULE)}
    first = build_documentation_index(repo, ["pkg/auth.py"], parsed)
    second = build_documentation_index(repo, ["pkg/auth.py"], parsed)
    assert [e.path for e in first.entries] == [e.path for e in second.entries]
    assert first.topics == second.topics
