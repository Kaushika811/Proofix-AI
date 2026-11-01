"""Can this repository's tests actually run here?

Deterministic, no LLM call, no network. See `docs/ENVIRONMENT_PRECHECK_DESIGN.md`
for the reasoning; the short version:

A manifest tells you what a project *declares*, not whether the environment can
*run* it. Every GitHub repository that fails today has a perfectly good
`pyproject.toml` — what it lacks is an installed `pytest`. So detection answers
"what kind of project is this", and the probe answers the operative question:
"will the test runner start".

**This module never installs anything.** `suggested_command` is a string for a
human to run. Installing dependencies from an arbitrary cloned repository
executes that repository's build hooks on the host, and the pipeline's
subprocesses are not yet sandboxed.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.models.environment import DetectedManifest, EnvironmentReport
from backend.services.subprocess_runner import PYTHON, run_command

# Manifest → (language, priority). Lower priority wins when several are present:
# `pyproject.toml` describes the project better than a bare `requirements.txt`.
_MANIFESTS: list[tuple[str, str, int]] = [
    ("pyproject.toml", "python", 0),
    ("setup.py", "python", 1),
    ("Pipfile", "python", 1),
    ("requirements.txt", "python", 2),
    ("requirements-dev.txt", "python", 3),
    ("uv.lock", "python", 3),
    ("poetry.lock", "python", 3),
    ("package.json", "node", 0),
    ("Cargo.toml", "rust", 0),
    ("go.mod", "go", 0),
    ("Gemfile", "ruby", 0),
    ("pom.xml", "jvm", 0),
    ("build.gradle", "jvm", 0),
    ("build.gradle.kts", "jvm", 0),
]

# The only language the pipeline can actually repair: every agent is `ast`-bound
# end to end (CLAUDE.md §8, P4).
_SUPPORTED_LANGUAGE = "python"

_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
    "build", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache",
}


def detect_manifests(repo: Path) -> list[DetectedManifest]:
    """Manifests at the repository root and one level down.

    One level down catches the common monorepo layout (`packages/*/pyproject.toml`)
    without walking a large tree.
    """
    found: list[DetectedManifest] = []

    def scan(directory: Path, prefix: str) -> None:
        for name, language, _priority in _MANIFESTS:
            candidate = directory / name
            if candidate.is_file():
                found.append(
                    DetectedManifest(
                        path=f"{prefix}{name}",
                        kind=name,
                        language=language,
                    )
                )

    scan(repo, "")
    try:
        for child in sorted(repo.iterdir()):
            if not child.is_dir() or child.name in _SKIP_DIRS or child.name.startswith("."):
                continue
            scan(child, f"{child.name}/")
    except OSError:
        # An unreadable repository root is a real condition, not a crash. The
        # caller reports what was found, which may be nothing.
        pass

    return found


def primary_language(manifests: list[DetectedManifest]) -> str | None:
    """The language of the highest-priority root manifest."""
    best: tuple[int, str] | None = None
    priority_of = {name: priority for name, _lang, priority in _MANIFESTS}

    for manifest in manifests:
        # Root manifests decide the project's language; a nested one does not.
        depth_penalty = 0 if "/" not in manifest.path else 10
        score = priority_of.get(manifest.kind, 9) + depth_penalty
        if best is None or score < best[0]:
            best = (score, manifest.language)

    return best[1] if best else None


def python_manifests(manifests: list[DetectedManifest]) -> list[DetectedManifest]:
    """The Python manifests, best first.

    `primary_language` answers "what kind of project does this look like", which
    is the right answer for a *label* and the wrong one for a *decision*. A
    polyglot repository with `frontend/package.json` and `backend/requirements.txt`
    and no root manifest reads as node under that rule — and was then rejected as
    unsupported, while A1 went on to index `backend/` and build a SIG from it.
    The pipeline could see the Python it had just declined to look at.

    So the operative question is not "is this a Python project" but "is there
    Python here to repair". Ranked root-first, then by manifest priority, so the
    suggested command names the manifest a human would actually install from.
    """
    priority_of = {name: priority for name, _lang, priority in _MANIFESTS}

    def rank(manifest: DetectedManifest) -> tuple[int, int, str]:
        return (
            0 if "/" not in manifest.path else 1,
            priority_of.get(manifest.kind, 9),
            manifest.path,
        )

    return sorted((m for m in manifests if m.language == _SUPPORTED_LANGUAGE), key=rank)


def _manifest_directory(manifest: DetectedManifest) -> str:
    """The directory holding a manifest, `""` for the repository root."""
    return manifest.path.rsplit("/", 1)[0] if "/" in manifest.path else ""


def _suggested_command(kinds: set[str], directory: str) -> str | None:
    """How a human prepares this checkout. ProoFix never runs it (module docstring)."""
    if "pyproject.toml" in kinds:
        return f'pip install -e "./{directory}[dev]"' if directory else 'pip install -e ".[dev]"'
    if "requirements.txt" in kinds:
        return f"pip install -r {directory}/requirements.txt" if directory else "pip install -r requirements.txt"
    if "Pipfile" in kinds:
        return f"(cd {directory} && pipenv install --dev)" if directory else "pipenv install --dev"
    if "setup.py" in kinds:
        return f"pip install -e ./{directory}" if directory else "pip install -e ."
    return None


#: SGR sequences. Python 3.14 colours tracebacks, and `reason` is rendered
#: verbatim by the UI — unstripped, the user reads `\x1b[1;35mModuleNotFoundError`.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


#: `ModuleNotFoundError: No module named 'x'` / `ImportError: cannot import name`.
_MISSING_MODULE_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s*No module named ['\"]([\w.]+)['\"]"
)


#: pytest's own collection summary: "3 tests collected in 0.01s",
#: "no tests collected in 0.00s", "3 tests collected, 1 error in 0.01s".
_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected")
_NO_TESTS_RE = re.compile(r"no tests collected")


def _tests_collected_from_output(text: str) -> int | None:
    """The count pytest itself reported, or `None` if it never said."""
    plain = _plain(text or "")
    match = _COLLECTED_RE.search(plain)
    if match:
        return int(match.group(1))
    if _NO_TESTS_RE.search(plain):
        return 0
    return None


def _missing_modules_from_output(text: str) -> list[str]:
    """Modules pytest reported it could not import, in first-seen order.

    Read from collection output rather than sampled from source: these are the
    imports that actually failed, for the actual test suite, on the actual
    interpreter. Only the top-level package is kept — telling a user that
    `foo.bar.baz` is missing when the fix is `pip install foo` is noise.
    """
    seen: list[str] = []
    for match in _MISSING_MODULE_RE.finditer(text or ""):
        root = match.group(1).split(".")[0]
        if root not in seen:
            seen.append(root)
    return seen


async def probe_environment(repo: Path, timeout: int = 30) -> EnvironmentReport:
    """Decide whether this repository's tests can run, and say why not."""
    manifests = detect_manifests(repo)
    language = primary_language(manifests)

    if not manifests:
        return EnvironmentReport(
            status="no_manifest",
            language=None,
            manifests=[],
            test_runner=None,
            test_runner_available=False,
            missing_imports=[],
            reason=(
                "No dependency manifest was found at the repository root or one "
                "level below it, so there is nothing describing how to prepare "
                "this project."
            ),
            suggested_command=None,
            blocking=True,
        )

    # Unsupported means "there is no Python here", not "the root manifest is not
    # Python". A repository whose Python lives in `backend/` is repairable.
    python = python_manifests(manifests)
    if not python:
        return EnvironmentReport(
            status="unsupported",
            language=language,
            manifests=manifests,
            test_runner=None,
            test_runner_available=False,
            missing_imports=[],
            reason=(
                f"This is a {language} project. ProoFix analyses and repairs "
                "Python only — every stage of the pipeline is built on Python's "
                "AST."
            ),
            suggested_command=None,
            blocking=True,
        )

    # Where the Python lives. The probe still runs at the repository root,
    # because that is where A3.5 runs pytest — a probe that answered for a
    # different directory than reproduction uses would answer the wrong
    # question. The directory only shapes the command a human is told to run.
    python_root = _manifest_directory(python[0])
    located = f" Python was found in `{python_root}/`." if python_root else ""

    # The operative check: will the runner start? `-c "import pytest"` is the
    # cheapest honest answer, and it uses the same interpreter A3.5 will use.
    code, _stdout, stderr = await run_command(
        [PYTHON, "-c", "import pytest; print(pytest.__version__)"],
        cwd=repo,
        timeout=timeout,
    )
    runner_available = code == 0

    # An importable pytest is necessary but not sufficient — a repository whose
    # own dependencies are missing fails at reproduction with a bare
    # ImportError. But *sampling* the repository's imports to decide that was
    # wrong, and wrong in the dangerous direction: it blocked Flask, a fully
    # prepared checkout whose suite collects 483 tests. The sample picked up
    #
    #   `_typeshed`  — exists only under `TYPE_CHECKING`, never importable
    #   `flaskr`, `task_app`, `js_example` — example apps inside the repo
    #   `asgiref`, `celery`, `docutils` — docs extras the suite never imports
    #
    # and none of them says anything about whether the tests can run.
    #
    # So ask the thing that will actually run them. `pytest --collect-only`
    # imports every test module and its transitive dependencies and reports
    # exactly the failures that would stop reproduction — no heuristics, no
    # guessing which imports matter.
    collect_code, collect_out, collect_err = (
        await run_command(
            [PYTHON, "-m", "pytest", "--collect-only", "-q", "--no-header", "-p", "no:cacheprovider"],
            cwd=repo,
            timeout=timeout,
        )
        if runner_available
        else (1, "", "")
    )

    # Exit 5 is "no tests collected" — the environment is fine, the suite is
    # empty. That is A3.5's `NO_TESTS`, not an environment fault, so it must not
    # block: blocking here would report a missing dependency that does not exist.
    collection_ok = collect_code in (0, 5)

    if runner_available and collection_ok:
        return EnvironmentReport(
            status="ready",
            language=_SUPPORTED_LANGUAGE,
            manifests=manifests,
            test_runner="pytest",
            test_runner_available=True,
            missing_imports=[],
            tests_collected=_tests_collected_from_output(f"{collect_out}\n{collect_err}"),
            reason=(
                "pytest collected the test suite without import errors. "
                "Collection is ready; runtime prerequisites and behavior will be checked during reproduction."
                f"{located}"
            ),
            suggested_command=None,
            blocking=False,
        )

    # Not runnable. Name the modules pytest itself could not import, so the
    # message points at the real problem rather than a sampled guess.
    missing: list[str] = [] if runner_available else ["pytest"]
    for module in _missing_modules_from_output(_plain(f"{collect_out}\n{collect_err}")):
        if module not in missing:
            missing.append(module)
        if len(missing) >= 8:
            break

    # Only Python manifests in the same directory as the best one: a command
    # built from `frontend/package.json` would install the wrong project.
    kinds = {m.kind for m in python if _manifest_directory(m) == python_root}
    suggested = _suggested_command(kinds, python_root)

    # Name what is actually missing. When pytest is present and the project's
    # own dependencies are not, "pytest is not importable" would be false.
    if not runner_available:
        # The *last* line of a Python traceback is the one that names the cause
        # ("ModuleNotFoundError: No module named 'pytest'"); the first is always
        # "Traceback (most recent call last):", which told the reader nothing.
        detail = [line for line in _plain(stderr or "").strip().splitlines() if line.strip()]
        first_line = detail[-1].strip()[:200] if detail else "pytest is not importable"
    elif missing:
        named = ", ".join(missing[:5])
        first_line = (
            f"pytest is available, but collecting the test suite failed to "
            f"import: {named}."
        )
    else:
        # Collection failed without naming a module — a `conftest.py` that
        # raises, a syntax error, a plugin that errors on load. Quote what
        # pytest said rather than asserting a cause it did not state.
        tail = _plain(collect_err or collect_out or "").strip().splitlines()
        quoted = tail[-1][:200] if tail else f"pytest collection exited {collect_code}"
        first_line = f"pytest could not collect the test suite: {quoted}"

    return EnvironmentReport(
        status="not_prepared",
        language=_SUPPORTED_LANGUAGE,
        manifests=manifests,
        test_runner="pytest",
        # Reported as found when it is: this branch is now also reached with a
        # working pytest and missing project dependencies, and claiming pytest
        # was absent there would send the reader after the wrong problem.
        test_runner_available=runner_available,
        missing_imports=missing,
        reason=(
            "This repository's dependencies are not installed in the interpreter "
            f"ProoFix would run its tests with, so no failure can be reproduced. {first_line}"
        ),
        suggested_command=suggested,
        blocking=True,
    )
