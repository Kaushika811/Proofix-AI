"""A small, fully-specified repository used across the knowledge-graph tests.

Deliberately hand-built rather than generated: every edge these tests assert on
traces to a line of source written here, so a failure names a real relationship
rather than an accident of a fixture generator.

Shape:

    pkg/auth.py     Session (class) + login/validate (functions), imports util
    pkg/util.py     helper (called by auth), no dependents beyond auth
    pkg/api.py      @route-decorated endpoint, calls login
    pkg/orphan.py   imported by nobody, imports nobody
    tests/test_auth.py  calls validate  -> produces VALIDATES
    README.md       documents pkg/auth.py and login()
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from backend.models.repository_graph import (
    RepairMemory,
    RepairRecord,
    RepositoryIntelligence,
)
from backend.services.knowledge_graph import build_knowledge_graph
from backend.services.repository_indexer import index_repository

AUTH = '''"""Authentication module."""

from pkg.util import helper

MAX_AGE = 30


class Session:
    """A user session."""

    def refresh(self):
        return helper(MAX_AGE)


def validate(token):
    """Validate a token."""
    if not token:
        return False
    if len(token) > 10:
        return helper(token)
    return True


def login(user):
    """Log a user in."""
    return Session() if validate(user) else None
'''

UTIL = '''"""Shared helpers."""


def helper(value):
    return value
'''

API = '''from pkg.auth import login


def route(fn):
    return fn


@route
def login_endpoint(request):
    return login(request)
'''

ORPHAN = '''def unreferenced():
    return 1
'''

TEST_AUTH = '''from pkg.auth import validate


def test_validate_rejects_empty():
    assert not validate("")
'''

README = """# Fixture Repository

## Authentication

The `pkg/auth.py` module owns sessions. Call `login()` to begin one.
"""


def write_repo(root: Path) -> Path:
    """Materialise the fixture repository at `root`."""
    pkg = root / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "auth.py").write_text(AUTH)
    (pkg / "util.py").write_text(UTIL)
    (pkg / "api.py").write_text(API)
    (pkg / "orphan.py").write_text(ORPHAN)

    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_auth.py").write_text(TEST_AUTH)

    (root / "README.md").write_text(README)
    (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    return root


def build_index(root: Path, **kwargs) -> RepositoryIntelligence:
    return index_repository(root, ["pkg/"], **kwargs)


def with_history(index: RepositoryIntelligence, now: datetime | None = None) -> RepositoryIntelligence:
    """Attach synthetic commits, ownership and churn.

    The fixture repository has no git metadata, so history is injected rather
    than mined — which keeps these tests about the graph, not about git.
    """
    from backend.models.repository_graph import CommitRecord, FileEvolution
    from backend.services.ownership_graph import build_ownership_graph

    reference = now or datetime(2026, 8, 6)
    commits = [
        CommitRecord(
            sha="aaa111",
            author="Ada",
            committed_at=reference - timedelta(days=2),
            message_summary="fix: reject empty tokens",
            files=["pkg/auth.py", "pkg/util.py"],
            is_fix=True,
        ),
        CommitRecord(
            sha="bbb222",
            author="Ada",
            committed_at=reference - timedelta(days=5),
            message_summary="fix: session refresh",
            files=["pkg/auth.py"],
            is_fix=True,
        ),
        CommitRecord(
            sha="ccc333",
            author="Grace",
            committed_at=reference - timedelta(days=9),
            message_summary="feat: add endpoint",
            files=["pkg/api.py"],
            is_fix=False,
        ),
    ]

    index.history.commits = commits
    index.history.evolution = {
        "pkg/auth.py": FileEvolution(file="pkg/auth.py", commit_count=2, fix_commit_count=2, churn=1.0),
        "pkg/util.py": FileEvolution(file="pkg/util.py", commit_count=1, fix_commit_count=1, churn=0.5),
        "pkg/api.py": FileEvolution(file="pkg/api.py", commit_count=1, fix_commit_count=0, churn=0.0),
    }
    index.history.co_change = {
        "pkg/auth.py": {"pkg/util.py": 1},
        "pkg/util.py": {"pkg/auth.py": 1},
    }
    index.ownership = build_ownership_graph(index.history, now=reference)
    return index


def with_repairs(index: RepositoryIntelligence) -> RepositoryIntelligence:
    index.repair_memory = RepairMemory(
        repository_id="fixture",
        records=[
            RepairRecord(
                repair_id="run-1:pkg/auth.py:validate",
                repository_hash="old-hash",
                file="pkg/auth.py",
                file_hash="fh1",
                function="validate",
                function_hash="qh1",
                bug_type="value-validation",
                affected_files=["pkg/auth.py", "pkg/util.py"],
                validation_passed=True,
                mutation_score=0.5,
                security_score=100.0,
                retry_count=1,
                pr_type="draft",
            )
        ],
    )
    return index


def full_index(root: Path) -> RepositoryIntelligence:
    """Fixture repository with history, ownership and repair memory attached."""
    write_repo(root)
    return with_repairs(with_history(build_index(root)))


def full_graph(root: Path):
    return build_knowledge_graph(full_index(root))
