"""Parse real mutation-testing output into evidence-backed counts.

Replaces the previous hardcoded `0.5` mutation score. A merge decision may only
consume numbers that were actually measured, so this module has exactly two
possible answers: a score derived from parsed mutant statuses, or an explicit
``unavailable`` outcome carrying the reason it could not be determined. There is
no third "assume something reasonable" path.

Supported mutmut output shapes, in preference order:

1. **Per-mutant listing** (mutmut 3.x ``mutmut results``) — lines of the form
   ``    <mutant_key>: <status>``. Most precise: every mutant is accounted for
   individually.
2. **Progress summary** (mutmut 3.x ``mutmut run``) — the emoji status line
   ``12/20  🎉 8 🫥 0  ⏰ 0  🤔 0  🙁 2  🔇 0  🧙 0``.
3. **Section headers** (mutmut 2.x ``mutmut results``) — ``Survived 🙁 (2)``.

Status classification follows mutmut's own ``status_by_exit_code`` vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from backend.models.validation import MutantRecord

# A mutant the test suite demonstrably caught.
DETECTED_STATUSES = frozenset({"killed", "timeout", "caught by type check"})

# A mutant the test suite failed to catch — the signal that matters.
SURVIVED_STATUSES = frozenset({"survived"})

# Statuses that establish neither outcome. Counted and reported, but excluded
# from the score on both sides: inflating the numerator would overstate test
# strength, and inflating the denominator would punish mutants that were never
# meaningfully evaluated (skipped by pragma, no covering test, interrupted).
INCONCLUSIVE_STATUSES = frozenset({
    "no tests",
    "skipped",
    "not checked",
    "suspicious",
    "segfault",
    "check was interrupted by user",
})

KNOWN_STATUSES = DETECTED_STATUSES | SURVIVED_STATUSES | INCONCLUSIVE_STATUSES

# "not_run" is distinct from "unavailable": mutation testing was never attempted
# (the test suite failed first), rather than attempted and inconclusive.
MutationStatus = Literal["scored", "unavailable", "not_run"]

# mutmut 3.x per-mutant line: "    x_auth__mutmut_1: survived"
_RESULT_LINE_RE = re.compile(
    r"^\s*(?P<key>\S+):\s*(?P<status>" + "|".join(sorted(KNOWN_STATUSES, key=len, reverse=True)) + r")\s*$",
    re.MULTILINE,
)

# mutmut names every mutant after the function it mutates:
# "x_decode_token__mutmut_3". This is the only attribution A8 gets for free —
# real, mutmut's own naming convention, not a guess — so it is worth keeping
# even though it is function-level, not line-level.
_FUNCTION_FROM_ID_RE = re.compile(r"^x_(?P<func>.+?)__mutmut_\d+$")

# `mutmut show <id>` unified-diff headers, used to recover the one line a
# mutant actually changed (line-level, exact) for mutants worth the extra
# subprocess call — see `parse_mutmut_show_diff`.
_DIFF_FILE_HEADER_RE = re.compile(r"^--- (?:a/)?(?P<file>\S+)")
_DIFF_HUNK_HEADER_RE = re.compile(r"^@@ -(?P<start>\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


def mutant_function_name(mutant_id: str) -> str | None:
    """The function mutmut's own naming convention says this mutant attacks.

    `None` for anything that doesn't match `x_<function>__mutmut_<n>` — never
    a guess at attribution the id doesn't actually carry.
    """
    match = _FUNCTION_FROM_ID_RE.match(mutant_id)
    return match.group("func") if match else None


def parse_mutant_records(text: str) -> list[MutantRecord]:
    """Every individual mutant from a `mutmut results --all true` listing.

    Returns `[]` when the text isn't the per-mutant listing shape (the
    progress-summary and legacy-section formats carry no per-mutant lines to
    extract) — callers must not treat an empty list as "zero mutants".
    """
    records: list[MutantRecord] = []
    for match in _RESULT_LINE_RE.finditer(text):
        key = match.group("key")
        records.append(
            MutantRecord(
                mutant_id=key,
                status=match.group("status"),
                function=mutant_function_name(key),
            )
        )
    return records


def parse_mutmut_show_diff(text: str) -> dict | None:
    """The one line `mutmut show <id>` says this mutant changed.

    Reads a real unified diff and returns `None` the moment the shape doesn't
    match what it expects — this never fabricates a line number or a before/
    after pair from output it can't parse.
    """
    file = None
    for raw in text.splitlines():
        header = _DIFF_FILE_HEADER_RE.match(raw)
        if header:
            file = header.group("file")
            break

    lines = text.splitlines()
    for i, raw in enumerate(lines):
        hunk = _DIFF_HUNK_HEADER_RE.match(raw)
        if not hunk:
            continue
        cursor = int(hunk.group("start"))
        before: str | None = None
        before_line: int | None = None
        after: str | None = None
        for body in lines[i + 1 :]:
            if body.startswith("@@") or body.startswith("--- ") or body.startswith("+++ "):
                break
            if body.startswith("-"):
                before = body[1:].strip()
                before_line = cursor
                cursor += 1
                continue
            if body.startswith("+"):
                after = body[1:].strip()
                if before is not None:
                    break
                continue
            cursor += 1  # unchanged context line
        if before is not None and after is not None:
            return {"file": file, "line": before_line, "before": before, "after": after}
    return None

# mutmut 3.x progress line. The spinner prefix and \r carriage returns are
# tolerated; the counts are anchored on their emoji.
_SUMMARY_RE = re.compile(
    r"(?P<checked>\d+)/(?P<total>\d+)\s+"
    r"🎉\s*(?P<killed>\d+)\s+"
    r"🫥\s*(?P<no_tests>\d+)\s+"
    r"⏰\s*(?P<timeout>\d+)\s+"
    r"🤔\s*(?P<suspicious>\d+)\s+"
    r"🙁\s*(?P<survived>\d+)\s+"
    r"🔇\s*(?P<skipped>\d+)"
    r"(?:\s+🧙\s*(?P<type_checked>\d+))?"
)

# mutmut 2.x section header: "Survived 🙁 (2)"
_SECTION_RE = re.compile(
    r"^(?P<label>Survived|Killed|Timed out|Suspicious|Skipped|Untested)\b[^\(\n]*\((?P<count>\d+)\)",
    re.MULTILINE | re.IGNORECASE,
)

_SECTION_STATUS = {
    "survived": "survived",
    "killed": "killed",
    "timed out": "timeout",
    "suspicious": "suspicious",
    "skipped": "skipped",
    "untested": "not checked",
}


@dataclass
class MutationCounts:
    """Mutant tallies grouped by mutmut status."""

    by_status: dict[str, int] = field(default_factory=dict)

    def add(self, status: str, count: int = 1) -> None:
        if count <= 0:
            return
        self.by_status[status] = self.by_status.get(status, 0) + count

    def _sum(self, statuses: frozenset[str]) -> int:
        return sum(count for status, count in self.by_status.items() if status in statuses)

    @property
    def killed(self) -> int:
        return self._sum(DETECTED_STATUSES)

    @property
    def survived(self) -> int:
        return self._sum(SURVIVED_STATUSES)

    @property
    def inconclusive(self) -> int:
        return self._sum(INCONCLUSIVE_STATUSES)

    @property
    def evaluated(self) -> int:
        """Mutants that produced a determinate kill/survive verdict."""
        return self.killed + self.survived

    @property
    def recorded(self) -> int:
        return sum(self.by_status.values())

    def score(self) -> float | None:
        """Killed / evaluated, or None when nothing was conclusively evaluated."""
        if self.evaluated <= 0:
            return None
        return round(self.killed / self.evaluated, 4)


class MutationOutcome(BaseModel):
    """The only thing A8 is permitted to report about mutation testing."""

    status: MutationStatus = "unavailable"
    mutation_score: float | None = None
    killed_mutants: int | None = None
    survived_mutants: int | None = None
    total_mutants: int | None = None
    inconclusive_mutants: int | None = None
    unavailable_reason: str | None = None
    by_status: dict[str, int] = Field(default_factory=dict)
    #: Individual mutants, when the per-mutant listing was available. `[]`
    #: for the progress-summary and legacy-section formats, which carry only
    #: aggregate counts — never backfilled with synthesized records.
    mutants: list[MutantRecord] = Field(default_factory=list)

    @property
    def mutant_survived(self) -> bool:
        return bool(self.survived_mutants)


def _unavailable(reason: str) -> MutationOutcome:
    return MutationOutcome(status="unavailable", unavailable_reason=reason)


def parse_results_output(text: str) -> MutationCounts | None:
    """Parse a per-mutant listing. Returns None when no mutant lines are present."""
    if not text:
        return None

    counts = MutationCounts()
    for match in _RESULT_LINE_RE.finditer(text):
        counts.add(match.group("status"))

    return counts if counts.recorded else None


def parse_run_summary(text: str) -> MutationCounts | None:
    """Parse the mutmut progress summary. Returns None when absent."""
    if not text:
        return None

    # A run rewrites the line in place; the final occurrence is the total.
    match = None
    for match in _SUMMARY_RE.finditer(text):
        pass
    if match is None:
        return None

    counts = MutationCounts()
    counts.add("killed", int(match.group("killed")))
    counts.add("survived", int(match.group("survived")))
    counts.add("timeout", int(match.group("timeout")))
    counts.add("no tests", int(match.group("no_tests")))
    counts.add("suspicious", int(match.group("suspicious")))
    counts.add("skipped", int(match.group("skipped")))
    if match.group("type_checked"):
        counts.add("caught by type check", int(match.group("type_checked")))

    total = int(match.group("total"))
    unchecked = total - counts.recorded
    counts.add("not checked", max(0, unchecked))

    return counts if counts.recorded else None


def parse_legacy_sections(text: str) -> MutationCounts | None:
    """Parse mutmut 2.x section headers. Returns None when absent."""
    if not text:
        return None

    counts = MutationCounts()
    for match in _SECTION_RE.finditer(text):
        status = _SECTION_STATUS.get(match.group("label").lower())
        if status:
            counts.add(status, int(match.group("count")))

    return counts if counts.recorded else None


def counts_to_outcome(
    counts: MutationCounts, mutants: list[MutantRecord] | None = None
) -> MutationOutcome:
    """Convert parsed counts into an outcome, or `unavailable` if inconclusive."""
    records = mutants or []
    score = counts.score()
    if score is None:
        return MutationOutcome(
            status="unavailable",
            unavailable_reason=(
                "no mutants produced a conclusive result "
                f"({counts.inconclusive} inconclusive, {counts.recorded} recorded)"
            ),
            killed_mutants=0,
            survived_mutants=0,
            total_mutants=0,
            inconclusive_mutants=counts.inconclusive,
            by_status=dict(counts.by_status),
            mutants=records,
        )

    return MutationOutcome(
        status="scored",
        mutation_score=score,
        killed_mutants=counts.killed,
        survived_mutants=counts.survived,
        total_mutants=counts.evaluated,
        inconclusive_mutants=counts.inconclusive,
        by_status=dict(counts.by_status),
        mutants=records,
    )


def parse_mutation_output(
    *,
    run_exit_code: int,
    run_stdout: str = "",
    run_stderr: str = "",
    results_exit_code: int | None = None,
    results_stdout: str = "",
    results_stderr: str = "",
) -> MutationOutcome:
    """Derive a mutation outcome from mutmut invocations.

    Never fabricates. When the tool did not run, or ran but emitted nothing this
    module can interpret, the result is `unavailable` with a stated reason.
    """
    if run_exit_code == -1:
        detail = (run_stderr or "").strip().splitlines()
        reason = detail[-1] if detail else "mutmut could not be executed"
        return _unavailable(f"mutmut execution failed: {reason}")

    # Per-mutant listing is the most precise source available.
    # `mutmut results` is stateful and may expose a previous invocation. Only
    # consume it when the current mutation run completed successfully.
    if run_exit_code == 0 and results_exit_code != -1:
        counts = parse_results_output(results_stdout)
        if counts:
            return counts_to_outcome(counts, parse_mutant_records(results_stdout))
        counts = parse_legacy_sections(results_stdout)
        if counts:
            return counts_to_outcome(counts)

    combined_run = f"{run_stdout}\n{run_stderr}"
    counts = parse_run_summary(combined_run) or parse_legacy_sections(combined_run)
    if counts:
        return counts_to_outcome(counts)

    if "failed to collect stats" in combined_run:
        missing = re.search(r"ModuleNotFoundError: No module named ['\"]([\w.]+)['\"]", combined_run)
        detail = f" (missing module {missing.group(1)})" if missing else ""
        return _unavailable(f"mutmut test collection failed before mutation testing{detail}")

    return _unavailable("mutmut produced no parseable mutation results")
