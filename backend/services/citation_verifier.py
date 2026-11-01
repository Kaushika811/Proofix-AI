"""Deterministic citation path normalization and multi-stage verification."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from backend.services.path_resolution import (
    match_key,
    normalize_path_token,
    path_candidates,
    relative_to_repo,
)

FUNCTION_NAME_RE = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(")
LINE_WINDOW_OFFSETS = (0, 1, 2, 3, 5)


@dataclass
class CitationFingerprint:
    function_name: str | None = None
    normalized_line: str | None = None
    claim_line: str | None = None
    claim_tokens: set[str] = field(default_factory=set)


@dataclass
class CitationMetrics:
    total_citations: int = 0
    verified_exact: int = 0
    verified_line_window: int = 0
    verified_ast: int = 0
    verified_fingerprint: int = 0
    unresolved: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total_citations": self.total_citations,
            "verified_exact": self.verified_exact,
            "verified_line_window": self.verified_line_window,
            "verified_ast": self.verified_ast,
            "verified_fingerprint": self.verified_fingerprint,
            "unresolved": self.unresolved,
        }

    def record(self, method: str) -> None:
        if method == "verified_exact":
            self.verified_exact += 1
        elif method == "verified_line_window":
            self.verified_line_window += 1
        elif method == "verified_ast":
            self.verified_ast += 1
        elif method == "verified_fingerprint":
            self.verified_fingerprint += 1
        else:
            self.unresolved += 1


def _sig_file_keys(sig: dict | None) -> list[str]:
    if not sig:
        return []
    files = sig.get("files") or {}
    if isinstance(files, dict):
        return list(files.keys())
    return []


def _rglob_matches(repo_path: Path, basename: str) -> list[str]:
    if not basename:
        return []
    matches: list[str] = []
    for path in repo_path.rglob(basename):
        if path.is_file():
            try:
                matches.append(str(path.resolve().relative_to(repo_path.resolve())).replace("\\", "/"))
            except ValueError:
                continue
    return matches


def _pick_rglob_match(raw_path: str, matches: list[str]) -> str | None:
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    cited = normalize_path_token(raw_path)
    scored: list[tuple[int, str]] = []
    for match in matches:
        folded = normalize_path_token(match)
        score = 0
        if cited and (folded == cited or folded.endswith(f"/{cited}") or cited.endswith(f"/{folded}")):
            score += 10
        score += folded.count("/")
        scored.append((score, match))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return None


def resolve_citation_path(
    repo_path: Path,
    raw_path: str,
    sig: dict | None = None,
) -> str | None:
    repo = repo_path.resolve()
    sig_files = _sig_file_keys(sig)

    for candidate in path_candidates(raw_path):
        full = repo / candidate
        if full.is_file():
            return str(full.relative_to(repo)).replace("\\", "/")

        matched = match_key(candidate, sig_files)
        if matched and (repo / matched).is_file():
            return matched

    name = PurePosixPath(str(raw_path).replace("\\", "/")).name
    rglob = _rglob_matches(repo, name)
    picked = _pick_rglob_match(raw_path, rglob)
    if picked:
        return picked

    absolute = Path(str(raw_path).replace("\\", "/"))
    if absolute.is_absolute() and absolute.is_file():
        relative = relative_to_repo(repo, raw_path)
        if relative:
            return relative

    return None


def extract_function_name(claim: str) -> str | None:
    match = FUNCTION_NAME_RE.search(claim)
    if match:
        return match.group(1)
    match = re.search(r"\b([a-z_][a-z0-9_]*)\s+function\b", claim, re.I)
    if match:
        return match.group(1)
    return None


def _normalize_line_text(line: str) -> str:
    return " ".join(line.strip().split())


def _claim_tokens(claim: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]+", claim.lower()):
        if len(token) > 3:
            tokens.add(token)
    return tokens


def build_fingerprint(claim: str, lines: list[str] | None = None, line: int | None = None) -> CitationFingerprint:
    normalized_line = None
    if lines and line and 1 <= line <= len(lines):
        normalized_line = _normalize_line_text(lines[line - 1])
    return CitationFingerprint(
        function_name=extract_function_name(claim),
        normalized_line=normalized_line,
        claim_line=(
            _normalize_line_text(claim)
            if re.match(r"\s*(def|async\s+def|if|elif|else|for|while|return|class|[A-Za-z_][\w.]*\s*=)", claim)
            else None
        ),
        claim_tokens=_claim_tokens(claim),
    )


def _line_matches_fingerprint(
    line_text: str,
    fingerprint: CitationFingerprint,
    *,
    use_normalized_line: bool = False,
) -> bool:
    normalized = _normalize_line_text(line_text)
    if not normalized:
        return False
    if use_normalized_line and fingerprint.normalized_line and fingerprint.normalized_line == normalized:
        return True
    if fingerprint.function_name and fingerprint.function_name in normalized:
        return True
    lowered = normalized.lower()
    if fingerprint.claim_tokens and any(token in lowered for token in fingerprint.claim_tokens):
        return True
    return False


def _function_exists_in_ast(source: str, function_name: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return function_name in source

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return True
    return False


def _verify_exact(lines: list[str], line: int, fingerprint: CitationFingerprint) -> bool:
    if not (1 <= line <= len(lines)):
        return False
    return _line_matches_fingerprint(lines[line - 1], fingerprint)


def _verify_line_window(lines: list[str], line: int, fingerprint: CitationFingerprint) -> int | None:
    for offset in LINE_WINDOW_OFFSETS:
        for candidate in {line - offset, line + offset}:
            if candidate == line:
                continue
            if 1 <= candidate <= len(lines) and _line_matches_fingerprint(lines[candidate - 1], fingerprint):
                return candidate
    return None


def _verify_fingerprint_anywhere(
    lines: list[str],
    fingerprint: CitationFingerprint,
    cited_line: int,
) -> int | None:
    needle = fingerprint.claim_line or fingerprint.normalized_line
    if not needle:
        return None
    matches = [
        idx + 1
        for idx, line in enumerate(lines)
        if _normalize_line_text(line) == needle and (idx + 1) != cited_line
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def verify_citation(
    repo_path: Path,
    citation: dict,
    sig: dict | None = None,
) -> tuple[dict, str]:
    claim = str(citation.get("claim") or "").strip()
    line = int(citation.get("line") or 1)
    raw_file = str(citation.get("file") or "")

    # A matching symbol or line locates source; it cannot prove the prose claim.
    # Overwrite caller-supplied status so an LLM cannot assert its own support.
    citation = {**citation, "claim_support_status": "unverified"}
    resolved = resolve_citation_path(repo_path, raw_file, sig=sig)
    if not resolved:
        return {**citation, "verified": False, "source_located": False}, "unresolved"

    full = repo_path / resolved
    try:
        source = full.read_text(encoding="utf-8")
    except OSError:
        return {**citation, "file": resolved, "verified": False, "source_located": False}, "unresolved"

    lines = source.splitlines()
    fingerprint = build_fingerprint(claim, lines=lines, line=line)

    if _verify_exact(lines, line, fingerprint):
        return {
            **citation,
            "file": resolved,
            "line": line,
            "verified": True,
            "source_located": True,
        }, "verified_exact"

    window_line = _verify_line_window(lines, line, fingerprint)
    if window_line is not None:
        return {
            **citation,
            "file": resolved,
            "line": window_line,
            "verified": True,
            "source_located": True,
        }, "verified_line_window"

    if fingerprint.function_name and _function_exists_in_ast(source, fingerprint.function_name):
        return {
            **citation,
            "file": resolved,
            "line": line,
            "verified": True,
            "source_located": True,
        }, "verified_ast"

    anywhere = _verify_fingerprint_anywhere(lines, fingerprint, line)
    if anywhere is not None:
        return {
            **citation,
            "file": resolved,
            "line": anywhere,
            "verified": True,
            "source_located": True,
        }, "verified_fingerprint"

    return {
        **citation,
        "file": resolved,
        "line": line,
        "verified": False,
        "source_located": False,
    }, "unresolved"


def verify_all_citations_with_metrics(
    repo_path: Path,
    citations: list[dict],
    sig: dict | None = None,
) -> tuple[list[dict], dict[str, int]]:
    metrics = CitationMetrics(total_citations=len(citations))
    validated: list[dict] = []

    for citation in citations:
        result, method = verify_citation(repo_path, citation, sig=sig)
        metrics.record(method)
        validated.append(result)

    return validated, metrics.to_dict()


# -- LLM citation coercion -------------------------------------------------
# Moved here from `citation_validator.py`, which was a pass-through shim: its
# `validate_all_citations*` functions returned `verify_all_citations_with_metrics`
# unchanged, so every caller took one extra hop to reach this module. Coercion
# is the only thing that file did, and it belongs beside the verification it
# feeds — malformed citations are dropped before they are ever verified.

def _normalize_file_path(file: Any) -> str | None:
    if file is None:
        return None
    text = str(file).strip()
    return text.lstrip("/") if text else None


def _normalize_line(line: Any) -> int | None:
    if line is None:
        return None
    try:
        parsed = int(line)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _ref_to_citation(ref: Any) -> dict | None:
    if hasattr(ref, "model_dump"):
        data = ref.model_dump()
    elif isinstance(ref, dict):
        data = ref
    else:
        return None

    file = _normalize_file_path(data.get("file"))
    line = _normalize_line(data.get("line"))
    claim = str(data.get("claim") or data.get("description") or "").strip()
    if not file or line is None or not claim:
        return None
    return {"file": file, "line": line, "claim": claim}


def coerce_llm_citations(
    citations: list[dict] | None,
    evidence_refs: list[Any] | None = None,
) -> list[dict]:
    """Drop or repair malformed LLM citations before Pydantic validation."""
    coerced: list[dict] = []
    seen: set[tuple[str, int, str]] = set()

    for raw in citations or []:
        if not isinstance(raw, dict):
            continue
        file = _normalize_file_path(raw.get("file"))
        line = _normalize_line(raw.get("line"))
        claim = str(raw.get("claim") or raw.get("description") or "").strip()
        if not file or line is None or not claim:
            continue
        key = (file, line, claim)
        if key in seen:
            continue
        seen.add(key)
        coerced.append({"file": file, "line": line, "claim": claim})

    if coerced:
        return coerced

    for ref in evidence_refs or []:
        citation = _ref_to_citation(ref)
        if citation is None:
            continue
        key = (citation["file"], citation["line"], citation["claim"])
        if key in seen:
            continue
        seen.add(key)
        coerced.append(citation)

    return coerced
