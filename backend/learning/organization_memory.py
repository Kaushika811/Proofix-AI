"""Aggregate preferences across every repository an organisation owns.

A single repository teaches you what *it* does. Several repositories agreeing
teach you what the *organisation* does, and that is the level a new or small
repository should inherit conventions from — a two-file service has no style of
its own worth following, but the twelve services around it do.

Aggregation is by majority across repositories, **not** across files. Counting
files would let one large monorepo package outvote every other service; counting
repositories asks the question that actually matters: how many of our codebases
do it this way?

A convention needs `MIN_REPOSITORIES` agreeing before it is asserted. Below that
it stays `unknown`, because two repositories agreeing is a coincidence as often
as a convention.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from backend.models.learning import (
    OrganizationProfile,
    RepairKnowledge,
    RepositoryProfile,
)

# Repositories that must agree before a convention is asserted organisation-wide.
MIN_REPOSITORIES = 2

# Share of repositories that must agree.
AGREEMENT_THRESHOLD = 0.6

# Libraries that say nothing about preference because everyone has them.
UBIQUITOUS = frozenset({
    "os", "sys", "re", "json", "typing", "pathlib", "datetime", "collections",
    "dataclasses", "abc", "enum", "functools", "itertools", "logging", "time",
    "math", "hashlib", "asyncio", "contextlib", "uuid", "copy", "io", "traceback",
    "subprocess", "tempfile", "shutil", "warnings", "inspect", "textwrap",
})

# Directory names that indicate an architectural style.
ARCHITECTURE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("layered", ("services", "repositories", "controllers", "models")),
    ("hexagonal", ("adapters", "ports", "domain")),
    ("modular-monolith", ("modules", "packages")),
    ("microservice", ("services", "cmd", "internal")),
    ("mvc", ("views", "controllers", "models")),
    ("agent-pipeline", ("agents", "orchestrator")),
)


@dataclass
class OrganizationMemory:
    """Cross-repository preferences, rebuilt from repository profiles."""

    organization_id: str = "default"
    profiles: dict[str, RepositoryProfile] = field(default_factory=dict)

    def register(self, profile: RepositoryProfile) -> None:
        """Add or replace one repository's profile."""
        self.profiles[profile.repository_id] = profile

    # -- aggregation -----------------------------------------------------

    def _majority(self, values: list[str]) -> str:
        """Dominant value across repositories, or "unknown" without agreement."""
        usable = [v for v in values if v and v not in ("unknown", "mixed", "none")]
        if len(usable) < MIN_REPOSITORIES:
            return "unknown"
        value, count = Counter(usable).most_common(1)[0]
        return value if count / len(usable) >= AGREEMENT_THRESHOLD else "unknown"

    def build(
        self,
        records: list[RepairKnowledge] | None = None,
        file_paths: dict[str, tuple[str, ...]] | None = None,
    ) -> OrganizationProfile:
        """Resolve every registered profile into one organisation profile."""
        profiles = list(self.profiles.values())
        records = records or []

        naming = {
            "function": self._majority([p.style.function_naming for p in profiles]),
            "class": self._majority([p.style.class_naming for p in profiles]),
            "constant": self._majority([p.style.constant_naming for p in profiles]),
        }

        testing = {
            "docstrings": self._majority(
                ["required" if p.style.docstring_coverage >= 0.5 else "optional" for p in profiles]
            ),
            "type_hints": self._majority(
                ["required" if p.style.type_hint_coverage >= 0.5 else "optional" for p in profiles]
            ),
        }

        frameworks = Counter(
            p.framework.primary_framework for p in profiles
            if p.framework.primary_framework != "unknown"
        )

        profile = OrganizationProfile(
            organization_id=self.organization_id,
            repositories=sorted(self.profiles),
            naming_conventions={k: v for k, v in naming.items() if v != "unknown"},
            testing_conventions={k: v for k, v in testing.items() if v != "unknown"},
            logging_style=self._majority([p.style.logging_style for p in profiles]),
            error_handling_style=self._majority([p.style.exception_style for p in profiles]),
            frameworks=dict(sorted(frameworks.items(), key=lambda kv: (-kv[1], kv[0]))),
            total_repairs=sum(p.repairs_recorded for p in profiles),
            total_reviews=sum(p.repairs_reviewed for p in profiles),
            updated_at=datetime.utcnow(),
        )

        profile.architecture_style = self._infer_architecture(file_paths or {})
        profile.dependency_injection_style = self._infer_injection(profiles)
        profile.authentication_style = self._infer_authentication(frameworks)
        profile.validation_style = self._infer_validation(frameworks)
        profile.folder_conventions = self._folder_conventions(file_paths or {})
        return profile

    # -- inference from counted evidence ---------------------------------

    def _infer_architecture(self, file_paths: dict[str, tuple[str, ...]]) -> str:
        """Architectural style from directory vocabulary across repositories."""
        if not file_paths:
            return "unknown"

        votes: Counter = Counter()
        for paths in file_paths.values():
            directories = {segment for path in paths for segment in path.split("/")[:-1]}
            for style, markers in ARCHITECTURE_MARKERS:
                matched = sum(1 for marker in markers if marker in directories)
                # A majority of a style's markers must be present. One shared
                # directory name is not an architecture.
                if matched >= max(2, len(markers) // 2):
                    votes[style] += 1

        if not votes:
            return "unknown"
        style, count = votes.most_common(1)[0]
        return style if count >= min(MIN_REPOSITORIES, len(file_paths)) else "unknown"

    def _infer_injection(self, profiles: list[RepositoryProfile]) -> str:
        """Dependency-injection style, from the framework that implies it."""
        conventions = [
            c.convention
            for p in profiles
            for c in p.framework.conventions
            if c.aspect == "dependency_injection"
        ]
        return self._majority(conventions)

    def _infer_authentication(self, frameworks: Counter) -> str:
        if not frameworks:
            return "unknown"
        primary = frameworks.most_common(1)[0][0]
        return {
            "FastAPI": "security dependencies",
            "Django": "django.contrib.auth",
            "Flask": "flask-login or explicit middleware",
            "Spring": "Spring Security",
            "NestJS": "guards",
            "Laravel": "Laravel guards",
            "ASP.NET": "ASP.NET Identity",
        }.get(primary, "unknown")

    def _infer_validation(self, frameworks: Counter) -> str:
        if not frameworks:
            return "unknown"
        primary = frameworks.most_common(1)[0][0]
        return {
            "FastAPI": "pydantic models",
            "Django": "forms or DRF serializers",
            "Flask": "marshmallow or explicit parsing",
            "NestJS": "class-validator DTOs",
            "Spring": "bean validation annotations",
            "Laravel": "form request classes",
        }.get(primary, "unknown")

    def _folder_conventions(self, file_paths: dict[str, tuple[str, ...]]) -> dict[str, int]:
        """How many repositories use each top-level directory name."""
        counts: Counter = Counter()
        for paths in file_paths.values():
            tops = {path.split("/")[0] for path in paths if "/" in path}
            counts.update(tops)
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:20])

    # -- library preference ----------------------------------------------

    def learn_libraries(self, imports_by_repository: dict[str, set[str]]) -> dict[str, int]:
        """Third-party libraries counted by how many repositories use each.

        Standard-library modules are excluded: every repository imports `os`, so
        counting it would put it at the top of a list meant to express choice.
        """
        counts: Counter = Counter()
        for imports in imports_by_repository.values():
            counts.update(
                module for module in imports
                if module and module not in UBIQUITOUS and not module.startswith("_")
            )
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    # -- reporting -------------------------------------------------------

    def summary(self, profile: OrganizationProfile | None = None) -> dict:
        profile = profile or self.build()
        return {
            "organization_id": profile.organization_id,
            "repositories": len(profile.repositories),
            "maturity": profile.maturity,
            "architecture_style": profile.architecture_style,
            "naming_conventions": profile.naming_conventions,
            "testing_conventions": profile.testing_conventions,
            "logging_style": profile.logging_style,
            "error_handling_style": profile.error_handling_style,
            "authentication_style": profile.authentication_style,
            "validation_style": profile.validation_style,
            "frameworks": profile.frameworks,
            "preferred_libraries": dict(list(profile.preferred_libraries.items())[:10]),
            "total_repairs": profile.total_repairs,
            "total_reviews": profile.total_reviews,
        }
