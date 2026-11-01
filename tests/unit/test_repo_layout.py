import tempfile
from pathlib import Path

import pytest

from backend.services.repo_layout import (
    bandit_exclude_arg,
    discover_source_roots,
    is_production_file,
    is_vendor_path,
    resolve_scan_paths,
    semgrep_exclude_args,
)


@pytest.fixture
def repo_tmp():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def test_discover_src_layout(repo_tmp):
    (repo_tmp / "src" / "myapp").mkdir(parents=True)
    (repo_tmp / "src" / "myapp" / "__init__.py").write_text("")
    (repo_tmp / "src" / "myapp" / "main.py").write_text("print('hi')")
    roots = discover_source_roots(repo_tmp)
    assert "src/myapp/" in roots


def test_discover_app_layout(repo_tmp):
    (repo_tmp / "app").mkdir()
    (repo_tmp / "app" / "__init__.py").write_text("")
    (repo_tmp / "app" / "routes.py").write_text("")
    roots = discover_source_roots(repo_tmp)
    assert "app/" in roots


def test_discover_backend_layout(repo_tmp):
    (repo_tmp / "backend").mkdir()
    (repo_tmp / "backend" / "service.py").write_text("")
    roots = discover_source_roots(repo_tmp)
    assert "backend/" in roots


def test_discover_top_level_package(repo_tmp):
    (repo_tmp / "myproject").mkdir()
    (repo_tmp / "myproject" / "__init__.py").write_text("")
    (repo_tmp / "myproject" / "core.py").write_text("")
    roots = discover_source_roots(repo_tmp)
    assert "myproject/" in roots


def test_discover_flat_layout(repo_tmp):
    (repo_tmp / "main.py").write_text("")
    roots = discover_source_roots(repo_tmp)
    assert "" in roots


def test_discover_tests_only_fallback(repo_tmp):
    (repo_tmp / "tests").mkdir()
    (repo_tmp / "tests" / "test_x.py").write_text("")
    roots = discover_source_roots(repo_tmp)
    assert roots == [""]


def test_is_production_file_excludes_tests():
    assert is_production_file("tests/test_x.py", ["src/myapp/"]) is False
    assert is_production_file("src/myapp/main.py", ["src/myapp/"]) is True


def test_resolve_scan_paths(repo_tmp):
    (repo_tmp / "src" / "pkg").mkdir(parents=True)
    paths = resolve_scan_paths(repo_tmp, ["src/pkg/"])
    assert len(paths) == 1
    assert paths[0].name == "pkg"


# -- is_vendor_path / scanner exclude args --------------------------------


def test_is_vendor_path_flags_installed_dependency_source():
    assert is_vendor_path("vuln-demo/.venv/Lib/site-packages/httpx/_auth.py") is True
    assert is_vendor_path("frontend/node_modules/lodash/lodash.js") is True
    assert is_vendor_path("some/dist-packages/pkg.py") is True
    assert is_vendor_path(".git/hooks/pre-commit") is True
    assert is_vendor_path("build/output.py") is True


def test_is_vendor_path_does_not_flag_a_repositorys_own_tests_or_docs():
    """`is_vendor_path` is narrower than `is_excluded_path` on purpose — a
    test file is the repository's own code, not a dependency, even though
    source-root discovery (`is_excluded_path`) treats it as non-production."""
    assert is_vendor_path("tests/test_auth.py") is False
    assert is_vendor_path("docs/README.md") is False
    assert is_vendor_path("app/auth.py") is False


def test_bandit_exclude_arg_covers_venv_and_site_packages():
    arg = bandit_exclude_arg()
    assert "*/.venv/*" in arg
    assert "*/site-packages/*" in arg
    assert "*/node_modules/*" in arg
    assert "mutants/*" in arg
    assert "*/mutants/*" in arg


def test_semgrep_exclude_args_one_flag_per_directory():
    args = semgrep_exclude_args()
    assert "--exclude=.venv" in args
    assert "--exclude=node_modules" in args
    assert "--exclude=site-packages" in args
    assert "--exclude=mutants" in args
