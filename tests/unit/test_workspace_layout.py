"""Enterprise layouts: monorepos, nested repositories, packages, languages."""

import pytest
from git import Repo

from backend.services.repository_indexer import index_repository
from backend.services.workspace_layout import (
    PACKAGE_MANIFESTS,
    WorkspaceLayout,
    detect_languages,
    detect_nested_repositories,
    detect_packages,
    detect_workspace,
    source_roots_for_workspace,
)


def make_package(root, path: str, manifest: str = "pyproject.toml", module: str = "mod.py"):
    directory = root / path if path else root
    directory.mkdir(parents=True, exist_ok=True)
    (directory / manifest).write_text("[project]\nname='x'\n")
    (directory / module).write_text("def go():\n    return 1\n")
    return directory


# -- nested repositories ---------------------------------------------------


def test_nested_git_repository_is_detected(tmp_path):
    (tmp_path / "embedded").mkdir()
    Repo.init(tmp_path / "embedded")
    assert detect_nested_repositories(tmp_path) == ["embedded"]


def test_conventionally_skipped_directory_is_not_reported_as_nested(tmp_path):
    """`vendor/` is excluded from indexing by name, before any .git check."""
    (tmp_path / "vendor").mkdir()
    Repo.init(tmp_path / "vendor")
    assert detect_nested_repositories(tmp_path) == []


def test_repository_without_nesting_reports_none(tmp_path):
    make_package(tmp_path, "")
    assert detect_nested_repositories(tmp_path) == []


def test_nested_repository_contents_are_not_descended_into(tmp_path):
    outer = tmp_path / "embedded"
    outer.mkdir()
    Repo.init(outer)
    inner = outer / "deeper"
    inner.mkdir()
    Repo.init(inner)
    assert detect_nested_repositories(tmp_path) == ["embedded"]


def test_nested_repository_files_are_excluded_from_the_index(tmp_path):
    make_package(tmp_path, "pkg", module="__init__.py")
    (tmp_path / "pkg" / "real.py").write_text("def real():\n    return 1\n")

    embedded = tmp_path / "embedded"
    embedded.mkdir()
    Repo.init(embedded)
    (embedded / "foreign.py").write_text("def foreign():\n    return 1\n")

    index = index_repository(tmp_path, ["pkg/"])
    assert "pkg/real.py" in index.repository_graph.files
    assert not any(f.startswith("embedded/") for f in index.repository_graph.files)


def test_nested_repository_is_reported_on_the_index(tmp_path):
    make_package(tmp_path, "pkg", module="__init__.py")
    Repo.init((tmp_path / "embedded").resolve(), mkdir=True)
    index = index_repository(tmp_path, ["pkg/"])
    assert "embedded" in index.workspace.nested_repositories


def test_excluded_directories_helper():
    layout = WorkspaceLayout(nested_repositories=["a", "b"])
    assert layout.excluded_directories() == frozenset({"a", "b"})


# -- packages --------------------------------------------------------------


def test_root_package_is_detected_and_flagged(tmp_path):
    make_package(tmp_path, "")
    packages = detect_packages(tmp_path)
    assert packages[0].is_root
    assert packages[0].path == ""


def test_sub_packages_are_detected(tmp_path):
    make_package(tmp_path, "packages/alpha")
    make_package(tmp_path, "packages/beta")
    paths = {p.path for p in detect_packages(tmp_path)}
    assert {"packages/alpha", "packages/beta"} <= paths


def test_package_manifest_is_recorded(tmp_path):
    make_package(tmp_path, "svc", manifest="package.json")
    package = next(p for p in detect_packages(tmp_path) if p.path == "svc")
    assert package.manifest == "package.json"


@pytest.mark.parametrize(
    "manifest,language",
    [("package.json", "javascript"), ("go.mod", "go"), ("Cargo.toml", "rust"), ("pyproject.toml", "python")],
)
def test_manifest_implies_language(tmp_path, manifest, language):
    make_package(tmp_path, "svc", manifest=manifest)
    package = next(p for p in detect_packages(tmp_path) if p.path == "svc")
    assert package.language == language


def test_all_known_manifests_are_recognised(tmp_path):
    for position, manifest in enumerate(PACKAGE_MANIFESTS):
        make_package(tmp_path, f"p{position}", manifest=manifest)
    assert len(detect_packages(tmp_path)) == len(PACKAGE_MANIFESTS)


def test_excluded_directory_yields_no_package(tmp_path):
    make_package(tmp_path, "vendor")
    assert detect_packages(tmp_path, exclude=frozenset({"vendor"})) == []


def test_build_directories_are_skipped(tmp_path):
    make_package(tmp_path, "node_modules/thing", manifest="package.json")
    assert detect_packages(tmp_path) == []


# -- workspace kind --------------------------------------------------------


def test_single_package_repository(tmp_path):
    make_package(tmp_path, "")
    assert detect_workspace(tmp_path).kind == "single_package"


def test_two_sibling_packages_make_a_monorepo(tmp_path):
    make_package(tmp_path, "alpha")
    make_package(tmp_path, "beta")
    layout = detect_workspace(tmp_path)
    assert layout.kind == "monorepo"
    assert layout.is_monorepo


def test_one_package_under_a_workspace_container_makes_a_monorepo(tmp_path):
    make_package(tmp_path, "services/api")
    assert detect_workspace(tmp_path).kind == "monorepo"


def test_nested_parent_kind(tmp_path):
    make_package(tmp_path, "")
    Repo.init((tmp_path / "embedded").resolve(), mkdir=True)
    assert detect_workspace(tmp_path).kind == "nested_parent"


def test_empty_directory_is_a_single_package(tmp_path):
    assert detect_workspace(tmp_path).kind == "single_package"


def test_missing_directory_degrades_without_raising(tmp_path):
    layout = detect_workspace(tmp_path / "absent")
    assert layout.packages == []
    assert layout.nested_repositories == []


# -- languages -------------------------------------------------------------


def test_language_counts(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.go").write_text("package main\n")
    (tmp_path / "c.ts").write_text("export const x = 1;\n")
    counts, foreign = detect_languages(tmp_path)
    assert counts == {"go": 1, "python": 1, "typescript": 1}
    assert set(foreign) == {"b.go", "c.ts"}


def test_python_files_are_not_listed_as_foreign(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    _counts, foreign = detect_languages(tmp_path)
    assert foreign == []


def test_primary_language_is_the_most_common(tmp_path):
    for i in range(3):
        (tmp_path / f"f{i}.go").write_text("package main\n")
    (tmp_path / "one.py").write_text("x = 1\n")
    assert detect_workspace(tmp_path).primary_language == "go"


def test_primary_language_defaults_to_python():
    assert WorkspaceLayout().primary_language == "python"


def test_unknown_suffixes_are_ignored(tmp_path):
    (tmp_path / "notes.xyz").write_text("hello\n")
    counts, _foreign = detect_languages(tmp_path)
    assert counts == {}


def test_excluded_directories_are_not_scanned_for_languages(tmp_path):
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "x.go").write_text("package main\n")
    counts, _foreign = detect_languages(tmp_path, exclude=frozenset({"vendor"}))
    assert counts == {}


# -- source roots ----------------------------------------------------------


def test_monorepo_package_paths_become_source_roots(tmp_path):
    make_package(tmp_path, "services/api")
    make_package(tmp_path, "services/worker")
    layout = detect_workspace(tmp_path)
    roots = source_roots_for_workspace(layout, ["existing/"])

    assert "existing/" in roots
    assert "services/api/" in roots
    assert "services/worker/" in roots


def test_single_package_roots_are_untouched(tmp_path):
    make_package(tmp_path, "")
    layout = detect_workspace(tmp_path)
    assert source_roots_for_workspace(layout, ["pkg/"]) == ["pkg/"]


def test_non_python_packages_are_not_added_as_roots(tmp_path):
    make_package(tmp_path, "services/api")
    make_package(tmp_path, "services/web", manifest="package.json")
    layout = detect_workspace(tmp_path)
    roots = source_roots_for_workspace(layout, [])
    assert "services/api/" in roots
    assert "services/web/" not in roots


def test_roots_are_not_duplicated(tmp_path):
    make_package(tmp_path, "services/api")
    make_package(tmp_path, "services/worker")
    layout = detect_workspace(tmp_path)
    roots = source_roots_for_workspace(layout, ["services/api/"])
    assert roots.count("services/api/") == 1


def test_monorepo_packages_are_all_indexed(tmp_path):
    make_package(tmp_path, "services/api", module="handler.py")
    make_package(tmp_path, "services/worker", module="task.py")
    index = index_repository(tmp_path, [])

    files = set(index.repository_graph.files)
    assert "services/api/handler.py" in files
    assert "services/worker/task.py" in files
    assert index.workspace.kind == "monorepo"
