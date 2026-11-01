"""Environment precheck artifacts.

See `docs/ENVIRONMENT_PRECHECK_DESIGN.md`. These are published to the UI
verbatim — `reason` and `suggested_command` are rendered as written, not slotted
into a sentence the frontend composes, matching the convention established for
root-cause summaries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EnvironmentStatus = Literal[
    "ready",            # the test runner starts; reproduction can proceed
    "not_prepared",     # recognised project, dependencies not installed
    "no_test_runner",   # recognised project, no runner ProoFix can drive
    "unsupported",      # a language ProoFix cannot repair
    "no_manifest",      # nothing declaring dependencies at all
]


class DetectedManifest(BaseModel):
    """A dependency manifest found in the repository."""

    path: str
    kind: str
    language: str


class EnvironmentReport(BaseModel):
    status: EnvironmentStatus
    language: str | None = None
    manifests: list[DetectedManifest] = Field(default_factory=list)

    test_runner: str | None = None
    test_runner_available: bool = False

    #: Modules the repository imports that do not resolve. `pytest` first when
    #: it is among them, since it is the one that blocks reproduction outright.
    missing_imports: list[str] = Field(default_factory=list)

    #: The number `pytest --collect-only` actually reported, read from its own
    #: summary line. `None` when collection never ran (runner unavailable) —
    #: distinct from `0`, which means collection ran and found nothing.
    tests_collected: int | None = None

    #: User-facing explanation, in the backend's own words.
    reason: str = ""

    #: A command for a **human** to run. ProoFix never executes it — installing
    #: from a cloned repository runs that repository's build hooks on the host,
    #: and the pipeline's subprocesses are not sandboxed.
    suggested_command: str | None = None

    #: Whether reproduction and everything downstream should be skipped.
    blocking: bool = False
