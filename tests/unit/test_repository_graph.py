"""Repository graph: node kinds, edge kinds, and resolution boundaries."""

import pytest

from backend.services.repository_graph import (
    build_module_index,
    build_repository_graph,
    discover_python_files,
    is_config_file,
    is_test_file,
    node_id,
)

AUTH = '''"""Auth module."""

import os

from pkg.util import helper

MAX_AGE = 30


class Session:
    """A session."""

    def refresh(self):
        return helper(MAX_AGE)


class AdminSession(Session):
    def escalate(self):
        return True


def login(user):
    return Session()
'''

UTIL = '''def helper(value):
    return value + 1
'''

TEST_AUTH = '''from pkg.auth import login


def test_login():
    assert login("x")
'''


@pytest.fixture
def repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "auth.py").write_text(AUTH)
    (pkg / "util.py").write_text(UTIL)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_auth.py").write_text(TEST_AUTH)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    return tmp_path


@pytest.fixture
def graph(repo):
    built, _ = build_repository_graph(repo, ["pkg/"])
    return built


def test_is_test_file_matches_name_and_directory():
    assert is_test_file("tests/test_auth.py")
    assert is_test_file("pkg/auth_test.py")
    assert not is_test_file("pkg/auth.py")


def test_is_config_file_matches_name_and_suffix():
    assert is_config_file("pyproject.toml")
    assert is_config_file("setup.cfg")
    assert not is_config_file("pkg/auth.py")


def test_discovery_includes_tests_alongside_source(repo):
    files = discover_python_files(repo, ["pkg/"])
    assert "pkg/auth.py" in files
    assert "tests/test_auth.py" in files


def test_every_node_kind_is_produced(graph):
    kinds = {node.kind for node in graph.nodes.values()}
    for expected in ("repository", "package", "file", "class", "function", "method", "variable", "test", "configuration"):
        assert expected in kinds, f"missing node kind {expected}"


def test_contains_defines_and_inherits_edges(graph):
    auth_file = node_id("file", "pkg/auth.py")
    session = node_id("class", "pkg/auth.py", "Session")
    refresh = node_id("method", "pkg/auth.py", "Session.refresh")

    edges = {(e.source, e.target, e.kind) for e in graph.edges}
    assert (auth_file, session, "defines") in edges
    assert (session, refresh, "contains") in edges
    assert (node_id("class", "pkg/auth.py", "AdminSession"), session, "inherits") in edges


def test_imports_edge_resolves_to_a_repository_file(graph):
    edges = {(e.source, e.target) for e in graph.edges_of("imports")}
    assert (node_id("file", "pkg/auth.py"), node_id("file", "pkg/util.py")) in edges


def test_external_import_becomes_depends_on_not_imports(graph):
    external = [e for e in graph.edges_of("depends_on") if e.attributes.get("external")]
    assert any(e.attributes.get("module") == "os" for e in external)
    # `os` is not a repository file, so it must never produce an `imports` edge.
    assert all("os" not in e.target for e in graph.edges_of("imports"))


def test_test_file_importing_source_produces_a_tests_edge(graph):
    edges = {(e.source, e.target) for e in graph.edges_of("tests")}
    assert (node_id("file", "tests/test_auth.py"), node_id("file", "pkg/auth.py")) in edges


def test_variable_node_records_module_constant(graph):
    node = graph.nodes[node_id("variable", "pkg/auth.py", "MAX_AGE")]
    assert node.kind == "variable"
    assert node.file == "pkg/auth.py"


def test_function_node_carries_line_span(graph):
    node = graph.nodes[node_id("function", "pkg/auth.py", "login")]
    assert node.lineno > 0
    assert node.end_lineno >= node.lineno


def test_module_index_maps_dotted_names_and_packages():
    index = build_module_index(["pkg/__init__.py", "pkg/auth.py"])
    assert index["pkg.auth"] == "pkg/auth.py"
    assert index["pkg"] == "pkg/__init__.py"


def test_build_is_deterministic(repo):
    first, _ = build_repository_graph(repo, ["pkg/"])
    second, _ = build_repository_graph(repo, ["pkg/"])
    assert sorted(first.nodes) == sorted(second.nodes)
    assert [(e.source, e.target, e.kind) for e in first.edges] == [
        (e.source, e.target, e.kind) for e in second.edges
    ]


def test_unparseable_file_is_skipped_without_raising(repo):
    (repo / "pkg" / "broken.py").write_text("def (((\n")
    built, modules = build_repository_graph(repo, ["pkg/"])
    assert "pkg/broken.py" not in modules
    # The file node still exists; only its symbols are absent.
    assert node_id("file", "pkg/broken.py") in built.nodes
