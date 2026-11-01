"""Literal re-execution commands for proof bundles (no shell interpolation)."""

from __future__ import annotations

import shlex

FULL_SUITE_COMMAND = "python -m pytest -v --tb=long"
FULL_SUITE_TIMEOUT = 120


def build_targeted_reproduction_command(failing_test: str) -> str:
    """Return a copy-pasteable pytest command for a single test nodeid."""
    return f"python -m pytest {shlex.quote(failing_test)} -v --tb=long"


def build_reproduction_command(failing_test: str | None) -> tuple[str, bool, int]:
    """
    Returns (command, is_targeted, timeout_seconds).
    is_targeted=False means full-suite fallback (lower-confidence proof).
    """
    if failing_test:
        return build_targeted_reproduction_command(failing_test), True, FULL_SUITE_TIMEOUT
    return FULL_SUITE_COMMAND, False, FULL_SUITE_TIMEOUT
