"""Repository path normalization, candidate generation, and matching.

Single source of truth for "is this the same file?". Before this module the same
question was answered by three independent implementations — `target_resolver`
(SIG-key matching), `citation_verifier` (citation anchoring), and `mci_verifier`
(phantom detection) — each with its own notion of case sensitivity, prefix
stripping, and basename fallback.

Vocabulary used throughout:

``token``
    A casefolded POSIX string used only for *comparison*. Never returned as a
    path a caller should open.
``candidate``
    A case-preserving repo-relative path string that may or may not exist.
``key``
    A path that already exists in some index — a SIG file key, or a path that
    exists on disk relative to the repository root.

Matching is case-insensitive. Every match helper requires the match to be
*unique*: an ambiguous candidate resolves to ``None`` rather than to an
arbitrary winner, so a wrong file is never silently patched or cited.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

# Prefixes git diff headers put in front of otherwise repo-relative paths.
_DIFF_PREFIXES = ("a/", "b/")

# Quote characters that wrap paths in LLM prose and markdown.
_QUOTE_CHARS = "`\"'"

# Upper bound on generated suffix candidates. Repository paths are shallow in
# practice; the cap only guards against pathological input.
_MAX_SUFFIX_CANDIDATES = 12


def to_posix(raw_path: str) -> str:
    """Normalize separators and strip decoration, preserving case.

    Handles Windows separators, `./` prefixes, git diff `a/` and `b/` prefixes,
    surrounding quotes/backticks, and leading slashes. The result is suitable
    for joining to a repository root.
    """
    if raw_path is None:
        return ""

    text = str(raw_path).strip().strip(_QUOTE_CHARS).strip()
    if not text:
        return ""

    text = text.replace("\\", "/")

    for prefix in _DIFF_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break

    while text.startswith("./"):
        text = text[2:]

    return text.lstrip("/")


def normalize_path_token(raw_path: str) -> str:
    """Return a casefolded POSIX token for path comparison.

    Drops `.` segments and collapses separators so that ``a/vulnapi/Auth.py``,
    ``b\\vulnapi\\auth.py`` and ``./vulnapi/./auth.py`` all compare equal.
    Returns ``""`` for input that contains no path segments.
    """
    text = to_posix(raw_path)
    if not text:
        return ""

    parts = [part for part in text.split("/") if part and part != "."]
    if not parts:
        return ""

    return PurePosixPath(*parts).as_posix().casefold()


def basename(raw_path: str) -> str:
    """Final path segment, case preserved. Empty string when there is none."""
    text = to_posix(raw_path)
    return PurePosixPath(text).name if text else ""


def is_absolute(raw_path: str) -> bool:
    """True for POSIX absolute paths and Windows drive/UNC paths."""
    text = str(raw_path or "").strip().strip(_QUOTE_CHARS).strip()
    if not text:
        return False
    if text.startswith("/") or text.startswith("\\\\"):
        return True
    # Windows drive letter, e.g. C:\src\app.py or C:/src/app.py
    return len(text) >= 3 and text[1] == ":" and text[2] in ("\\", "/")


def path_candidates(raw_path: str) -> list[str]:
    """Ordered repo-relative candidates for a possibly-absolute path.

    Yields the full normalized path followed by progressively shorter suffixes,
    most specific first, ending at the bare basename. This replaces the previous
    hardcoded container-name whitelist (which contained the fixture repository's
    own directory name) with a repository-agnostic rule: any suffix of the path
    is a candidate, and uniqueness decides the winner.
    """
    text = to_posix(raw_path)
    if not text:
        return []

    parts = [part for part in text.split("/") if part and part != "."]
    if not parts:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    for start in range(len(parts)):
        candidate = PurePosixPath(*parts[start:]).as_posix()
        token = normalize_path_token(candidate)
        if not token or token in seen:
            continue
        seen.add(token)
        candidates.append(candidate)
        if len(candidates) >= _MAX_SUFFIX_CANDIDATES:
            break

    return candidates


def match_key(candidate: str, keys: Iterable[str]) -> str | None:
    """Match a candidate against known keys: exact, then unique suffix, then unique basename.

    Comparison is case-insensitive. Ambiguity yields ``None``.
    """
    token = normalize_path_token(candidate)
    if not token:
        return None

    key_list = list(keys)
    if not key_list:
        return None

    folded = [(key, normalize_path_token(key)) for key in key_list]

    for key, key_token in folded:
        if key_token == token:
            return key

    suffix_matches = [key for key, key_token in folded if key_token.endswith(f"/{token}")]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    base = PurePosixPath(token).name
    base_matches = [
        key
        for key, key_token in folded
        if key_token == base or key_token.endswith(f"/{base}")
    ]
    if len(base_matches) == 1:
        return base_matches[0]

    return None


def resolve_against_keys(raw_path: str, keys: Iterable[str]) -> str | None:
    """Resolve a raw path to one of ``keys`` by trying each candidate in order."""
    key_list = list(keys)
    if not key_list:
        return None
    for candidate in path_candidates(raw_path):
        matched = match_key(candidate, key_list)
        if matched:
            return matched
    return None


def relative_to_repo(repo_path: Path, raw_path: str) -> str | None:
    """Repo-relative POSIX path for an absolute path inside the repository.

    Returns ``None`` when the path is outside the repository or not absolute.
    """
    text = to_posix(raw_path)
    if not text:
        return None

    original = str(raw_path or "").strip().strip(_QUOTE_CHARS).strip()
    candidate = Path(original.replace("\\", "/")) if is_absolute(original) else Path(text)

    try:
        return str(candidate.resolve().relative_to(repo_path.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return None


def resolve_existing(repo_path: Path, raw_path: str) -> str | None:
    """Resolve a raw path to a repo-relative path that exists on disk.

    Tries the absolute interpretation first, then each repo-relative candidate.
    """
    if not str(raw_path or "").strip():
        return None

    repo = repo_path.resolve()

    if is_absolute(raw_path):
        relative = relative_to_repo(repo, raw_path)
        if relative and (repo / relative).is_file():
            return relative

    for candidate in path_candidates(raw_path):
        if (repo / candidate).is_file():
            try:
                return str((repo / candidate).relative_to(repo)).replace("\\", "/")
            except ValueError:
                continue

    return None


def paths_equivalent(left: str, right: str) -> bool:
    """True when two path references denote the same file.

    Equal after normalization, or one side is a bare basename matching the
    other's final segment — so ``auth.py`` and ``vulnapi/auth.py`` are
    equivalent, while ``api.py`` and ``vulnapi/auth.py`` are not. Empty input is
    never equivalent to anything.

    Deliberately stricter than :func:`match_key`: two *qualified* paths must
    match in full. This backs phantom detection in MCI verification, where a
    loose match would silently raise the fidelity score, so partial-suffix
    equality is not accepted here.
    """
    left_token = normalize_path_token(left)
    right_token = normalize_path_token(right)
    if not left_token or not right_token:
        return False
    if left_token == right_token:
        return True

    left_parts = left_token.split("/")
    right_parts = right_token.split("/")
    if left_parts[-1] != right_parts[-1]:
        return False

    return len(left_parts) == 1 or len(right_parts) == 1
