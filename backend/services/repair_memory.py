"""Persistent memory of repairs this pipeline has already made.

Every field is deterministic metadata derived from a completed run: which file
and function were changed, what the validation and security outcomes were, how
many retries it took, and the accepted diff. Nothing is summarised by a model and
nothing is embedded — retrieval is exact-hash matching with a fixed weight table,
so a match is reproducible and explainable.

Retrieval identity is layered, strongest first:

    function hash  — the same function body, byte for byte
    file hash      — the same file content
    file path      — the same file, changed since
    bug type       — the same category of defect anywhere in the repository

A function-hash hit is the only one strong enough to mean "we have literally
fixed this before". The weaker tiers are context, and A7 treats all of them as
metadata that runtime evidence always outranks.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.models.repository_graph import (
    RepairMatch,
    RepairMemory,
    RepairQuery,
    RepairRecord,
)
from backend.services.python_ast_parser import parse_source

# -- similarity weights ----------------------------------------------------
# Fixed and additive, mirroring the convention in `context_ranker`.

W_FUNCTION_HASH = 1.00   # identical function body
W_FILE_HASH = 0.70       # identical file content
W_SAME_FILE = 0.40       # same path, content has since changed
W_SAME_FUNCTION = 0.35   # same qualified function name, body has changed
W_BUG_TYPE = 0.25        # same defect category
W_VALIDATED = 0.15       # the historical repair actually passed validation

# Below this a match is noise; returning it would only dilute the prompt.
MIN_SIMILARITY = 0.25

# Keep memory bounded. Oldest records are dropped first.
MAX_RECORDS = 500

# Diffs are stored for provenance, not for replay into a prompt.
MAX_DIFF_CHARS = 20_000


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def repository_id(repo_path: Path | str) -> str:
    """Stable identity for a repository across runs.

    The pipeline works on a fresh temp clone each run, so the clone path cannot
    identify anything. The basename is what survives, hashed with nothing else so
    two runs of the same repository share one memory.
    """
    return content_hash(Path(str(repo_path)).name or "repository")


def file_hash(repo_path: Path, rel_path: str) -> str:
    try:
        return content_hash((Path(repo_path) / rel_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


def function_hash(repo_path: Path, rel_path: str, qualname: str | None) -> str:
    """Hash of one function's exact source span, or "" when it cannot be read."""
    if not qualname:
        return ""
    try:
        source = (Path(repo_path) / rel_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    parsed = parse_source(source)
    if parsed is None:
        return ""

    for span in parsed.function_spans:
        if span.qualname == qualname or span.name == qualname:
            lines = source.splitlines()
            body = "\n".join(lines[span.span_start - 1 : span.end_lineno])
            return content_hash(body)
    return ""


# -- bug classification ----------------------------------------------------
# Generic Python failure modes only. Nothing here may encode the semantics of a
# particular repository or fixture; a category must be derivable from the
# exception type or the scanner's own rule id alone.

_EXCEPTION_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("null-dereference", ("AttributeError", "TypeError", "UnboundLocalError")),
    ("missing-key", ("KeyError", "IndexError")),
    ("value-validation", ("ValueError", "ValidationError", "AssertionError")),
    ("type-conversion", ("DecimalException", "OverflowError", "ZeroDivisionError")),
    ("io-failure", ("OSError", "IOError", "FileNotFoundError", "PermissionError")),
    ("import-failure", ("ImportError", "ModuleNotFoundError", "NameError")),
    ("timeout", ("TimeoutError", "asyncio.TimeoutError")),
    ("concurrency", ("RuntimeError",)),
)

_SECURITY_RULE_MARKERS = ("B1", "B2", "B3", "B5", "B6", "B7", "bandit", "security")


def classify_bug_type(
    reproduction: dict | None = None,
    root_cause: dict | None = None,
    static_findings: list[dict] | None = None,
) -> str:
    """Assign a defect category from evidence, deterministically.

    Runtime evidence wins: an observed exception type is a fact, while a static
    finding is an inference. Returns "unknown" rather than guessing.
    """
    exception_type = str((reproduction or {}).get("exception_type") or "").strip()
    if exception_type:
        bare = exception_type.rsplit(".", 1)[-1]
        for category, names in _EXCEPTION_CATEGORIES:
            if bare in names or exception_type in names:
                return category
        return f"exception:{bare}"

    for finding in (static_findings or [])[:1]:
        rule = str(finding.get("rule_id") or finding.get("test_id") or "")
        if any(marker in rule for marker in _SECURITY_RULE_MARKERS):
            return "security-finding"
        if rule:
            return f"static:{rule}"

    if (root_cause or {}).get("citations"):
        return "logic-error"
    return "unknown"


# -- record construction ---------------------------------------------------


def build_repair_record(
    *,
    run_id: str,
    repo_path: Path,
    repository_hash: str,
    file: str,
    function: str | None,
    bug_type: str,
    root_cause_summary: str = "",
    affected_files: list[str] | None = None,
    validation_passed: bool = False,
    mutation_score: float | None = None,
    mutation_status: str = "not_run",
    security_score: float | None = None,
    retry_count: int = 0,
    pr_type: str = "",
    accepted_patch_diff: str = "",
    original_file_source: str | None = None,
    original_function_source: str | None = None,
) -> RepairRecord:
    """Build one record.

    The hashes describe the code *as it was before the repair* — that is what a
    future run will be looking at when it asks "have we seen this before?".
    `original_*_source` supplies that pre-patch text; without it the hashes fall
    back to the current working tree, which after A7 is the patched content.
    """
    if original_file_source is not None:
        file_digest = content_hash(original_file_source)
    else:
        file_digest = file_hash(repo_path, file)

    if original_function_source is not None:
        function_digest = content_hash(original_function_source)
    elif original_file_source is not None:
        function_digest = _function_hash_from_source(original_file_source, function)
    else:
        function_digest = function_hash(repo_path, file, function)

    return RepairRecord(
        repair_id=f"{run_id}:{file}:{function or '-'}",
        repository_hash=repository_hash,
        file=file,
        file_hash=file_digest,
        function=function,
        function_hash=function_digest,
        bug_type=bug_type,
        root_cause_summary=(root_cause_summary or "")[:500],
        affected_files=sorted(set(affected_files or [])),
        validation_passed=validation_passed,
        mutation_score=mutation_score,
        mutation_status=mutation_status,
        security_score=security_score,
        retry_count=retry_count,
        pr_type=pr_type,
        accepted_patch_diff=(accepted_patch_diff or "")[:MAX_DIFF_CHARS],
    )


def _function_hash_from_source(source: str, qualname: str | None) -> str:
    if not qualname:
        return ""
    parsed = parse_source(source)
    if parsed is None:
        return ""
    for span in parsed.function_spans:
        if span.qualname == qualname or span.name == qualname:
            lines = source.splitlines()
            return content_hash("\n".join(lines[span.span_start - 1 : span.end_lineno]))
    return ""


def record_repair(memory: RepairMemory, record: RepairRecord) -> RepairMemory:
    """Append or replace a record, keeping memory bounded and deterministic.

    A repeat of the same `repair_id` replaces the earlier entry: re-running the
    same repair should correct the record, not duplicate it.
    """
    kept = [r for r in memory.records if r.repair_id != record.repair_id]
    kept.append(record)
    memory.records = kept[-MAX_RECORDS:]
    return memory


# -- retrieval -------------------------------------------------------------


def find_similar_repairs(
    memory: RepairMemory,
    query: RepairQuery,
    limit: int = 3,
) -> list[RepairMatch]:
    """Rank historical repairs against the current one.

    Records from the current repository state are excluded: a record whose
    `repository_hash` matches the query's was written by this same commit, and
    surfacing a run's own output back to itself is circular.
    """
    matches: list[RepairMatch] = []

    for record in memory.records:
        if not record.validation_passed:
            continue
        if query.repository_hash and record.repository_hash == query.repository_hash:
            continue

        similarity = 0.0
        matched_on: list[str] = []

        if query.function_hash and record.function_hash == query.function_hash:
            similarity += W_FUNCTION_HASH
            matched_on.append("identical function body")
        elif query.function and record.function == query.function and record.file == query.file:
            similarity += W_SAME_FUNCTION
            matched_on.append("same function")

        if query.file_hash and record.file_hash == query.file_hash:
            similarity += W_FILE_HASH
            matched_on.append("identical file content")
        elif query.file and record.file == query.file:
            similarity += W_SAME_FILE
            matched_on.append("same file")

        if query.bug_type != "unknown" and record.bug_type == query.bug_type:
            similarity += W_BUG_TYPE
            matched_on.append(f"same bug type ({record.bug_type})")

        if record.validation_passed:
            similarity += W_VALIDATED
            matched_on.append("validated repair")

        if similarity >= MIN_SIMILARITY and matched_on:
            matches.append(
                RepairMatch(
                    record=record,
                    similarity=round(similarity, 4),
                    matched_on=matched_on,
                )
            )

    # Total order: similarity desc, most recent first, then id for stability.
    matches.sort(key=lambda m: (-m.similarity, -m.record.recorded_at.timestamp(), m.record.repair_id))
    return matches[:limit]


def repair_signal(memory: RepairMemory, file: str) -> float:
    """0..1 — how often this file has needed repair before.

    A file the pipeline has repaired repeatedly is a plausible place to look
    again. Saturates at three prior repairs so one hot file cannot dominate.
    """
    prior = sum(1 for r in memory.records if r.file == file and r.validation_passed)
    return round(min(1.0, prior / 3.0), 4) if prior else 0.0


def summarize_matches(matches: list[RepairMatch]) -> list[dict]:
    """Flatten matches into prompt-safe metadata.

    The diff is deliberately excluded. A historical patch pasted into a prompt
    invites the model to reproduce it instead of reading the runtime evidence,
    which is precisely the failure mode this layer must not introduce.
    """
    return [
        {
            "file": m.record.file,
            "function": m.record.function,
            "bug_type": m.record.bug_type,
            "root_cause_summary": m.record.root_cause_summary,
            "retry_count": m.record.retry_count,
            "mutation_score": m.record.mutation_score,
            "security_score": m.record.security_score,
            "similarity": m.similarity,
            "matched_on": list(m.matched_on),
        }
        for m in matches
    ]


_NON_SLUG = re.compile(r"[^a-z0-9_.:-]+")


def normalize_bug_type(value: str) -> str:
    """Lowercase slug form, so categories compare exactly across runs."""
    return _NON_SLUG.sub("-", (value or "unknown").strip().lower()).strip("-") or "unknown"
