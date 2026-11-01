"""Detect what kind of repository this actually is, before indexing it.

A single-package library, a monorepo of twenty services, and a repository with a
vendored checkout inside it all need different treatment, and getting it wrong is
not a cosmetic problem:

* A **nested repository** — a directory with its own `.git` — has its own
  history, its own owners and its own churn. Indexing it as part of the parent
  attributes another project's commits to this one, which corrupts ownership and
  every risk score derived from it. Nested repositories are therefore detected
  and excluded, and reported so the exclusion is visible rather than silent.

* A **monorepo** has several independent packages that happen to share a
  checkout. Their source roots are separate, and a file in one is not a
  dependency of a file in another merely because they are adjacent.

* **Other languages** are represented as file nodes without symbols. The AST
  layer is Python-only and honestly says so, but a repository that is 60% Go
  should not report 40% coverage as though the rest did not exist — the files
  are real, they are owned, they have history, and the graph can carry all of
  that without pretending to parse them.

Detection is filesystem-only: manifests, directory markers, and file suffixes.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

# Manifests that mark a directory as an independently packaged unit.
PACKAGE_MANIFESTS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
)

# Directories a monorepo conventionally puts its packages under.
WORKSPACE_CONTAINERS = ("packages", "services", "apps", "libs", "modules", "projects", "components")

# Source suffixes worth representing as nodes even though only Python is parsed.
LANGUAGE_SUFFIXES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".sql": "sql",
}

SKIP_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", "vendor", ".tox", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "site-packages", "dist-packages", ".next", ".nuxt", "coverage",
    "mutants",
})

# Depth limit for package discovery. Packages live near the top; scanning deeper
# finds test fixtures and vendored copies, not real packages.
MAX_PACKAGE_DEPTH = 3


class PackageInfo(BaseModel):
    """One independently packaged unit inside the repository."""

    path: str
    name: str
    manifest: str = ""
    language: str = "python"
    is_root: bool = False


class WorkspaceLayout(BaseModel):
    """What the repository is, structurally."""

    kind: str = "single_package"  # single_package | monorepo | nested_parent
    packages: list[PackageInfo] = Field(default_factory=list)
    nested_repositories: list[str] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)
    foreign_files: list[str] = Field(default_factory=list)

    @property
    def is_monorepo(self) -> bool:
        return self.kind == "monorepo"

    @property
    def primary_language(self) -> str:
        if not self.languages:
            return "python"
        return max(self.languages.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def excluded_directories(self) -> frozenset[str]:
        return frozenset(self.nested_repositories)


def _should_skip(name: str) -> bool:
    return name in SKIP_DIRECTORIES or (name.startswith(".") and name != ".")


def detect_nested_repositories(repo_path: Path, max_depth: int = MAX_PACKAGE_DEPTH) -> list[str]:
    """Directories below the root that are themselves git repositories."""
    repo = repo_path.resolve()
    if not repo.is_dir():
        return []

    found: list[str] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or _should_skip(child.name):
                continue
            if (child / ".git").exists():
                found.append(str(child.relative_to(repo)).replace("\\", "/"))
                continue  # its own contents belong to that repository, not this one
            walk(child, depth + 1)

    walk(repo, 1)
    return found


def detect_packages(repo_path: Path, exclude: frozenset[str] | None = None) -> list[PackageInfo]:
    """Independently packaged units, root first."""
    repo = repo_path.resolve()
    if not repo.is_dir():
        return []

    excluded = exclude or frozenset()
    packages: list[PackageInfo] = []

    def manifest_in(directory: Path) -> str:
        for manifest in PACKAGE_MANIFESTS:
            if (directory / manifest).is_file():
                return manifest
        return ""

    root_manifest = manifest_in(repo)
    if root_manifest:
        packages.append(
            PackageInfo(
                path="",
                name=repo.name,
                manifest=root_manifest,
                language=_manifest_language(root_manifest),
                is_root=True,
            )
        )

    def walk(directory: Path, depth: int) -> None:
        if depth > MAX_PACKAGE_DEPTH:
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir() or _should_skip(child.name):
                continue
            rel = str(child.relative_to(repo)).replace("\\", "/")
            if rel in excluded:
                continue
            manifest = manifest_in(child)
            if manifest:
                packages.append(
                    PackageInfo(
                        path=rel,
                        name=child.name,
                        manifest=manifest,
                        language=_manifest_language(manifest),
                    )
                )
            walk(child, depth + 1)

    walk(repo, 1)
    return packages


def _manifest_language(manifest: str) -> str:
    return {
        "package.json": "javascript",
        "go.mod": "go",
        "Cargo.toml": "rust",
        "pom.xml": "java",
        "build.gradle": "java",
        "Gemfile": "ruby",
        "composer.json": "php",
    }.get(manifest, "python")


def detect_languages(
    repo_path: Path,
    exclude: frozenset[str] | None = None,
    max_files: int = 20_000,
) -> tuple[dict[str, int], list[str]]:
    """File counts per language, plus the non-Python source paths.

    Foreign files are returned so the indexer can represent them as nodes: they
    are owned, they have history, and omitting them would understate coverage.
    """
    repo = repo_path.resolve()
    if not repo.is_dir():
        return {}, []

    excluded = exclude or frozenset()
    counts: dict[str, int] = {}
    foreign: list[str] = []
    scanned = 0

    def walk(directory: Path) -> None:
        nonlocal scanned
        if scanned >= max_files:
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if scanned >= max_files:
                return
            if child.is_dir():
                if _should_skip(child.name):
                    continue
                rel = str(child.relative_to(repo)).replace("\\", "/")
                if rel in excluded:
                    continue
                walk(child)
                continue

            language = LANGUAGE_SUFFIXES.get(child.suffix.lower())
            if language is None:
                continue
            scanned += 1
            counts[language] = counts.get(language, 0) + 1
            if language != "python":
                foreign.append(str(child.relative_to(repo)).replace("\\", "/"))

    walk(repo)
    return dict(sorted(counts.items())), sorted(foreign)


def detect_workspace(repo_path: Path) -> WorkspaceLayout:
    """Full layout detection. Cheap: directory listings, no file reads."""
    repo = repo_path.resolve()

    nested = detect_nested_repositories(repo)
    excluded = frozenset(nested)
    packages = detect_packages(repo, excluded)
    languages, foreign = detect_languages(repo, excluded)

    non_root = [p for p in packages if not p.is_root]
    in_container = [
        p for p in non_root
        if PurePosixPath(p.path).parts and PurePosixPath(p.path).parts[0] in WORKSPACE_CONTAINERS
    ]

    # Two or more sibling packages, or any package under a conventional
    # workspace container, means the packages are the unit of work — not the
    # repository as a whole.
    if len(non_root) >= 2 or in_container:
        kind = "monorepo"
    elif nested:
        kind = "nested_parent"
    else:
        kind = "single_package"

    return WorkspaceLayout(
        kind=kind,
        packages=packages,
        nested_repositories=nested,
        languages=languages,
        foreign_files=foreign,
    )


def source_roots_for_workspace(layout: WorkspaceLayout, discovered: list[str]) -> list[str]:
    """Merge discovered Python roots with monorepo package paths.

    Discovery runs from the repository root and can miss a package nested two
    directories down. Adding package paths as roots makes each unit visible
    without changing what discovery already found.
    """
    roots = list(discovered)
    if not layout.is_monorepo:
        return roots

    for package in layout.packages:
        if package.is_root or package.language != "python":
            continue
        candidate = package.path.rstrip("/") + "/"
        if candidate not in roots:
            roots.append(candidate)
    return roots
