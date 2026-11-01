"""A9 — post-patch security re-scan.

Answers one question: **did the patch introduce a vulnerability that was not
there before?** It is a differential check, so the whole agent turns on what
"the same finding" means across two scans of a file whose lines have moved.

Two defects shaped the current form, both of the same family — a number that
was not a measurement:

* The finding key was ``file:line:message[:50]``. A patch that inserts a line
  above a pre-existing finding shifts it down, the key changes, and the
  unchanged finding is reported as newly introduced — a rejection, a retry, and
  a draft PR, for code the patch never touched.
* An absent scanner produced ``security_score = 100.0``. `bandit` not being
  installed is not evidence that a patch is safe, but it scored higher than any
  real scan can and cleared `SECURITY_TECHNICAL_THRESHOLD` outright.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from backend.agents.base import AgentBase
from backend.models.findings import Finding
from backend.models.validation import SecurityRescanResult, ValidationFailure
from backend.services.path_resolution import normalize_path_token
from backend.services.retry_brief_builder import build_retry_brief
from backend.services.repo_layout import (
    bandit_exclude_arg,
    get_scan_targets,
    resolve_source_roots,
    semgrep_exclude_args,
)
from backend.services.security_rescan_commands import build_security_rescan_command
from backend.services.subprocess_runner import parse_json_safe, run_command
from backend.state.schema import RunStateModel

#: Points deducted per newly introduced finding.
NEW_FINDING_PENALTY = 25.0

_WHITESPACE = re.compile(r"\s+")

#: Bandit reports `issue_severity` as HIGH / MEDIUM / LOW.
_BANDIT_SEVERITY: dict[str, float] = {
    "HIGH": 0.9,
    "MEDIUM": 0.6,
    "LOW": 0.3,
}

#: Semgrep reports `extra.severity` as ERROR / WARNING / INFO.
_SEMGREP_SEVERITY: dict[str, float] = {
    "ERROR": 0.9,
    "WARNING": 0.6,
    "INFO": 0.3,
}


def _bandit_severity(result: dict) -> float | None:
    """Normalised severity from bandit's own `issue_severity`, or `None`.

    Bandit always populates this field; `None` is the honest fallback for a
    result whose structure does not match expectations rather than a fabricated
    middle value.
    """
    raw = str(result.get("issue_severity", "")).strip().upper()
    return _BANDIT_SEVERITY.get(raw)


def _semgrep_severity(result: dict) -> float | None:
    """Normalised severity from semgrep's own `extra.severity`, or `None`."""
    raw = str(result.get("extra", {}).get("severity", "")).strip().upper()
    return _SEMGREP_SEVERITY.get(raw)

#: A finding's identity for differential comparison: which tool said it, in
#: which file, about what. **Not the line** — that is what moves under a patch.
FindingKey = tuple[str, str, str]


def finding_key(finding: dict, tool: str) -> FindingKey:
    """What makes two findings across two scans *the same* finding.

    The message is normalized only for whitespace and case, never truncated: the
    old 50-character prefix collided distinct bandit issues that share an
    opening clause, and a collision here is a real new vulnerability silently
    accepted — the failure direction that matters most.
    """
    message = _WHITESPACE.sub(" ", str(finding.get("message", ""))).strip().casefold()
    return (tool, normalize_path_token(str(finding.get("file", ""))), message)


#: The scanners A9 can compare. `ruff` is deliberately absent — style, not
#: security (see `_keyed`) — so it never gets a lane in the ledger either.
KNOWN_SCANNERS: tuple[str, ...] = ("bandit", "semgrep")


def _line_of(finding: dict) -> int:
    return int(finding.get("line", 0) or 0)


@dataclass(frozen=True)
class KeyReconciliation:
    """One `(tool, file, message)` key, reconciled across the two scans.

    `entry` is the durable, JSON-safe account of the comparison; `baseline` and
    `post` are the original finding dicts it indexes into, kept only so the
    caller can lift the introduced ones back out without re-deriving them.
    """

    entry: dict
    baseline: list[dict]
    post: list[dict]


def _reconcile_key(
    key: FindingKey,
    baseline_findings: list[dict],
    post_findings: list[dict],
) -> dict:
    """The whole comparison for one key: what carried, what is new, what went.

    Two occurrences of the same key are *the same finding across the two scans*
    only up to multiplicity — nothing in either scanner's output identifies an
    individual occurrence, so the pairing is constructed, not read off. It is
    constructed the way a reader would: an occurrence still sitting on its
    original line pairs with itself first (delta 0), and only then do the moved
    ones pair with the nearest baseline line left over. That ordering is what
    makes the rendered rails show the *smallest* shift consistent with the
    evidence rather than an arbitrary one.

    The surplus rule is unchanged and load-bearing: when a key gained
    occurrences, the ones reported as introduced are those whose line the
    baseline cannot account for, so the retry brief names the copy the patch
    added rather than the pre-existing one.
    """
    tool, file, _normalized = key
    surplus = max(0, len(post_findings) - len(baseline_findings))
    known_lines = {_line_of(f) for f in baseline_findings}
    # Stable sort on a boolean: unmatched lines keep their scan order, then the
    # already-occupied ones. Identical to the ordering this replaced.
    ordered = sorted(
        range(len(post_findings)),
        key=lambda j: _line_of(post_findings[j]) in known_lines,
    )
    introduced_indexes = ordered[:surplus]
    carried_indexes = ordered[surplus:]

    remaining = list(range(len(baseline_findings)))
    matched: list[tuple[int, int]] = []
    unpaired: list[int] = []
    for j in sorted(carried_indexes, key=lambda j: _line_of(post_findings[j])):
        exact = next(
            (i for i in remaining if _line_of(baseline_findings[i]) == _line_of(post_findings[j])),
            None,
        )
        if exact is None:
            unpaired.append(j)
            continue
        remaining.remove(exact)
        matched.append((exact, j))
    for j in unpaired:
        # `carried_indexes` never exceeds the baseline count, so `remaining`
        # cannot be empty here.
        i = min(
            remaining,
            key=lambda i: abs(_line_of(baseline_findings[i]) - _line_of(post_findings[j])),
        )
        remaining.remove(i)
        matched.append((i, j))
    matched.sort(key=lambda pair: _line_of(post_findings[pair[1]]))

    # The displayed message is the raw one, not the key's casefolded/whitespace-
    # collapsed form — the key exists to compare, not to read.
    sample = (post_findings or baseline_findings or [{}])[0]
    return {
        "tool": tool,
        "file": file,
        "message": str(sample.get("message", "")),
        "baseline": [{"line": _line_of(f)} for f in baseline_findings],
        "post": [{"line": _line_of(f)} for f in post_findings],
        "matched": [
            {
                "baseline_index": i,
                "post_index": j,
                "line_delta": _line_of(post_findings[j]) - _line_of(baseline_findings[i]),
            }
            for i, j in matched
        ],
        "introduced_indexes": introduced_indexes,
        "resolved_indexes": sorted(remaining),
    }


def reconcile(
    baseline: list[tuple[FindingKey, dict]],
    post: list[tuple[FindingKey, dict]],
) -> list[KeyReconciliation]:
    """The full two-scan reconciliation, per key, computed once.

    Counting, not set difference. Set difference answers "does this kind of
    finding exist in both scans", which drops a real regression: a file that had
    one hardcoded password and now has two contains a new one, and both share a
    key. Comparing *counts* per key catches that while still absorbing a pure
    line shift, where the count is unchanged.

    Keys present in the post scan come first, in scan order — `new_findings` is
    read straight off this list and its order is part of A9's existing contract.
    Keys only the baseline had (every occurrence resolved) follow.
    """
    baseline_groups: dict[FindingKey, list[dict]] = {}
    for key, finding in baseline:
        baseline_groups.setdefault(key, []).append(finding)
    post_groups: dict[FindingKey, list[dict]] = {}
    for key, finding in post:
        post_groups.setdefault(key, []).append(finding)

    ordered_keys = list(post_groups) + [k for k in baseline_groups if k not in post_groups]
    return [
        KeyReconciliation(
            entry=_reconcile_key(key, baseline_groups.get(key, []), post_groups.get(key, [])),
            baseline=baseline_groups.get(key, []),
            post=post_groups.get(key, []),
        )
        for key in ordered_keys
    ]


def introduced_from_reconciliation(reconciliations: list[KeyReconciliation]) -> list[dict]:
    """The post-scan findings the baseline cannot account for."""
    return [rec.post[j] for rec in reconciliations for j in rec.entry["introduced_indexes"]]


def reconciliation_lanes(
    reconciliations: list[KeyReconciliation], scanners_run: list[str]
) -> list[dict]:
    """One lane per known scanner, whether or not it ran.

    A scanner that did not run gets a lane with `compared=False` and no keys,
    never an empty lane that reads as "nothing found". Its baseline is already
    excluded upstream (`_keyed` only keys tools that ran), so there is nothing
    to put in it — the lane exists precisely to say so.
    """
    return [
        {
            "tool": tool,
            "compared": tool in scanners_run,
            "keys": [rec.entry for rec in reconciliations if rec.entry["tool"] == tool],
        }
        for tool in KNOWN_SCANNERS
    ]


def new_findings_by_multiplicity(
    baseline: list[tuple[FindingKey, dict]],
    post: list[tuple[FindingKey, dict]],
) -> list[dict]:
    """Post-scan findings with no counterpart in the baseline.

    Kept as the narrow question A9's rejection gate asks; the reasoning lives in
    `reconcile`, which also keeps the side of the comparison this discards.
    """
    return introduced_from_reconciliation(reconcile(baseline, post))


class A9SecurityRescanAgent(AgentBase):
    agent_id = "A9"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Running post-patch security re-scan")
        repo = Path(state.repo_clone_path or state.repo_path).resolve()
        static = state.static_report or {}
        baseline = static.get("baseline_json", {})
        sig_data = state.sig or await self.store.get_json(state.run_id, "sig")
        scan_targets = get_scan_targets(state, repo, sig_data)
        source_roots = resolve_source_roots(repo, state.source_roots or None, sig_data)

        bandit_executed, post_bandit = await self._run_bandit(repo, scan_targets)
        semgrep_executed, post_semgrep = await self._run_semgrep(repo, scan_targets)
        scanners_run = [
            tool
            for tool, executed in (("bandit", bandit_executed), ("semgrep", semgrep_executed))
            if executed
        ]

        # Only tools that ran may contribute to the comparison. A baseline from a
        # tool absent this time would make every one of its findings look
        # resolved, and a post-scan from a tool absent at baseline would make
        # every one of its findings look new.
        baseline_pairs = self._keyed(baseline, scanners_run)
        post_pairs = [(finding_key(f, "bandit"), f) for f in post_bandit] + [
            (finding_key(f, "semgrep"), f) for f in post_semgrep
        ]

        # Reconciled once and kept. The rejection gate reads only the introduced
        # side, but the carried pairs are the evidence that a shifted finding is
        # a shifted finding — discarding them left every downstream reader with
        # "trust me" where the comparison should be.
        reconciliations = reconcile(baseline_pairs, post_pairs)
        new_findings = [
            Finding(
                id=f"new-{index}",
                file=f.get("file", ""),
                line=f.get("line", 0),
                message=f.get("message", ""),
                tools=[f.get("tool", "")],
                severity=f.get("severity") if f.get("severity") is not None else 0.0,
                severity_measured=f.get("severity") is not None,
            )
            for index, f in enumerate(introduced_from_reconciliation(reconciliations))
        ]

        rejected = len(new_findings) > 0
        # No scanner ran, so nothing was verified. A perfect score here would be
        # `bandit is not installed` reported as `this patch is safe`, and it
        # cleared the auto-merge security gate outright. Absent is not 100 for
        # the same reason it is not 0 (`services/measurement.py`).
        security_score = (
            max(0.0, 100.0 - len(new_findings) * NEW_FINDING_PENALTY) if scanners_run else None
        )
        reexecution_command, reexecution_timeout = build_security_rescan_command(scan_targets)
        failure_brief = None
        validation_failure = None
        if rejected:
            nf = new_findings[0]
            security_constraint = f"must not introduce {nf.message} near {nf.file}:{nf.line}"
            validation_failure = ValidationFailure(
                assertion_message=f"New security finding: {nf.message}",
                validation_stage="security",
                pytest_stdout="",
                pytest_stderr="",
            )
            failure_brief = build_retry_brief(
                validation_failure,
                state.retry_count + 1,
                patch_bundle=state.patch_bundle,
                security_constraint=security_constraint,
            )

        result = SecurityRescanResult(
            new_findings=[f.model_dump() for f in new_findings],
            rejected=rejected,
            security_score=security_score,
            failure_brief=failure_brief,
            validation_failure=validation_failure,
            reexecution_command=reexecution_command,
            reexecution_timeout_seconds=reexecution_timeout,
            scanners_run=scanners_run,
            reconciliation=reconciliation_lanes(reconciliations, scanners_run),
        )
        result_dict = result.model_dump(mode="json")
        if validation_failure:
            validation_failure = validation_failure.model_copy(
                update={"security_result": result_dict}
            )
            result.validation_failure = validation_failure
            result_dict = result.model_dump(mode="json")
            if failure_brief:
                failure_brief = failure_brief.model_copy(
                    update={"validation_failure": validation_failure}
                )
                result.failure_brief = failure_brief
                result_dict["failure_brief"] = failure_brief.model_dump(mode="json")

        state.security_result = result_dict
        if failure_brief:
            state.retry_brief = failure_brief.model_dump(mode="json")
        if validation_failure:
            state.validation_failure = validation_failure.model_dump(mode="json")

        # The message is what the user reads on the card. "0 new findings" for a
        # scan that never ran is the same lie the score used to tell.
        message = (
            f"Security scan: {len(new_findings)} new findings"
            if scanners_run
            else "Security re-scan did not run — no scanner was available"
        )

        await self.emit_status(
            state,
            "completed",
            message,
            {
                "rejected": rejected,
                "security_score": security_score,
                "scanners_run": scanners_run,
                "source_roots": source_roots,
            },
        )
        return state

    async def _run_bandit(self, repo: Path, scan_targets: list[Path]) -> tuple[bool, list[dict]]:
        """`(executed, findings)`. Zero findings and *no scan* are not the same fact.

        Mirrors A3's `ScanOutcome` detection (`code == -1` is the runner's
        "could not execute"), deliberately without A3's stub fallback: a
        differential check against fabricated findings would compare a real scan
        to invented ones. The A3/A9 scanner duplication remains open as T5.
        """
        if not scan_targets:
            return False, []
        cmd = ["bandit", "-f", "json", "-q", "-x", bandit_exclude_arg()]
        for target in scan_targets:
            cmd.extend(["-r", str(target)])
        code, stdout, _ = await run_command(cmd, cwd=repo, timeout=60)
        if code == -1:
            return False, []
        data = parse_json_safe(stdout)
        # bandit exits 0 clean, 1 with issues; either way it emits a `results`
        # envelope. Its absence means the scan did not complete.
        if not isinstance(data, dict) or "results" not in data:
            return False, []
        return True, [
            {
                "tool": "bandit",
                "file": r.get("filename", "").replace(str(repo) + "/", ""),
                "line": r.get("line_number", 0),
                "message": r.get("issue_text", ""),
                "severity": _bandit_severity(r),
            }
            for r in data.get("results", [])
        ]

    async def _run_semgrep(self, repo: Path, scan_targets: list[Path]) -> tuple[bool, list[dict]]:
        if not scan_targets:
            return False, []
        cmd = ["semgrep", "--config=auto", "--json", *semgrep_exclude_args()]
        cmd.extend(str(t) for t in scan_targets)
        code, stdout, _ = await run_command(cmd, cwd=repo, timeout=90)
        if code == -1:
            return False, []
        data = parse_json_safe(stdout)
        if not isinstance(data, dict) or "results" not in data:
            return False, []
        return True, [
            {
                "tool": "semgrep",
                "file": r.get("path", "").replace(str(repo) + "/", ""),
                "line": r.get("start", {}).get("line", 0),
                "message": r.get("extra", {}).get("message", ""),
                "severity": _semgrep_severity(r),
            }
            for r in data.get("results", [])
        ]

    def _keyed(self, baseline: dict, tools: list[str]) -> list[tuple[FindingKey, dict]]:
        """A3's baseline, keyed per tool. `ruff` is style, not security — excluded."""
        return [
            (finding_key(finding, tool), finding)
            for tool in tools
            for finding in baseline.get(tool, []) or []
        ]
