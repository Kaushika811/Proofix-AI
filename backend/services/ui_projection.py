"""Project internal pipeline state onto the frontend's view models.

The UI never sees raw pipeline JSON. This module is the single translation
layer between `RunStateModel` / `AgentStatusEvent` and the typed models the
React app consumes (see `frontend/src/mocks/runReport.ts` and
`frontend/src/components/proofix/data.ts`).

Keeping the mapping here means agents stay free to change their internal
payloads without breaking the UI contract.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from backend.agents.a10_routing import (
    SCORE_THRESHOLD,
    SECURITY_TECHNICAL_THRESHOLD,
    citation_review_needed,
    gate_checks,
)
from backend.models.pr import AxisScores
from backend.orchestrator.trust_gating import draft_reasons
from backend.state.events import AgentStatusEvent
from backend.services.measurement import measured_mean, meets_threshold
from backend.services.python_ast_parser import parse_source
from backend.state.schema import RunStateModel

# ---------------------------------------------------------------- registry --


class StageDefinition(NamedTuple):
    """One workflow stage. Stages group agents into the story the UI tells."""

    id: str
    label: str
    order: int
    purpose: str


# The workflow the pipeline actually executes, grouped for presentation. Order
# is the order a run passes through them. `learning` runs outside the agent
# graph (the learning pipeline observes a run), so it owns no agents — an empty
# agent list is a fact about the stage, not a gap.
STAGE_REGISTRY: list[StageDefinition] = [
    StageDefinition(
        "repository",
        "Repository Understanding",
        1,
        "Build a structural and semantic model of the repository.",
    ),
    StageDefinition(
        "investigation",
        "Investigation",
        2,
        "Reproduce the failure and establish its cause with evidence.",
    ),
    StageDefinition(
        "context",
        "Context Engineering",
        3,
        "Reduce the repository to the minimal, privacy-checked repair context.",
    ),
    StageDefinition(
        "planning",
        "Repair Planning",
        4,
        "Order the repair work by dependency.",
    ),
    StageDefinition(
        "patch",
        "Repair Generation",
        5,
        "Generate repository-safe patches.",
    ),
    StageDefinition(
        "validation",
        "Validation",
        6,
        "Prove the repair works and decide whether it deserves merging.",
    ),
    StageDefinition(
        "learning",
        "Learning",
        7,
        "Fold the outcome into repository and organization memory.",
    ),
]

STAGE_BY_ID = {stage.id: stage for stage in STAGE_REGISTRY}

# Which surface an agent is published to.
#
# NOTE ON THE NAME: the frontend once called "Workspace V2" was cancelled and
# deleted. These two values are an **API contract** — the `surface` query
# parameter on `/agents` and `/stages` — and nothing here asks anyone to build
# a second frontend.
#
# **They now select the same thing.** The split existed because the product
# surface had no cards for A0.5 and A5.5, so publishing them would have added
# cards that never rendered. A0.5 gained one in Phase 2 and A5.5 in Phase 4, and
# with that the distinction is gone: every agent the graph executes has a card.
#
# The parameter is kept, accepted and validated, because it is published in the
# OpenAPI schema and a client may still send it — but it no longer changes the
# response, and `_V2_ONLY` is deleted rather than left as an empty category
# waiting to be repopulated. A registry test asserts the two surfaces stay
# identical, so a future entry cannot quietly resurrect the split.
SURFACE_V1 = "v1"
SURFACE_V2 = "v2"
_BOTH = frozenset({SURFACE_V1, SURFACE_V2})


class AgentDefinition(NamedTuple):
    """One agent as the UI knows it.

    A `NamedTuple` so existing positional unpacking of the first five fields
    keeps working while new consumers read by name.
    """

    card: str
    agent_id: str
    name: str
    purpose: str
    handoff: str
    stage: str
    surfaces: frozenset[str]


# The single source of truth for agent identity, ordering and stage membership.
# Nothing downstream — backend or frontend — may hardcode this mapping.
AGENT_REGISTRY: list[AgentDefinition] = [
    AgentDefinition(
        "environment",
        "A0.7",
        "Environment Precheck",
        "Establish whether this repository's tests can execute here.",
        "Environment Report",
        "repository",
        _BOTH,
    ),
    AgentDefinition(
        "intelligence",
        "A0.5",
        "Repository Indexing",
        "Index the repository into a reusable knowledge graph.",
        "Repository Index",
        "repository",
        _BOTH,
    ),
    AgentDefinition(
        "repo-intel",
        "A1",
        "Repository Intelligence",
        "Understand repository structure before any repair.",
        "Semantic Graph",
        "repository",
        _BOTH,
    ),
    AgentDefinition(
        "deps",
        "A2",
        "Dependency Analyzer",
        "Determine whether vulnerabilities are actually reachable.",
        "Reachability Map",
        "repository",
        _BOTH,
    ),
    AgentDefinition(
        "static",
        "A3",
        "Static Analysis",
        "Combine multiple analyzers into one prioritized report.",
        "Prioritized Findings",
        "repository",
        _BOTH,
    ),
    AgentDefinition(
        "reproduce",
        "A3.5",
        "Failure Reproduction",
        "Verify the reported bug actually exists.",
        "Runtime Evidence",
        "investigation",
        _BOTH,
    ),
    AgentDefinition(
        "root",
        "A4",
        "Root Cause Analysis",
        "Determine why the bug occurred, with cited evidence.",
        "Root Cause",
        "investigation",
        _BOTH,
    ),
    AgentDefinition(
        "blast",
        "A5",
        "Blast Radius",
        "Understand repository-wide impact of the change.",
        "Impact Set",
        "investigation",
        _BOTH,
    ),
    AgentDefinition(
        "context",
        "A5.5",
        "Context Engineering",
        "Reduce the repository to the minimal context a repair needs.",
        "Context Package",
        "context",
        _BOTH,
    ),
    AgentDefinition(
        "planner",
        "A6",
        "Repair Planner",
        "Create a dependency-aware repair order.",
        "Repair DAG",
        "planning",
        _BOTH,
    ),
    AgentDefinition(
        "patch",
        "A7",
        "Patch Generator",
        "Generate repository-safe patches.",
        "Patch Candidate",
        "patch",
        _BOTH,
    ),
    AgentDefinition(
        "mutation",
        "A8",
        "Mutation Validation",
        "Verify repair correctness beyond a passing test.",
        "Validation Report",
        "validation",
        _BOTH,
    ),
    AgentDefinition(
        "security",
        "A9",
        "Security Re-scan",
        "Ensure the patch introduced no new vulnerabilities.",
        "Security Delta",
        "validation",
        _BOTH,
    ),
    AgentDefinition(
        "merge",
        "A10",
        "Mergeability Assessment",
        "Determine whether the repair deserves merging.",
        "PR Decision",
        "validation",
        _BOTH,
    ),
]

CARD_BY_BACKEND_ID = {a.agent_id: a.card for a in AGENT_REGISTRY}
BACKEND_BY_CARD_ID = {a.card: a.agent_id for a in AGENT_REGISTRY}
AGENT_BY_CARD_ID = {a.card: a for a in AGENT_REGISTRY}
AGENT_BY_BACKEND_ID = {a.agent_id: a for a in AGENT_REGISTRY}


def agents_for_surface(surface: str = SURFACE_V1) -> list[AgentDefinition]:
    """Registry entries published to one surface, in pipeline order."""
    return [a for a in AGENT_REGISTRY if surface in a.surfaces]


def build_stage_registry(surface: str = SURFACE_V2) -> list[dict]:
    """The stage → agent structure the workspace navigates by.

    Published so no client hardcodes stage membership, ordering or labels. A
    stage with no agents on this surface still appears: `learning` runs outside
    the agent graph, and omitting it would misrepresent the workflow.
    """
    published = agents_for_surface(surface)
    return [
        {
            "id": stage.id,
            "label": stage.label,
            "order": stage.order,
            "purpose": stage.purpose,
            "agents": [
                {
                    "id": agent.card,
                    "agentId": agent.agent_id,
                    "name": agent.name,
                    "purpose": agent.purpose,
                    "handoff": agent.handoff,
                }
                for agent in published
                if agent.stage == stage.id
            ],
        }
        for stage in sorted(STAGE_REGISTRY, key=lambda s: s.order)
    ]


_SEVERITY_BY_SCORE = ((0.9, "CRITICAL"), (0.7, "HIGH"), (0.4, "MEDIUM"))


# ------------------------------------------------------------------ helpers --


def short_run_id(run_id: str) -> str:
    """Render a run id the way the UI header expects: `11b8…3a91`."""
    compact = run_id.replace("-", "")
    if len(compact) <= 8:
        return compact
    return f"{compact[:4]}…{compact[-4:]}"


def _fmt_duration(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest}s"


def _relative_time(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    delta = (datetime.utcnow() - moment).total_seconds()
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _pct(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return default


def _score(value: Any) -> float | None:
    """A score, or `None` when it was never measured.

    The nullable counterpart to `_pct`. `_pct` substitutes `0.0` for anything
    unparseable, which is right for a value that is genuinely zero and wrong for
    one that does not exist — a security re-scan that never ran was reported as
    scoring 0%, and that fabricated zero was averaged into the trust score.

    Use this for anything the pipeline may not have measured. `_pct` remains for
    values already coerced upstream (confidences, counts).
    """
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _score_text(value: Any) -> str:
    """A score for display, never inventing a number."""
    scored = _score(value)
    return "Not measured" if scored is None else f"{scored:g}"


def _tone(value: float | None) -> str:
    # "ok" is tied to the gate A10 actually applies, so a green ring never
    # disagrees with the router's own verdict on the same number.
    #
    # An unmeasured axis is deliberately neither "ok" nor "bad": it has not
    # scored well and it has not scored badly. Painting it red said the run
    # failed a check that never ran.
    if value is None:
        return "unknown"
    if value >= SCORE_THRESHOLD:
        return "ok"
    if value >= SCORE_THRESHOLD * 0.75:
        return "warn"
    return "bad"


def repo_branch(state: RunStateModel) -> str:
    """The branch actually checked out in the target repo.

    Every view model used to report "main" regardless of what the run operated
    on, which is wrong the moment anyone points ProoFix at a feature branch.
    Read from `.git/HEAD` directly — no subprocess, and it works on a bare path
    as well as a clone.
    """
    for candidate in (state.repo_clone_path, state.repo_path):
        if not candidate:
            continue
        try:
            head = (Path(candidate).expanduser() / ".git" / "HEAD").read_text().strip()
        except OSError:
            continue
        if head.startswith("ref: refs/heads/"):
            return head.split("refs/heads/", 1)[1]
        if head:
            # Detached HEAD — the commit is the most honest thing to show.
            return head[:7]
    return "—"


def repo_display_name(state: RunStateModel) -> str:
    raw = (state.repo_path or "").rstrip("/")
    if not raw:
        return "repository"
    tail = raw.split("/")[-1]
    return tail.replace(".git", "") or "repository"


#: B-B19. `state.environment.status` names the actual reason a run halted at
#: A0.7; collapsing all three blocking statuses into one label contradicted the
#: `reason` text printed directly beneath the badge.
_ENVIRONMENT_BLOCK_LABELS: dict[str, str] = {
    "unsupported": "Unsupported repository",
    "no_manifest": "No dependency manifest",
    "not_prepared": "Environment not prepared",
    "no_test_runner": "No test runner available",
}


def run_decision(state: RunStateModel) -> tuple[str, str]:
    """Return (decision, human label) in the UI's vocabulary."""
    if state.status == "failed":
        return "failed", "Failed"
    # A blocked run is not a failure or a PR routing outcome. It may have
    # stopped before reproduction or after rejecting a repair proposal.
    if state.status == "blocked":
        if state.recovery_blocked_reason:
            return "blocked", "Recovery stopped"
        env_status = (state.environment or {}).get("status")
        label = _ENVIRONMENT_BLOCK_LABELS.get(env_status, "Environment not prepared")
        return "blocked", label
    if state.issue_contract and not (state.patch_bundle or {}).get("patches") and state.pr_decision:
        return "draft", "No patch generated"
    decision = (state.pr_decision or {}).get("pr_type")
    if decision == "auto_mergeable":
        return "merge", "Auto Merge"
    if decision == "diff_only":
        return "draft", "Diff Only"
    if decision == "draft":
        return "draft", "Review required"
    return "draft", "Pending"


def sidebar_status(state: RunStateModel) -> str:
    if state.status == "failed":
        return "failed"
    if state.status == "blocked":
        return "blocked"
    if state.status in ("pending", "running", "validation_retry"):
        return "running"
    decision, _label = run_decision(state)
    if decision == "merge":
        return "completed"
    if decision == "failed":
        return "failed"
    return "draft"


def run_display_name(state: RunStateModel) -> str:
    """Name a run after what it actually repaired."""
    root = state.root_cause or {}
    summary = (root.get("root_cause") or root.get("summary") or "").strip()
    if summary:
        return summary.split(".")[0][:44] or "Repository Analysis"
    repro = state.reproduction or {}
    if repro.get("failing_test"):
        return str(repro["failing_test"]).split("::")[-1][:44]
    return "Repository Analysis"


def total_attempts(state: RunStateModel) -> int:
    """Patch attempts made: the initial one plus every retry."""
    return state.retry_count + 1


def elapsed_seconds(state: RunStateModel, events: list[AgentStatusEvent]) -> float:
    if not events:
        return 0.0
    start = min(e.timestamp for e in events)
    end = max(e.timestamp for e in events)
    return max(0.0, (end - start).total_seconds())


# ------------------------------------------------------------- agent cards --


def _event_index(events: list[AgentStatusEvent]) -> dict[str, list[AgentStatusEvent]]:
    grouped: dict[str, list[AgentStatusEvent]] = {}
    for event in events:
        grouped.setdefault(event.agent_id, []).append(event)
    return grouped


# `blocked` is terminal: the run is over and nothing further will be emitted.
# Omitting it left downstream stages rendering as still-running forever.
RUN_TERMINAL_STATES = ("completed", "failed", "blocked")


def _agent_status(events_for_agent: list[AgentStatusEvent], state: RunStateModel, card: str) -> str:
    if not events_for_agent:
        # A finished run that never reached this agent routed around it — e.g.
        # the security re-scan is skipped when mutation validation fails.
        return "skipped" if state.status in RUN_TERMINAL_STATES else "running"
    latest = events_for_agent[-1]
    if latest.status == "failed":
        return "failed"
    if latest.status != "completed":
        # A terminal run cannot still be running an agent. A0.5 makes this
        # reachable in practice: its failure is caught so the pipeline can
        # continue, so it emits `started` and never a terminal event, and the
        # card sat on "running" forever after the run had finished.
        if state.status in RUN_TERMINAL_STATES:
            agent_id = BACKEND_BY_CARD_ID.get(card)
            errored = any(e.get("agent") == agent_id for e in state.errors)
            return "failed" if errored else "skipped"
        return "running"

    if card == "mutation":
        mutation = state.mutation_result or {}
        if mutation.get("patch_retry_required") or mutation.get("mutant_survived"):
            return "retry"
        if mutation.get("pytest_passed") is False:
            return "failed"
    if card == "security" and (state.security_result or {}).get("rejected"):
        return "failed"
    if card == "merge":
        decision, _label = run_decision(state)
        return "draft" if decision == "draft" else "completed"
    return "completed"


def _agent_duration(events_for_agent: list[AgentStatusEvent]) -> str:
    if len(events_for_agent) < 2:
        return "—"
    span = (events_for_agent[-1].timestamp - events_for_agent[0].timestamp).total_seconds()
    return _fmt_duration(span) if span > 0 else "—"


def _lines_for(card: str, state: RunStateModel, events_for_agent: list[AgentStatusEvent]) -> list[str]:
    """Narrate what the agent did, from its own emitted messages plus state."""
    lines = [e.message for e in events_for_agent if e.message]
    if card == "context":
        # The completed-event summary ("Context: N function(s) from M
        # file(s), X% token reduction") repeats, in prose, exactly what the
        # funnel panel (`ContextEngineeringPanel`) already shows visually —
        # real candidate/extracted/function counts, named files and
        # functions, and token reduction in its own labelled section.
        # Filtered here only, not at the source: the event itself still
        # carries the message for `/events` and the audit trail, this only
        # keeps it out of the duplicated card-level narration.
        lines = [line for line in lines if not line.startswith("Context: ")]
    detail = _narrative_detail(card, state)
    if card == "intelligence":
        # A0.5 publishes nothing to run state, so its detail comes from the
        # event payload rather than from `state` like every other card.
        detail = _intelligence_detail(_latest_payload(events_for_agent, "repository_intelligence"))
    if lines or detail:
        return lines + detail
    if state.status in RUN_TERMINAL_STATES:
        return ["Skipped — the pipeline routed around this stage."]
    return ["Waiting to start…"]


def _intelligence_detail(published: dict) -> list[str]:
    """What A0.5's index means, in the terms the layer exists for.

    The agent's own message already reports the counts; this says whether the
    work was reused, and whether anything was remembered from earlier runs —
    the two facts that distinguish this layer from A1 re-indexing the same
    files.
    """
    if not published:
        return []

    _mode, detail = _index_mode(published)
    lines = [detail]

    remembered = published.get("repair_memory_entries", 0)
    if remembered:
        lines.append(
            f"{remembered} repair(s) remembered from previous runs on this repository."
        )
    else:
        lines.append("No previous repairs remembered for this repository.")

    commits = published.get("git_commits_indexed", 0)
    if commits:
        lines.append(f"{commits} commit(s) of history indexed for ownership and churn.")
    return lines


def _narrative_detail(card: str, state: RunStateModel) -> list[str]:
    if card == "environment":
        # The probe's own words. A blocked run's entire explanation is this
        # `reason`, so it is rendered verbatim rather than re-composed here.
        env = state.environment or {}
        if not env:
            return []
        detail = [f"Language: {env.get('language') or 'undetermined'}."]
        runner = env.get("test_runner")
        if runner:
            available = env.get("test_runner_available")
            detail.append(
                f"Test runner {runner} "
                + ("is available." if available else "is not available.")
            )
        reason = env.get("reason")
        if reason:
            detail.append(str(reason))
        command = env.get("suggested_command")
        if command:
            detail.append(f"Suggested command: {command}")
        return detail

    if card == "repo-intel":
        sig = state.sig or {}
        files = sig.get("files") or {}
        roots = ", ".join(sig.get("source_roots") or []) or "repository root"
        return [
            f"Discovered source roots: {roots}.",
            f"Indexed {len(files)} production files.",
            f"Mapped {len(sig.get('edges') or [])} import edges.",
        ] if files else []

    if card == "deps":
        cve = state.cve_report or {}
        findings = cve.get("findings") or []
        critical = cve.get("critical_queue") or []
        unknown = [f for f in findings if f.get("classification") == "Unknown"]
        return [
            f"Screened {len(findings)} advisories against the import graph.",
            f"{len(critical)} reachable, {len(unknown)} undetermined.",
        ] if findings else ["No vulnerable dependencies detected."]

    if card == "static":
        static = state.static_report or {}
        prioritized = static.get("prioritized") or []
        return [
            f"Collapsed {static.get('raw_count', 0)} raw findings to {len(prioritized)} prioritized.",
        ] if static else []

    if card == "reproduce":
        repro = state.reproduction or {}
        status = repro.get("status")
        if status == "CONFIRMED":
            return [
                f"Failing test: {repro.get('failing_test')}",
                f"{repro.get('exception_type') or 'Failure'}: {repro.get('exception_message') or 'assertion failed'}",
                "Runtime evidence captured.",
            ]
        if status:
            return [f"Reproduction not confirmed ({status}). Repair will be routed for manual review."]
        return []

    if card == "root":
        root = state.root_cause or {}
        citations = root.get("citations") or []
        verified = [c for c in citations if c.get("verified")]
        out = []
        if root.get("root_cause"):
            out.append(str(root["root_cause"])[:220])
        if citations:
            out.append(f"{len(verified)}/{len(citations)} source locations found; diagnosis still requires review.")
        return out

    if card == "blast":
        blast = state.blast_graph or {}
        scope = blast.get("auto_patch_scope") or []
        review = blast.get("human_review_required") or []
        return [
            f"{len(scope)} files auto-patchable, {len(review)} need human review.",
        ] if blast else []

    if card == "planner":
        dag = state.fix_dag or {}
        order = dag.get("execution_order") or []
        return [
            f"Sequenced {len(order)} repair steps across {len(dag.get('dependency_edges') or [])} dependency edges.",
        ] if order else []

    if card == "patch":
        bundle = state.patch_bundle or {}
        patches = bundle.get("patches") or []
        return [f"Modified {p.get('file')}" for p in patches] if patches else []

    if card == "mutation":
        mutation = state.mutation_result or {}
        out = []
        if mutation.get("target_test_passed") is not None:
            out.append(
                "Target test passes after patch."
                if mutation["target_test_passed"]
                else "Target test still failing after patch."
            )
        if mutation.get("new_failures"):
            out.append(f"{len(mutation['new_failures'])} new regressions introduced.")
        if mutation.get("mutant_survived"):
            out.append("A mutant survived — the test passes without validating the fix.")
        return out

    if card == "security":
        security = state.security_result or {}
        new_findings = security.get("new_findings") or []
        if not security:
            return []
        # A9 having produced a result is not the same as A9 having measured
        # one. With no scanner available it finds zero findings by definition,
        # and "no new findings" would read as a clean bill of health.
        if security.get("security_score") is None:
            return ["No scanner was available, so the patch was not re-scanned."]
        return [
            f"{len(new_findings)} new findings versus the pre-patch baseline."
            if new_findings
            else "No new findings versus the pre-patch baseline."
        ]

    if card == "merge":
        decision, label = run_decision(state)
        note = (state.pr_decision or {}).get("review_note")
        out = [f"Routed as {label}."]
        if note:
            out.append(str(note))
        return out

    return []


def _latest_payload(events_for_agent: list[AgentStatusEvent], key: str) -> dict:
    """The most recent payload section an agent published, or an empty dict.

    A0.5 and A5.5 publish their measurements on the event rather than onto
    `RunStateModel`, so their cards read from the timeline. Absent means the
    agent has not reached that point — never a substituted default.
    """
    for event in reversed(events_for_agent):
        section = (event.payload or {}).get(key)
        if isinstance(section, dict):
            return section
    return {}


def _a7_patch_metrics_by_file(events_for_agent: list[AgentStatusEvent]) -> dict[str, dict]:
    """A7's own per-file generation provenance, keyed by the file it patched.

    `a7_patch_metrics` is a single dict when A7 attempted one plan and a list
    when it attempted several (`a7_code_generation.py`); this normalizes both
    and keys on `file`, the one field that ties an attempt back to a specific
    `PatchCandidate` in the bundle. Reads only the most recent completed A7
    event — a retry regenerates the whole bundle, so an earlier event's
    provenance no longer describes what is on `state.patch_bundle` now.
    """
    for event in reversed(events_for_agent):
        if event.status != "completed":
            continue
        raw = (event.payload or {}).get("a7_patch_metrics")
        if not raw:
            continue
        entries = raw if isinstance(raw, list) else [raw]
        return {
            m["file"]: m for m in entries if isinstance(m, dict) and m.get("file")
        }
    return {}


def _metrics_for(
    card: str,
    state: RunStateModel,
    duration: str,
    events_for_agent: list[AgentStatusEvent] | None = None,
) -> list[dict] | None:
    def metric(label: str, value: Any) -> dict:
        return {"label": label, "value": str(value)}

    events_for_agent = events_for_agent or []

    if card == "environment":
        env = state.environment or {}
        if not env:
            return [metric("Duration", duration)]
        tests_collected = env.get("tests_collected")
        return [
            metric("Environment", env.get("status") or "unknown"),
            metric("Language", env.get("language") or "—"),
            metric("Test Runner", env.get("test_runner") or "—"),
            metric("Blocking", "yes" if env.get("blocking") else "no"),
            metric("Tests Collected", "not measured" if tests_collected is None else tests_collected),
        ]

    if card == "intelligence":
        published = _latest_payload(events_for_agent, "repository_intelligence")
        if not published:
            return [metric("Duration", duration)]
        cache = published.get("cache") or {}
        return [
            metric("Graph Nodes", published.get("repository_nodes", 0)),
            metric("Graph Edges", published.get("repository_edges", 0)),
            metric("Callables", published.get("call_graph_nodes", 0)),
            metric("Commits Indexed", published.get("git_commits_indexed", 0)),
            metric("Remembered Repairs", published.get("repair_memory_entries", 0)),
            metric("Cache Hits", cache.get("hits", published.get("cache_hits", 0))),
            metric("Index Build", _fmt_duration((published.get("total_ms") or 0) / 1000)),
            metric("Duration", duration),
        ]

    if card == "context":
        # `ContextEngineeringPanel` (`GET /api/runs/{id}/context`) already
        # shows every one of these numbers — files ranked, extracted,
        # context functions, token reduction, redactions, privacy status —
        # more prominently than this generic grid ever did. `None` (not an
        # empty list) so `AgentCard` omits the "Supporting metrics" section
        # entirely rather than rendering it with nothing to say.
        return None

    if card == "repo-intel":
        sig = state.sig or {}
        files = sig.get("files") or {}
        return [
            metric("Files", len(files)),
            metric("Import Edges", len(sig.get("edges") or [])),
            metric("Source Roots", len(sig.get("source_roots") or [])),
            metric("Duration", duration),
        ]
    if card == "deps":
        cve = state.cve_report or {}
        findings = cve.get("findings") or []
        return [
            metric("Advisories", len(findings)),
            metric("Reachable", len(cve.get("critical_queue") or [])),
            metric("Unknown", sum(1 for f in findings if f.get("classification") == "Unknown")),
            metric("Duration", duration),
        ]
    if card == "static":
        static = state.static_report or {}
        prioritized = static.get("prioritized") or []
        return [
            metric("Raw Findings", static.get("raw_count", 0)),
            metric("Prioritized", len(prioritized)),
            metric("Consensus", sum(1 for f in prioritized if f.get("consensus"))),
            metric("Duration", duration),
        ]
    if card == "reproduce":
        repro = state.reproduction or {}
        return [
            metric("Status", repro.get("status", "—")),
            metric("Confidence", f"{_pct((repro.get('confidence') or 0) * 100)}%"),
            metric("Baseline Failures", len(repro.get("pre_existing_failures") or [])),
            metric("Duration", duration),
        ]
    if card == "root":
        root = state.root_cause or {}
        citations = root.get("citations") or []
        return [
            metric("Citations", len(citations)),
            metric("Verified", sum(1 for c in citations if c.get("verified"))),
            metric("Confidence", f"{_pct((root.get('confidence') or 0) * 100)}%"),
            metric("Duration", duration),
        ]
    if card == "blast":
        blast = state.blast_graph or {}
        return [
            metric("In Scope", len(blast.get("scope") or [])),
            metric("Auto-patchable", len(blast.get("auto_patch_scope") or [])),
            metric("Human Review", len(blast.get("human_review_required") or [])),
            metric("Duration", duration),
        ]
    if card == "planner":
        dag = state.fix_dag or {}
        return [
            metric("Steps", len(dag.get("execution_order") or [])),
            metric("Dependencies", len(dag.get("dependency_edges") or [])),
            metric("Conflicts", len(dag.get("conflict_batches") or [])),
            metric("Duration", duration),
        ]
    if card == "patch":
        bundle = state.patch_bundle or {}
        patches = bundle.get("patches") or []
        diff = bundle.get("diff_text") or ""
        added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        return [
            metric("Files Patched", len(patches)),
            metric("Lines Added", added),
            metric("Lines Removed", removed),
            metric("Duration", duration),
        ]
    if card == "mutation":
        mutation = state.mutation_result or {}
        score = mutation.get("mutation_score")
        return [
            metric("Correctness", _score_text(mutation.get("correctness_score"))),
            metric("Mutation Score", "—" if score is None else f"{float(score):.2f}"),
            metric("New Failures", len(mutation.get("new_failures") or [])),
            metric("Duration", duration),
        ]
    if card == "security":
        security = state.security_result or {}
        return [
            metric("Security Score", _score_text(security.get("security_score"))),
            metric("New Findings", len(security.get("new_findings") or [])),
            metric("Rejected", "yes" if security.get("rejected") else "no"),
            metric("Duration", duration),
        ]
    if card == "merge":
        axis = (state.pr_decision or {}).get("axis_scores") or {}
        _decision, label = run_decision(state)
        return [
            metric("Correctness", _score_text(axis.get("correctness"))),
            metric("Security", _score_text(axis.get("security"))),
            metric("Fidelity", _score_text(axis.get("fidelity"))),
            metric("Decision", label),
        ]
    return [metric("Duration", duration)]


def _evidence_for(
    card: str,
    state: RunStateModel,
    events_for_agent: list[AgentStatusEvent] | None = None,
) -> dict:
    def payload(title: str, subtitle: str, fields: list[dict], pills: list[str] | None = None) -> dict:
        out: dict[str, Any] = {"title": title, "subtitle": subtitle, "fields": fields}
        if pills:
            out["pills"] = pills
        return out

    def field(label: str, value: Any, mono: bool = False) -> dict:
        return {"label": label, "value": str(value), "mono": mono}

    events_for_agent = events_for_agent or []

    if card == "environment":
        env = state.environment or {}
        if not env:
            return payload("Environment Report", "The precheck did not run for this run.", [])
        manifests = [
            str(m.get("path")) for m in (env.get("manifests") or []) if m.get("path")
        ]
        tests_collected = env.get("tests_collected")
        return payload(
            "Environment Report",
            "Whether this repository's tests can execute here.",
            [
                field("Status", env.get("status") or "unknown", True),
                field("Language", env.get("language") or "—", True),
                field("Test runner", env.get("test_runner") or "—", True),
                field(
                    "Test runner available",
                    "yes" if env.get("test_runner_available") else "no",
                    True,
                ),
                field(
                    "Tests collected",
                    "not measured" if tests_collected is None else tests_collected,
                    True,
                ),
                field(
                    "Missing imports",
                    ", ".join(env.get("missing_imports") or []) or "none",
                ),
                field("Reason", str(env.get("reason") or "—")),
                field("Suggested command", str(env.get("suggested_command") or "—"), True),
            ],
            manifests or None,
        )

    if card == "intelligence":
        published = _latest_payload(events_for_agent, "repository_intelligence")
        graph = _latest_payload(events_for_agent, "knowledge_graph")
        if not published:
            return payload("Repository Index", "Not yet published by this run.", [])
        fields = [
            field("Repository id", published.get("repository_id", "—"), True),
            field("Graph nodes", published.get("repository_nodes", 0), True),
            field("Graph edges", published.get("repository_edges", 0), True),
            field("Documented symbols", published.get("documentation_entries", 0), True),
        ]
        if graph:
            fields.append(field("Knowledge graph nodes", graph.get("node_count", 0), True))
        return payload(
            "Repository Index",
            "Reusable structural model shared across runs.",
            fields,
            [c.get("name", "") for c in (graph.get("capabilities") or [])[:6] if c.get("name")],
        )

    if card == "context":
        published = _latest_payload(events_for_agent, "context_engineering")
        if not published:
            return payload("Context Package", "Not yet published by this run.", [])
        if published.get("skipped"):
            return payload(
                "Context Package",
                "No repair target resolved — the generator kept its own context path.",
                [field("Skipped", published.get("skipped"), True)],
            )
        reduction = published.get("token_reduction")
        return payload(
            "Context Package",
            "Minimal, privacy-checked context handed to the patch generator.",
            [
                field("Target file", published.get("target_file") or "—", True),
                field("Target function", published.get("target_function") or "—", True),
                field("Context functions", published.get("context_functions", 0), True),
                field("Original tokens", published.get("original_tokens", 0), True),
                field("Reduced tokens", published.get("reduced_tokens", 0), True),
                field(
                    "Token reduction",
                    f"{float(reduction) * 100:.0f}%" if reduction is not None else "—",
                    True,
                ),
                field("Privacy guard", published.get("privacy_guard_status", "—"), True),
                field("Redactions", published.get("privacy_redactions", 0), True),
            ],
        )

    if card == "repo-intel":
        sig = state.sig or {}
        files = sig.get("files") or {}
        roles: dict[str, int] = {}
        for node in files.values():
            roles[node.get("role", "internal-util")] = roles.get(node.get("role", "internal-util"), 0) + 1
        return payload(
            "Semantic Intent Graph",
            "Static map of intent across the repository.",
            [
                field("Files indexed", len(files), True),
                field("Import edges", len(sig.get("edges") or []), True),
                field("Auth boundaries", roles.get("auth-boundary", 0), True),
                field("Public APIs", roles.get("public-api", 0), True),
            ],
            list(sig.get("source_roots") or [])[:6],
        )

    if card == "deps":
        cve = state.cve_report or {}
        findings = cve.get("findings") or []
        return payload(
            "Reachability Report",
            "Vulnerability surface narrowed by import-graph analysis.",
            [
                field("Advisories", len(findings), True),
                field("Reachable", len(cve.get("critical_queue") or []), True),
                field("Informational", sum(1 for f in findings if f.get("classification") == "Informational"), True),
                field("Unknown", sum(1 for f in findings if f.get("classification") == "Unknown"), True),
            ],
            [f"{f.get('package')}" for f in findings[:6] if f.get("package")],
        )

    if card == "static":
        static = state.static_report or {}
        prioritized = static.get("prioritized") or []
        return payload(
            "Prioritized Findings",
            "Consensus-ranked across bandit, semgrep and ruff.",
            [
                field("Raw findings", static.get("raw_count", 0), True),
                field("Prioritized", len(prioritized), True),
                field("Multi-tool consensus", sum(1 for f in prioritized if f.get("consensus")), True),
                field(
                    "Top severity",
                    f"{prioritized[0].get('severity', 0):.2f}" if prioritized else "—",
                    True,
                ),
            ],
            [f"{f.get('file')}:{f.get('line')}" for f in prioritized[:5]],
        )

    if card == "reproduce":
        repro = state.reproduction or {}
        return payload(
            "Runtime Evidence",
            "Proof the bug exists before any repair is attempted.",
            [
                field("Failing test", repro.get("failing_test") or "—", True),
                field("Exception", repro.get("exception_type") or "—", True),
                field("Location", f"{repro.get('failing_file') or '—'}:{repro.get('failing_line') or ''}", True),
                field("Confidence", f"{_pct((repro.get('confidence') or 0) * 100)}%", True),
            ],
        )

    if card == "root":
        root = state.root_cause or {}
        citations = root.get("citations") or []
        return payload(
            "Root Cause Brief",
            "Source locations checked; claim support reported separately.",
            [
                field("Summary", str(root.get("summary") or "—")[:120]),
                field("Citations", len(citations), True),
                field("Located", sum(1 for c in citations if c.get("verified")), True),
                field("Confidence", f"{_pct((root.get('confidence') or 0) * 100)}%", True),
            ],
            [f"{c.get('file')}:{c.get('line')}" for c in citations[:5]],
        )

    if card == "blast":
        blast = state.blast_graph or {}
        return payload(
            "Impact Set",
            "Files reachable from the resolved origin.",
            [
                field("Origins", ", ".join(blast.get("origins") or []) or "—", True),
                field("In scope", len(blast.get("scope") or []), True),
                field("Auto-patchable", len(blast.get("auto_patch_scope") or []), True),
                field("Human review", len(blast.get("human_review_required") or []), True),
            ],
            list(blast.get("auto_patch_scope") or [])[:6],
        )

    if card == "planner":
        dag = state.fix_dag or {}
        edges = dag.get("dependency_edges") or []
        batches = dag.get("conflict_batches") or []
        # A6 records *why* it drew each edge. Reporting only the count made the
        # ordering unreviewable — a number cannot be disagreed with, and the
        # reasons were sitting in state with no consumer but `/plan`.
        why = [
            f"{e.get('from_issue')} → {e.get('to_issue')}: {e.get('reason')}"
            for e in edges[:4]
            if e.get("reason")
        ]
        return payload(
            "Repair DAG",
            "Dependency-ordered repair sequence.",
            [
                field("Steps", len(dag.get("execution_order") or []), True),
                field("Dependency edges", len(edges), True),
                field("Conflict batches", len(batches), True),
                field("First step", (dag.get("execution_order") or ["—"])[0], True),
                # Conflict batches are fixes that touch the same file and cannot
                # be applied independently — the reason a plan is a DAG rather
                # than a list. Named, not counted.
                field(
                    "Conflicting fixes",
                    "; ".join(", ".join(b) for b in batches[:2]) if batches else "none",
                ),
                *([field("Ordering", " · ".join(why))] if why else []),
            ],
            list(dag.get("execution_order") or [])[:6],
        )

    if card == "patch":
        bundle = state.patch_bundle or {}
        patches = bundle.get("patches") or []
        contracts = bundle.get("contracts") or []
        return payload(
            "Patch Candidate",
            "AST-validated edits with behavioural contracts.",
            [
                field("Files", len(patches), True),
                field("Method", patches[0].get("method") if patches else "—", True),
                field("Contract", str(contracts[0].get("assertion"))[:110] if contracts else "—"),
                field("Style exemplar", str(bundle.get("style_exemplar_commit") or "—")[:12], True),
            ],
            [p.get("file") for p in patches if p.get("file")],
        )

    if card == "mutation":
        mutation = state.mutation_result or {}
        failure = mutation.get("validation_failure") or {}
        return payload(
            "Validation Report",
            "Scoped test run plus mutation gauntlet.",
            [
                field("Target test", _tristate(mutation.get("target_test_passed"), "pass", "fail"), True),
                field(
                    "Regressions",
                    _tristate(mutation.get("regression_tests_passed"), "none", "detected"),
                    True,
                ),
                field("Mutant survived", _tristate(mutation.get("mutant_survived"), "yes", "no"), True),
                field("Assertion", str(failure.get("assertion_message") or "—")[:110]),
            ],
            list(mutation.get("new_failures") or [])[:5],
        )

    if card == "security":
        security = state.security_result or {}
        new_findings = security.get("new_findings") or []
        return payload(
            "Security Delta",
            "Post-patch scan compared against the pre-patch baseline.",
            [
                field("Security score", _score_text(security.get("security_score")), True),
                field("New findings", len(new_findings), True),
                field("Verdict", "rejected" if security.get("rejected") else "accepted", True),
                field("Top finding", str(new_findings[0].get("message"))[:100] if new_findings else "—"),
            ],
            [f"{f.get('file')}:{f.get('line')}" for f in new_findings[:5]],
        )

    if card == "merge":
        decision_data = state.pr_decision or {}
        axis = decision_data.get("axis_scores") or {}
        proof = state.proof_bundle or {}
        _decision, label = run_decision(state)
        return payload(
            "Mergeability Decision",
            "Trust axes plus the proof bundle shipped with the PR.",
            [
                field("Decision", label, True),
                field("Scope risk", _score_text(axis.get("scope_risk")), True),
                field("Proof bundle", str(proof.get("bundle_hash") or "—")[:20], True),
                field("Review note", str(decision_data.get("review_note") or "—")[:110]),
            ],
        )

    return payload("Evidence", "No evidence captured for this stage.", [])


#: Index phases as A0.5 times them, in the order they run.
_INDEX_PHASES = (
    ("Graph", "graph_build_ms"),
    ("Call graph", "call_graph_ms"),
    ("Ownership", "ownership_ms"),
    ("History", "history_ms"),
    ("Docs", "documentation_ms"),
)


def _index_mode(published: dict) -> tuple[str, str]:
    """How A0.5 obtained this index, and the detail behind it.

    Mirrors `RepositoryIntelligenceAgent._summary` precedence deliberately: a
    cache hit outranks an incremental update, which outranks a full rebuild. The
    mode *is* the story of this card — a layer that exists to reuse work across
    runs is only interesting if you can see whether it reused any.
    """
    if published.get("cache_hits"):
        return "cache hit", "Index reused from a previous run."
    if published.get("incremental_updates"):
        changed = (
            f"+{published.get('files_added', 0)} added, "
            f"~{published.get('files_modified', 0)} modified, "
            f"-{published.get('files_deleted', 0)} deleted, "
            f"→{published.get('files_renamed', 0)} renamed"
        )
        return "incremental", f"Only the changed files were re-indexed: {changed}."
    return "full rebuild", "No usable prior index — the repository was indexed from scratch."


def _visualization_for(
    card: str,
    state: RunStateModel,
    events_for_agent: list[AgentStatusEvent] | None = None,
) -> dict | None:
    """Build the typed visualization payloads the UI renders per agent.

    Most agents write their artifact to `RunStateModel` and are projected from
    there. A0.5 deliberately does not — it never mutates run state, and its
    index lives in the cross-run cache — so its numbers are only ever in its own
    emitted event, which is why this takes the agent's events too.
    """
    events_for_agent = events_for_agent or []

    if card == "context":
        published = _latest_payload(events_for_agent, "context_engineering")
        if not published:
            return None
        if published.get("skipped"):
            # A5.5 ran, resolved no target, and handed A7 nothing. There is no
            # package to draw, and drawing an empty one would claim a reduction
            # that never happened.
            return {
                "kind": "context",
                "data": {
                    "skipped": str(published.get("skipped")),
                    "targetFile": "",
                    "targetFunction": None,
                    "originalTokens": 0,
                    "reducedTokens": 0,
                    "tokenReduction": None,
                    "contextFiles": 0,
                    "contextFunctions": 0,
                    "filesRanked": 0,
                    "privacyGuardStatus": published.get("privacy_guard_status") or "clean",
                    "redactions": 0,
                    "degraded": False,
                },
            }
        reduction = published.get("token_reduction")
        return {
            "kind": "context",
            "data": {
                "skipped": None,
                "targetFile": published.get("target_file") or "",
                "targetFunction": published.get("target_function"),
                "originalTokens": published.get("original_tokens", 0),
                "reducedTokens": published.get("reduced_tokens", 0),
                # Null rather than 0: a package that measured no reduction and
                # one that never measured are different claims.
                "tokenReduction": float(reduction) if reduction is not None else None,
                "contextFiles": published.get("context_files", 0),
                "contextFunctions": published.get("context_functions", 0),
                "filesRanked": published.get("files_ranked", 0),
                # The privacy boundary. `masked` means secrets were found and
                # replaced before the prompt left the process; `failed` means the
                # guard itself errored and nothing may be assumed about what got
                # through. Neither is "clean", and the UI must not round them to it.
                "privacyGuardStatus": published.get("privacy_guard_status") or "clean",
                "redactions": published.get("privacy_redactions", 0),
                "degraded": bool(published.get("degraded")),
            },
        }

    if card == "intelligence":
        published = _latest_payload(events_for_agent, "repository_intelligence")
        if not published:
            return None
        graph = _latest_payload(events_for_agent, "knowledge_graph")
        mode, mode_detail = _index_mode(published)
        return {
            "kind": "intelligence",
            "data": {
                "mode": mode,
                "modeDetail": mode_detail,
                # Only phases that took measurable time. A zero-millisecond
                # segment is not a phase that ran fast, it is a phase this
                # index did not need, and drawing it as a sliver implies work.
                "phases": [
                    {"label": label, "ms": published.get(key) or 0}
                    for label, key in _INDEX_PHASES
                    if (published.get(key) or 0) > 0
                ],
                "totalMs": published.get("total_ms") or 0,
                "metrics": {
                    "nodes": published.get("repository_nodes", 0),
                    "edges": published.get("repository_edges", 0),
                    "callables": published.get("call_graph_nodes", 0),
                    "commits": published.get("git_commits_indexed", 0),
                    "rememberedRepairs": published.get("repair_memory_entries", 0),
                },
                "capabilities": [
                    c.get("name", "")
                    for c in (graph.get("capabilities") or [])[:6]
                    if c.get("name")
                ],
            },
        }

    if card == "repo-intel":
        sig = state.sig or {}
        files = sig.get("files") or {}
        if not files:
            return None
        top = sorted(files.items(), key=lambda kv: -(kv[1].get("criticality") or 0))[:6]
        return {
            "kind": "repo-intel",
            "data": {
                # `ast` was `imports * 24 + 40` and `astNodes` was `imports * 24`
                # — invented magnitudes presented as a measured node count. The
                # SIG records imports, so report imports and nothing more.
                "files": [
                    {"name": path.split("/")[-1], "ast": len(node.get("imports") or [])}
                    for path, node in top
                ],
                "graphNodes": len(files),
                "metrics": {
                    "files": len(files),
                    "imports": sum(len(n.get("imports") or []) for n in files.values()),
                    "dependencies": len(sig.get("edges") or []),
                    "semanticRoles": len({n.get("role") for n in files.values()}),
                },
            },
        }

    if card == "deps":
        cve = state.cve_report or {}
        findings = cve.get("findings") or []
        if not findings:
            return None
        reachable = [f for f in findings if f.get("classification") == "Critical"]
        unreachable = [f for f in findings if f.get("classification") != "Critical"]
        return {
            "kind": "deps",
            "data": {
                "path": [
                    {"name": f.get("package", "?"), "sub": f.get("cve_id", "")} for f in reachable[:4]
                ],
                "unreachable": [{"name": f.get("package", "?")} for f in unreachable[:5]],
                "metrics": {
                    "reachable": len(reachable),
                    "deadFindings": len(unreachable),
                    "attackPaths": len(cve.get("critical_queue") or []),
                },
            },
        }

    if card == "static":
        static = state.static_report or {}
        prioritized = static.get("prioritized") or []
        if not prioritized:
            return None

        def severity_band(score: float) -> str:
            if score >= 0.7:
                return "HIGH"
            if score >= 0.4:
                return "MEDIUM"
            return "LOW"

        return {
            "kind": "static",
            "data": {
                # No `or ["bandit"]` fallback: naming a scanner that did not run
                # is worse than showing none.
                "scanners": sorted({t for f in prioritized for t in (f.get("tools") or [])}),
                "findings": [
                    {
                        "sev": severity_band(float(f.get("severity") or 0)),
                        "text": f"{f.get('message', 'finding')} ({f.get('file')}:{f.get('line')})"[:90],
                        "at": idx,
                    }
                    for idx, f in enumerate(prioritized[:6])
                ],
                "metrics": {
                    "raw": static.get("raw_count", 0),
                    "deduped": len(prioritized),
                    "prioritized": len(prioritized),
                },
            },
        }

    if card == "reproduce":
        repro = state.reproduction or {}
        if not repro.get("status"):
            return None
        failing = repro.get("failing_test") or "test suite"
        baseline = repro.get("pre_existing_failures") or []
        tests = [{"name": failing, "result": "FAIL"}]
        tests += [{"name": t, "result": "FAIL"} for t in baseline[:3] if t != failing]
        stack = [ln for ln in str(repro.get("traceback") or "").splitlines() if ln.strip()][-6:]
        return {
            "kind": "reproduce",
            "data": {
                # No invented fallbacks: showing a command the run never issued,
                # or an assertion it never raised, makes the card fiction.
                "command": repro.get("reexecution_command") or "—",
                "tests": tests,
                "failure": {
                    "name": failing,
                    "assertion": str(repro.get("exception_message") or "—")[:140],
                    "expected": "pass",
                    "actual": repro.get("exception_type") or "—",
                    "stack": stack,
                },
                "successMessage": (
                    "Failure reproduced — runtime evidence captured."
                    if repro.get("status") == "CONFIRMED"
                    else f"Reproduction {repro.get('status')} — routed for manual verification."
                ),
            },
        }

    if card == "root":
        root = state.root_cause or {}
        citations = root.get("citations") or []
        if not citations:
            return None
        return {
            "kind": "root",
            "data": {
                "lines": [
                    {"code": f"{c.get('file')}:{c.get('line')}", "probe": str(c.get("claim") or "")[:70]}
                    for c in citations[:5]
                ],
                "bugMessage": str(root.get("root_cause") or root.get("summary") or "Root cause identified")[:160],
                "evidence": [
                    {
                        "n": idx + 1,
                        "title": str(ref.get("source", "evidence")).replace("_", " ").title(),
                        "detail": str(ref.get("claim") or "")[:90],
                        "conf": round(float(ref.get("weight") or 0) * 100),
                    }
                    for idx, ref in enumerate((root.get("evidence_refs") or [])[:4])
                ],
            },
        }

    if card == "blast":
        blast = state.blast_graph or {}
        scope = blast.get("scope") or []
        if not scope:
            return None
        origins = blast.get("origins") or []
        return {
            "kind": "blast",
            "data": {
                "source": origins[0] if origins else "origin",
                "modules": [
                    {"name": s.get("path", "?"), "hitAt": int(s.get("hop_count") or 0)} for s in scope[:8]
                ],
            },
        }

    if card == "planner":
        dag = state.fix_dag or {}
        order = dag.get("execution_order") or []
        if not order:
            return None
        nodes = [
            {"id": issue, "label": issue[:18], "x": 12 + (idx % 3) * 34, "y": 20 + (idx // 3) * 30}
            for idx, issue in enumerate(order[:6])
        ]
        index_by_id = {n["id"]: i for i, n in enumerate(nodes)}
        edges = [
            [index_by_id[e["from_issue"]], index_by_id[e["to_issue"]]]
            for e in (dag.get("dependency_edges") or [])
            if e.get("from_issue") in index_by_id and e.get("to_issue") in index_by_id
        ]
        return {"kind": "planner", "data": {"nodes": nodes, "edges": edges[:8]}}

    if card == "patch":
        # The full diff/original/patched source lives at `GET
        # /runs/{id}/patch` (`PatchBundle`, fetched directly by the
        # frontend's patch panel) — this payload carries only what that
        # endpoint cannot: per-file generation provenance, which A7 computes
        # but only ever emits on its status event, never onto `RunStateModel`.
        bundle = state.patch_bundle or {}
        patches = bundle.get("patches") or []
        metrics_by_file = _a7_patch_metrics_by_file(events_for_agent)
        if not patches and not metrics_by_file:
            return None
        written_files = {p.get("file", "") for p in patches}
        files = []
        for p in patches:
            file = p.get("file", "")
            m = metrics_by_file.get(file, {})
            target_file = m.get("target_file")
            files.append(
                {
                    "file": file,
                    "method": p.get("method"),
                    # `target_file` is the same value — `blast.origins[0]` —
                    # on every plan in a run, so this is real set membership,
                    # not a per-file resolution A7 actually performed.
                    "isTarget": bool(target_file) and file == target_file,
                    "targetFunction": m.get("target_function"),
                    "generationSource": m.get("generation_source"),
                    "contextSource": m.get("context_source"),
                    "runtimePrompt": m.get("runtime_prompt"),
                    "retryNumber": m.get("retry_number"),
                    "retryReason": m.get("retry_reason"),
                    "semanticDiff": m.get("semantic_diff"),
                    "written": True,
                }
            )
        # A plan A7 attempted but never wrote — every retry rejected by the
        # integrity guard, or `llm_output` came back `None` — has an entry in
        # `metrics_by_file` (recorded before the write, see
        # `a7_code_generation.py`) but none in `patches`. Surfacing it here is
        # the only way the reject pile the guard produced becomes visible
        # instead of silently vanishing from the run.
        for file, m in metrics_by_file.items():
            if file in written_files:
                continue
            target_file = m.get("target_file")
            files.append(
                {
                    "file": file,
                    "method": None,
                    "isTarget": bool(target_file) and file == target_file,
                    "targetFunction": m.get("target_function"),
                    "generationSource": m.get("generation_source"),
                    "contextSource": m.get("context_source"),
                    "runtimePrompt": m.get("runtime_prompt"),
                    "retryNumber": m.get("retry_number"),
                    "retryReason": m.get("retry_reason"),
                    "semanticDiff": m.get("semantic_diff"),
                    "written": False,
                }
            )
        return {"kind": "patch", "data": {"files": files}}

    if card == "mutation":
        mutation = state.mutation_result or {}
        if not mutation:
            return None
        score = mutation.get("mutation_score")
        correctness = mutation.get("correctness_score")
        # `mutant_survived` defaults to `False` whether or not mutation ever
        # ran, so reading it directly renders "none survived" for a suite that
        # was never mutated. `mutation_status == "scored"` is the only signal
        # that a measurement actually happened; anything else is unmeasured.
        scored = mutation.get("mutation_status") == "scored"
        survived: bool | None = bool(mutation.get("mutant_survived")) if scored else None
        # A8 records an aggregate, not a per-mutant ledger. This used to emit
        # eight fabricated mutants with a kill count of 6-or-4 picked from a
        # boolean, and the UI computed a percentage from them — a chart of
        # numbers no tool produced. Only the real measurements go out now.
        return {
            "kind": "mutation",
            "data": {
                "score": float(score) if score is not None else None,
                "survived": survived,
                "survivedMutants": mutation.get("survived_mutants"),
                "pytestPassed": bool(mutation.get("pytest_passed")),
                "correctness": float(correctness) if correctness is not None else None,
                "correctnessThreshold": SCORE_THRESHOLD,
                "failureMessage": (
                    "A mutant survived — the test passes without validating the fix."
                    if survived
                    else (
                        f"Mutation score {float(score):.2f}."
                        if score is not None
                        else "Mutation testing did not run — no score was produced."
                    )
                ),
            },
        }

    if card == "merge":
        decision_data = state.pr_decision or {}
        axis = decision_data.get("axis_scores") or {}
        if not axis:
            return None
        _decision, label = run_decision(state)
        # The literal 80s here duplicated A10's gate by coincidence rather than
        # by reference, so changing SCORE_THRESHOLD would have left the card
        # marking axes "ok" that the router had just rejected.
        scope = _score(axis.get("scope_risk"))
        # `value: null` is the honest answer for an axis nobody measured, and
        # `ok` is only true for a measurement that actually cleared the bar —
        # never for a substituted zero, and never for an absence.
        metrics = [
            {
                "label": label,
                "value": value,
                "ok": value is not None and value >= SCORE_THRESHOLD,
                "measured": value is not None,
            }
            for label, value in [
                ("Correctness", _score(axis.get("correctness"))),
                ("Security", _score(axis.get("security"))),
                ("Fidelity", _score(axis.get("fidelity"))),
            ]
        ]
        metrics.append(
            {
                # High is good on this axis — see build_run_report.
                "label": "Scope Safety",
                "value": scope,
                "ok": scope is not None and scope >= SCORE_THRESHOLD,
                "measured": scope is not None,
                "scopeLabel": (
                    "Not measured"
                    if scope is None
                    else "Contained"
                    if scope >= SCORE_THRESHOLD
                    else "Elevated risk"
                ),
            }
        )
        # The composite gauge used to recompute its own weighted average from
        # three of the four axes client-side — a second trust formula living
        # in the frontend, disagreeing with the `trustScore` shown elsewhere
        # on the same page (which is `_trust_score`'s mean of every *measured*
        # axis). One authoritative composite, computed once, here.
        composite = _trust_score(state)
        return {
            "kind": "merge",
            "data": {
                "metrics": metrics,
                "compositeScore": None if composite is None else round(composite * 100),
                "decisionLabel": label,
                "reviewNote": str(decision_data.get("review_note") or "All trust gates satisfied."),
            },
        }

    return None


# A1's six architectural roles (`models.sig.FileRole`). Named here rather than
# imported from the model so this module states, independently, exactly which
# roles it will report a count for — a role the model gains later shows up as
# 0 until this list is updated, rather than silently appearing.
SEMANTIC_ROLES: tuple[str, ...] = (
    "auth-boundary",
    "data-access",
    "public-api",
    "config-surface",
    "test-only",
    "internal-util",
)


def build_semantic_graph(state: RunStateModel) -> dict | None:
    """A1's Semantic Intent Graph, projected for the workspace panel.

    Distinct from A0.5's Repository Knowledge Graph (`/api/knowledge/...`):
    A0.5 answers "what exists and how is it wired" — structural containment,
    call graph, git ownership. A1 answers "what does this code appear to do"
    — one of six architectural roles per file (`SEMANTIC_ROLES`), plus the
    import edges and the criticality/churn signal that role was weighed
    against. Nothing here is recomputed from A0.5's graph; every field traces
    to `SemanticIntentGraph` (`models/sig.py`), which A1 alone builds.

    Returns `None` when A1 has not produced a SIG yet — the caller renders
    that as pending/unavailable, never as an empty graph.
    """
    sig = state.sig or {}
    files = sig.get("files") or {}
    if not files:
        return None

    role_counts: dict[str, int] = {role: 0 for role in SEMANTIC_ROLES}
    out_files: list[dict] = []
    for path, node in files.items():
        role = node.get("role") or "internal-util"
        if role in role_counts:
            role_counts[role] += 1
        out_files.append(
            {
                "path": path,
                "role": role,
                "imports": list(node.get("imports") or []),
                "importedBy": list(node.get("imported_by") or []),
                "churnWeight": float(node.get("churn_weight") or 0.0),
                "criticality": float(node.get("criticality") or 0.0),
            }
        )
    # Highest-criticality first: this is the order the hotspot list and the
    # architecture view read files in, so it is sorted once, here, rather than
    # by every consumer.
    out_files.sort(key=lambda f: (-f["criticality"], f["path"]))

    return {
        "generatedAt": sig.get("generated_at"),
        "sourceRoots": list(sig.get("source_roots") or []),
        "files": out_files,
        "edges": [list(e) for e in (sig.get("edges") or [])],
        "roleCounts": role_counts,
        "totalFiles": len(out_files),
        "totalEdges": len(sig.get("edges") or []),
    }


# A2's classification is a reachability verdict, not a CVSS severity band —
# it answers "does this repository's own import graph reach the vulnerable
# package", which `severity` (a free-form OSV string, sometimes a CVSS score,
# sometimes the literal word "HIGH") cannot answer on its own. Ordered
# highest-attention-first: confirmed-reachable, then undetermined, then
# confirmed-inert.
_CLASSIFICATION_ORDER = {"Critical": 0, "Unknown": 1, "Informational": 2}


def build_dependency_risk(state: RunStateModel) -> dict | None:
    """A2's dependency/CVE reachability report, projected for the workspace panel.

    Distinct from A1's semantic roles and A0.5's structural graph: A2 answers
    "what does this repository depend on, and is any of that reachable" — OSV
    advisories narrowed by the import graph A1 built. Every field traces to
    `CVEReachabilityReport` (`models/cve.py`), which A2 alone writes.

    Returns `None` before A2 has run. A run that completed and found zero
    advisories still returns a payload — `manifest` set, `totalDependencies`
    real, `findings` empty — because "analyzed and clean" and "never ran" are
    different facts the caller must render differently.
    """
    cve = state.cve_report
    if cve is None:
        return None

    findings = cve.get("findings") or []
    out_findings = [
        {
            "package": f.get("package"),
            "cveId": f.get("cve_id"),
            "severity": f.get("severity"),
            "installedVersion": f.get("installed_version"),
            "affectedSymbol": f.get("affected_symbol"),
            "reachable": f.get("reachable"),
            "reachPath": f.get("reach_path"),
            "classification": f.get("classification") or "Unknown",
        }
        for f in findings
    ]
    out_findings.sort(key=lambda f: _CLASSIFICATION_ORDER.get(f["classification"], 3))

    return {
        "manifest": cve.get("manifest"),
        "ecosystem": cve.get("ecosystem"),
        "totalDependencies": cve.get("total_dependencies") or 0,
        "advisoryCount": len(findings),
        "reachableCount": sum(1 for f in out_findings if f["classification"] == "Critical"),
        "informationalCount": sum(
            1 for f in out_findings if f["classification"] == "Informational"
        ),
        "unknownCount": sum(1 for f in out_findings if f["classification"] == "Unknown"),
        "findings": out_findings,
    }


def build_static_findings(state: RunStateModel) -> dict | None:
    """A3's static-analysis findings, projected for the workspace panel.

    Distinct from A0.5's structure, A1's roles, and A2's dependency risk: A3
    answers "what did the scanners find in this repository's own source, and
    why does one finding outrank another". Every field traces to
    `StaticAnalysisReport` (`models/findings.py`), which A3 alone writes.

    Returns `None` before A3 has run. A run that completed and ranked zero
    findings still returns a payload (`scannerStatus` populated, `rawCount`
    real, `findings` empty) — "ran clean" and "never ran" are different facts.
    """
    static = state.static_report
    if static is None:
        return None

    prioritized = static.get("prioritized") or []
    findings = [
        {
            "id": f.get("id"),
            "rank": idx + 1,
            "file": f.get("file"),
            "line": f.get("line"),
            "message": f.get("message"),
            "tools": list(f.get("tools") or []),
            "severity": f.get("severity"),
            "severityMeasured": bool(f.get("severity_measured", False)),
            "consensus": bool(f.get("consensus", False)),
            "blastRadiusScore": f.get("blast_radius_score"),
            "criticality": f.get("criticality"),
            "churnWeight": f.get("churn_weight"),
        }
        for idx, f in enumerate(prioritized)
    ]

    return {
        "scannerStatus": dict(static.get("scanner_status") or {}),
        "rawCount": static.get("raw_count") or 0,
        "prioritizedCount": len(findings),
        "findings": findings,
    }


def _project_reconciliation_lane(lane: dict) -> dict:
    """One scanner's lane of the baseline-vs-post ledger, chip-addressable.

    The stored form indexes into per-key occurrence lists; the panel needs
    stable ids it can hang a rail off, so every occurrence is given one here
    (`b-<lane>-<key>-<n>` / `p-…`). `rails` is the pairing — a carried finding
    and how far the patch moved it — and a post chip appearing in no rail is
    introduced. That relationship is the contract: the panel derives its
    counts from these arrays and never from a parallel tally, so the ledger
    and the verdict bar cannot disagree.
    """
    tool = str(lane.get("tool") or "")
    keys = []
    for key_index, entry in enumerate(lane.get("keys") or []):
        group_id = f"{tool}-{key_index}"
        baseline = [
            {"id": f"b-{group_id}-{n}", "line": occ.get("line")}
            for n, occ in enumerate(entry.get("baseline") or [])
        ]
        post = [
            {"id": f"p-{group_id}-{n}", "line": occ.get("line")}
            for n, occ in enumerate(entry.get("post") or [])
        ]
        rails = [
            {
                "baselineId": baseline[m["baseline_index"]]["id"],
                "postId": post[m["post_index"]]["id"],
                "lineDelta": m.get("line_delta", 0),
            }
            for m in entry.get("matched") or []
            if 0 <= m.get("baseline_index", -1) < len(baseline)
            and 0 <= m.get("post_index", -1) < len(post)
        ]
        keys.append(
            {
                "id": group_id,
                "tool": tool,
                "file": entry.get("file") or "",
                "message": entry.get("message") or "",
                "baseline": baseline,
                "post": post,
                "rails": rails,
                "introducedIds": [
                    post[j]["id"] for j in entry.get("introduced_indexes") or [] if 0 <= j < len(post)
                ],
                "resolvedIds": [
                    baseline[i]["id"]
                    for i in entry.get("resolved_indexes") or []
                    if 0 <= i < len(baseline)
                ],
            }
        )
    return {"tool": tool, "compared": bool(lane.get("compared")), "keys": keys}


def build_security_rescan(state: RunStateModel) -> dict | None:
    """A9's post-patch security re-scan, projected for the workspace panel.

    A9 answers one question — did the patch introduce a finding that was not
    in A3's pre-patch baseline — and this projection carries the *comparison*
    that produced the answer, not just its result. `reconciliation` is one
    lane per known scanner; within a lane, each `(tool, file, message)` key
    lists its baseline and post occurrences and the `rails` pairing them, with
    the line delta each carried finding moved by. A post occurrence in no rail
    is introduced. That is the whole algorithm, addressable.

    It still does not compare severity: `_run_bandit`/`_run_semgrep` assign
    every finding the same constant 0.7, never a real per-issue measurement,
    so `severity` is omitted from `newFindings` and from every occurrence
    rather than displayed as something it is not.

    `reconciliationAvailable` is false for a run completed before A9 recorded
    the reconciliation (`SecurityRescanResult.reconciliation` defaults empty).
    An empty ledger for such a run would read as "nothing carried", which is a
    claim about the comparison rather than an absence of one — so the flag
    exists and the panel must say the detail was not recorded instead of
    rendering zeroes.

    `verdict` mirrors the three states the backend can actually produce:
    `security_score is None` means no scanner executed this rescan (not
    "clean" — "not measured"); otherwise `rejected` says whether a new
    finding was found. There is no fourth state.

    Returns `None` before A9 has run. A run that completed and found nothing
    new still returns a payload (`securityScore` real, `newFindings` empty) —
    "ran clean" and "never ran" are different facts.
    """
    security = state.security_result
    if security is None:
        return None

    new_findings_raw = security.get("new_findings") or []
    new_findings = [
        {
            "id": f.get("id"),
            "file": f.get("file"),
            "line": f.get("line"),
            "message": f.get("message"),
            "tools": list(f.get("tools") or []),
        }
        for f in new_findings_raw
    ]

    security_score = security.get("security_score")
    rejected = bool(security.get("rejected"))
    if security_score is None:
        verdict = "not_measured"
    elif rejected:
        verdict = "new_findings"
    else:
        verdict = "clean"

    retry_context = None
    if rejected:
        failure_brief = security.get("failure_brief") or {}
        validation_failure = security.get("validation_failure") or {}
        retry_context = {
            "assertionMessage": validation_failure.get("assertion_message"),
            "securityConstraint": failure_brief.get("security_constraint"),
        }

    scanners_run = list(security.get("scanners_run") or [])
    stored_lanes = security.get("reconciliation") or []
    reconciliation_available = bool(stored_lanes)
    if reconciliation_available:
        reconciliation = [_project_reconciliation_lane(lane) for lane in stored_lanes]
    else:
        # No stored comparison. Lanes still carry the one fact that survives —
        # which scanners ran — so "not compared" stays visible; the keys are
        # empty because there is no pairing to show, not because none carried.
        reconciliation = [
            {"tool": tool, "compared": tool in scanners_run, "keys": []}
            for tool in ("bandit", "semgrep")
        ]

    return {
        "verdict": verdict,
        "rejected": rejected,
        "securityScore": security_score,
        "newFindings": new_findings,
        "reconciliation": reconciliation,
        "reconciliationAvailable": reconciliation_available,
        "scannersRun": scanners_run,
        "reexecutionCommand": security.get("reexecution_command") or "",
        "reexecutionTimeoutSeconds": security.get("reexecution_timeout_seconds"),
        "retryContext": retry_context,
    }


# A mutant carries no status the codebase can compress to "detected" without
# reading `mutation_parser.DETECTED_STATUSES` — kept in sync manually because
# importing the agents module from here would invert the dependency.
_KILLED_STATUSES = {"killed", "timeout", "caught by type check"}
_SURVIVED_STATUSES = {"survived"}


def _mutant_bucket(status: str) -> str:
    if status in _KILLED_STATUSES:
        return "killed"
    if status in _SURVIVED_STATUSES:
        return "survived"
    return "inconclusive"


def _patched_source(state: RunStateModel) -> tuple[str | None, str | None, str | None]:
    """The one file A8 actually mutates — `patches[0]`, per `_run_mutmut`."""
    patches = (state.patch_bundle or {}).get("patches") or []
    if not patches:
        return None, None, None
    first = patches[0]
    return first.get("file"), first.get("patched"), first.get("original")


def _patched_line_numbers(original: str | None, patched: str | None) -> set[int]:
    """1-indexed lines in `patched` that A7's patch actually changed.

    Real diff (`difflib`), not a guess — this is what earns a line the
    left-edge "patched" indicator, as distinct from a line a mutant merely
    attacked. `[]` when there is no original to diff against (e.g. a new
    file), which the caller reads as "cannot tell, so don't mark anything".
    """
    if original is None or patched is None:
        return set()
    import difflib

    matcher = difflib.SequenceMatcher(a=original.splitlines(), b=patched.splitlines())
    changed: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(j1 + 1, j2 + 1))
    return changed


def _function_spans(source: str) -> dict[str, tuple[int, int]]:
    """Real line spans per function name, from parsing the actual patched file.

    Keyed by simple name (mutmut's own attribution is simple-name, not
    qualname) — a repo with two functions sharing a name collides here the
    same way mutmut's own naming does; the last one parsed wins.
    """
    parsed = parse_source(source)
    if parsed is None:
        return {}
    return {fn.name: (fn.lineno, fn.end_lineno) for fn in parsed.function_spans}


def _code_window(source_lines: list[str], start: int, end: int, cap: int = 40) -> list[dict]:
    """1-indexed `{line, text}` rows for `[start, end]`, capped so a huge
    function still renders a legible window rather than a scroll of code."""
    end = min(end, start + cap - 1)
    return [
        {"line": n, "text": source_lines[n - 1]}
        for n in range(start, end + 1)
        if 0 < n <= len(source_lines)
    ]


def build_mutation_validation(state: RunStateModel) -> dict | None:
    """A8's scoped-validation + mutation gauntlet, projected as a sabotage board.

    A8 runs three gates in a fixed order and stops at the first one that
    fails: the target test, the full regression suite, then mutmut against
    the one file it patched (`patch_bundle.patches[0]` — `_run_mutmut` never
    mutates more than that). `stage` tells the panel which gate the run
    actually reached — a gate the run never got to is `"not_reached"`, never
    rendered as if it passed.

    Every mutant carries the function mutmut's own naming convention
    attributes it to (`x_<function>__mutmut_<n>` — real, not inferred).
    A bounded subset (survivors first, `a8_mutation_validator.MAX_MUTANT_DIFFS`)
    also carries a real line number and before/after snippet, fetched from
    `mutmut show <id>` and parsed as an actual unified diff. Mutants outside
    that budget carry `function` only — `functions[].markers` holds just the
    line-resolved ones; `functions[].unattributed` counts the rest so the UI
    can show "N more mutants attacked this function" without inventing where.

    `mutationStatus` is the only trustworthy signal that mutation testing
    produced a real measurement (`"scored"`) versus could not
    (`"unavailable"`) versus never got to run (`"not_run"`) — reading
    `mutant_survived` directly would render "no mutants survived" for a suite
    that was never mutated.

    Returns `None` before A8 has produced a durable result for this run.
    """
    mutation = state.mutation_result
    if not mutation:
        return None

    pytest_available = mutation.get("pytest_available", True)
    target_test_passed = mutation.get("target_test_passed")
    regression_tests_passed = mutation.get("regression_tests_passed")
    mutation_status = mutation.get("mutation_status", "not_run")

    if not pytest_available:
        stage = "not_reached"
    elif target_test_passed is False:
        stage = "target_test"
    elif regression_tests_passed is False:
        stage = "regression"
    elif mutation_status == "scored":
        stage = "mutation"
    else:
        stage = "not_reached"

    score = mutation.get("mutation_score")
    correctness = mutation.get("correctness_score")
    scored = mutation_status == "scored"
    survived = bool(mutation.get("mutant_survived")) if scored else None

    failure_brief = mutation.get("failure_brief") or {}
    validation_failure = mutation.get("validation_failure") or {}
    retry_context = None
    if mutation.get("patch_retry_required"):
        retry_context = {
            "assertionMessage": validation_failure.get("assertion_message"),
            "expectedValue": validation_failure.get("expected_value"),
            "actualValue": validation_failure.get("actual_value"),
            "violatedContract": failure_brief.get("violated_contract"),
            "retryInstruction": failure_brief.get("retry_instruction"),
        }

    reproduction = state.reproduction or {}

    # -------------------------------------------------------- per-function attack map
    patch_file, patched_source, original_source = _patched_source(state)
    spans = _function_spans(patched_source) if patched_source else {}
    source_lines = patched_source.splitlines() if patched_source else []
    patched_lines = _patched_line_numbers(original_source, patched_source)

    raw_mutants = mutation.get("mutants") or []
    by_function: dict[str, list[dict]] = {}
    unattributed_counts = {"killed": 0, "survived": 0, "inconclusive": 0}
    for m in raw_mutants:
        fn = m.get("function")
        if fn is None:
            unattributed_counts[_mutant_bucket(m.get("status", ""))] += 1
            continue
        by_function.setdefault(fn, []).append(m)

    functions: list[dict] = []
    survivors: list[dict] = []
    for fn_name, records in sorted(by_function.items(), key=lambda kv: -len(kv[1])):
        counts = {"killed": 0, "survived": 0, "inconclusive": 0}
        markers: list[dict] = []
        unattributed = 0
        for m in records:
            bucket = _mutant_bucket(m.get("status", ""))
            counts[bucket] += 1
            line = m.get("line")
            before, after = m.get("before"), m.get("after")
            if line is not None:
                markers.append(
                    {
                        "mutantId": m.get("mutant_id"),
                        "status": bucket,
                        "line": line,
                        # Real only when `a8_mutation_validator` fetched this
                        # mutant's diff (survivors first, then killed, up to
                        # `MAX_MUTANT_DIFFS`) — `null` otherwise, never guessed.
                        "before": before,
                        "after": after,
                    }
                )
            else:
                unattributed += 1
            if bucket == "survived":
                survivors.append(
                    {
                        "mutantId": m.get("mutant_id"),
                        "function": fn_name,
                        "file": m.get("file") or patch_file,
                        "line": line,
                        "before": before,
                        "after": after,
                    }
                )

        span = spans.get(fn_name)
        span_lines = (
            sorted(n for n in patched_lines if span[0] <= n <= span[1]) if span else []
        )
        entry = {
            "name": fn_name,
            "killed": counts["killed"],
            "survived": counts["survived"],
            "inconclusive": counts["inconclusive"],
            "total": len(records),
            "markers": markers,
            "unattributed": unattributed,
            "codeAvailable": span is not None,
            "spanStart": span[0] if span else None,
            "spanEnd": span[1] if span else None,
            "lines": _code_window(source_lines, span[0], span[1]) if span else [],
            # Lines this patch actually changed (real diff against A7's
            # `original`), not merely lines a mutant attacked — drives the
            # left-edge "patched" indicator distinct from the mutation gutter.
            "patchedLines": span_lines,
        }
        functions.append(entry)

    return {
        "stage": stage,
        "pytestAvailable": pytest_available,
        "pytestPassed": bool(mutation.get("pytest_passed")),
        "targetTestId": reproduction.get("failing_test"),
        "targetTestPassed": target_test_passed,
        "regressionTestsPassed": regression_tests_passed,
        "newFailures": list(mutation.get("new_failures") or []),
        "preExistingFailures": list(mutation.get("pre_existing_failures") or []),
        "mutationStatus": mutation_status,
        "unavailableReason": mutation.get("mutation_unavailable_reason"),
        "mutationScore": float(score) if score is not None else None,
        "mutantSurvived": survived,
        "killedMutants": mutation.get("killed_mutants"),
        "survivedMutants": mutation.get("survived_mutants"),
        "totalMutants": mutation.get("total_mutants"),
        "inconclusiveMutants": mutation.get("inconclusive_mutants"),
        "mutantsByStatus": dict(mutation.get("mutants_by_status") or {}),
        "correctnessScore": float(correctness) if correctness is not None else None,
        "correctnessThreshold": SCORE_THRESHOLD,
        "patchRetryRequired": bool(mutation.get("patch_retry_required")),
        "pytestReexecutionCommand": mutation.get("pytest_reexecution_command") or "",
        "reexecutionCommand": mutation.get("reexecution_command") or "",
        "reexecutionTimeoutSeconds": mutation.get("reexecution_timeout_seconds"),
        "retryContext": retry_context,
        "patchFile": patch_file,
        "functions": functions,
        "survivors": survivors,
        "unattributedCounts": unattributed_counts,
    }


# A3.5's four real outcomes, mapped to the UI-facing vocabulary the panel
# renders. This is presentation only — `ReproductionStatus` itself is
# unchanged; nothing here invents a fifth backend state.
_REPRODUCTION_UI_STATUS = {
    "CONFIRMED": "reproduced",
    "UNCONFIRMED": "not_reproduced",
    "NO_TESTS": "unavailable",
    "INFRA_ERROR": "error",
}


def _reproduction_stages(repro: dict) -> list[dict]:
    """Five real stages of A3.5's execution, derived from its final state.

    Not live progress — A3.5 runs as one atomic subprocess call with no
    intermediate events, so there is nothing to poll mid-run. This is a
    deterministic, post-hoc decomposition of what the recorded result proves
    happened: every `status` and `detail` below is computed from fields A3.5
    actually persisted, never fabricated or animated.
    """
    status = repro.get("status")
    exit_code = repro.get("exit_code")
    timed_out = bool(repro.get("timed_out"))
    collected = repro.get("tests_collected")
    passed = repro.get("tests_passed")
    failed = repro.get("tests_failed")
    infra_detail = repro.get("infra_detail")
    evidence_source = repro.get("evidence_source")

    stages: list[dict] = []

    if exit_code == -1:
        suite_status = "failed"
        suite_detail = (
            "The pytest subprocess exceeded its time limit and was terminated."
            if timed_out
            else (infra_detail or "The pytest subprocess could not be started.")
        )
    else:
        suite_status = "done"
        suite_detail = f"pytest exited with code {exit_code}."
    stages.append({"id": "suite_executed", "label": "Test suite executed", "status": suite_status, "detail": suite_detail})

    if suite_status != "done":
        stages.append({"id": "tests_collected", "label": "Tests collected", "status": "skipped", "detail": "Not reached — the suite never executed."})
    elif status == "NO_TESTS":
        stages.append({"id": "tests_collected", "label": "Tests collected", "status": "failed", "detail": "pytest collected zero tests."})
    elif collected is not None:
        stages.append({"id": "tests_collected", "label": "Tests collected", "status": "done", "detail": f"{collected} test(s) collected."})
    else:
        stages.append({"id": "tests_collected", "label": "Tests collected", "status": "failed", "detail": "pytest's JSON report could not be read, so collection is unknown."})

    collect_status = stages[-1]["status"]
    if collect_status != "done":
        stages.append({"id": "tests_run", "label": "Tests executed", "status": "skipped", "detail": "Not reached — no tests were collected."})
    elif status == "INFRA_ERROR":
        stages.append({"id": "tests_run", "label": "Tests executed", "status": "failed", "detail": infra_detail or "pytest reported an unexpected exit code."})
    else:
        detail = f"{passed} passed, {failed} failed." if passed is not None and failed is not None else "Tests ran to completion."
        stages.append({"id": "tests_run", "label": "Tests executed", "status": "done", "detail": detail})

    run_status = stages[-1]["status"]
    if run_status != "done":
        stages.append({"id": "failure_observed", "label": "Failure observed", "status": "skipped", "detail": "Not reached — the tests did not run to completion."})
    elif status == "CONFIRMED":
        stages.append({
            "id": "failure_observed",
            "label": "Failure observed",
            "status": "done",
            "detail": f"{repro.get('failing_test') or 'A test'} failed with {repro.get('exception_type') or 'an error'}.",
        })
    else:
        stages.append({"id": "failure_observed", "label": "Failure observed", "status": "not_triggered", "detail": "No failure — every collected test passed."})

    observe_status = stages[-1]["status"]
    if observe_status == "done" and repro.get("traceback"):
        source_label = "structured pytest report" if evidence_source == "pytest_report" else "output text pattern match"
        stages.append({"id": "evidence_captured", "label": "Evidence captured", "status": "done", "detail": f"Exception and traceback captured from the {source_label}."})
    elif observe_status == "done":
        stages.append({"id": "evidence_captured", "label": "Evidence captured", "status": "failed", "detail": "A failure was observed but no traceback could be extracted."})
    else:
        stages.append({"id": "evidence_captured", "label": "Evidence captured", "status": "skipped", "detail": "Not reached — no failure was observed."})

    return stages


def build_reproduction_evidence(state: RunStateModel) -> dict | None:
    """A3.5's failure-reproduction evidence, projected for the workspace panel.

    A3.5 is an independent full-suite pytest gate — it does not target a
    specific A3 finding (`orchestrator/nodes.py::reproduction_gate` never
    reads `state.static_report`), so this projection never claims a
    finding-to-reproduction link that does not exist. It answers a narrower,
    real question: does the reported bug actually reproduce as a failing
    test, right now, in this repository.

    Returns `None` before A3.5 has run. Every field traces to
    `ReproductionResult` (`models/reproduction.py`), which A3.5 alone writes.
    """
    repro = state.reproduction
    if repro is None:
        return None

    exception_type = repro.get("exception_type")
    exception_message = repro.get("exception_message")
    error_signature = (
        f"{exception_type}: {exception_message}" if exception_type and exception_message else exception_type
    )

    failing_test = repro.get("failing_test")
    baseline = [t for t in (repro.get("pre_existing_failures") or []) if t != failing_test]

    return {
        "status": repro.get("status"),
        "uiStatus": _REPRODUCTION_UI_STATUS.get(repro.get("status"), "unavailable"),
        "confidence": repro.get("confidence"),
        "evidenceSource": repro.get("evidence_source"),
        "failingTest": failing_test,
        "exceptionType": exception_type,
        "exceptionMessage": exception_message,
        "errorSignature": error_signature,
        "failingFile": repro.get("failing_file"),
        "failingLine": repro.get("failing_line"),
        "traceback": repro.get("traceback"),
        "infraDetail": repro.get("infra_detail"),
        "command": repro.get("command") or None,
        "exitCode": repro.get("exit_code"),
        "timedOut": bool(repro.get("timed_out")),
        "stdout": repro.get("stdout"),
        "stderr": repro.get("stderr"),
        "durationSeconds": repro.get("duration_seconds"),
        "startedAt": repro.get("started_at"),
        "finishedAt": repro.get("finished_at"),
        "testsCollected": repro.get("tests_collected"),
        "testsPassed": repro.get("tests_passed"),
        "testsFailed": repro.get("tests_failed"),
        "reexecutionCommand": repro.get("reexecution_command") or None,
        "reexecutionIsTargeted": bool(repro.get("reexecution_is_targeted")),
        "reexecutionTimeoutSeconds": repro.get("reexecution_timeout_seconds"),
        "baselineFailures": baseline,
        "stages": _reproduction_stages(repro),
    }


# Presentation copy A5's real ambiguity forces into words rather than a
# fabricated per-file flag: `risk_score` is `criticality * churn_weight *
# security_score`, and `churn_weight` defaults to `0.0` both when a file
# genuinely has no recent bug-fix commits *and* when the repository has no git
# history to measure from at all (`git_service.get_churn_weights` returns `{}`
# in both cases). A5 cannot tell those apart — that ambiguity is A1's, not
# introduced here — so the projection labels the column "priority", not
# "risk", and states the caveat once rather than guessing per file.
RISK_MEASUREMENT_CAVEAT = (
    "Priority combines file criticality, churn and role. Churn is 0 both when "
    "a file has no recent bug-fix commits and when the repository has no git "
    "history to measure from — this run cannot tell those apart, so a 0 here "
    "is a floor, not a measured absence of risk."
)


def _repair_plan_why(issue_id: str, findings_by_id: dict, cve_by_id: dict) -> dict | None:
    """Join a fix-node's issue_id back to the A3 finding or A2 CVE that produced it.

    `FixNode` carries no message of its own — `build_fix_nodes` only ever
    stores `issue_id`/`files`/`depends_on` — so "why does this node exist" is
    only answerable by reversing the identity `build_fix_nodes` assigned:
    `finding["id"]` verbatim for a static finding, `f"cve-{record.cve_id}"`
    for a CVE. Returns `None` when neither matches — a legitimate outcome for
    the synthetic `"fix-0"` catch-all node `build_fix_nodes` emits when there
    were no findings or CVEs to name at all.
    """
    if issue_id in findings_by_id:
        f = findings_by_id[issue_id]
        return {
            "kind": "static_finding",
            "message": f.get("message"),
            "severity": f.get("severity"),
            "severityMeasured": bool(f.get("severity_measured", False)),
            "tools": list(f.get("tools") or []),
        }
    cve_id = issue_id[len("cve-") :] if issue_id.startswith("cve-") else None
    if cve_id and cve_id in cve_by_id:
        c = cve_by_id[cve_id]
        return {
            "kind": "cve",
            "package": c.get("package"),
            "severity": c.get("severity"),
            "installedVersion": c.get("installed_version"),
            "reachPath": c.get("reach_path"),
        }
    return None


def build_repair_plan(state: RunStateModel) -> dict | None:
    """A6's repair plan, projected for the workspace board.

    Distinct from A5's blast-radius impact: A5 answers what could be affected
    by a change; A6 answers what ProoFix proposes to change, in what order,
    and why. Every field traces to `FixDAGPlan` (`models/fix_dag.py`), which
    A6 alone writes, joined read-only against A3's findings and A2's CVE
    records to answer "why" — `FixNode` itself carries no message.

    **A6's plan is not what A7 executes.** `a7_code_generation.py` reads
    exactly one value from this plan — `execution_order[0]`, as a label — and
    derives its actual patch targets from A5's blast scope and A4's root
    cause instead. This projection carries that fact through as
    `executionAuthority` rather than letting the ordered list on screen imply
    an execution guarantee this run cannot back up.

    Returns `None` before A6 has run. A run with zero fix nodes still returns
    a payload with an empty `steps` list — A6 ran and found nothing to plan,
    which is a different fact from A6 never having run.
    """
    from backend.services.repair_step_evidence import step_evidence

    dag = state.fix_dag
    if dag is None:
        return None

    findings_by_id = {
        f.get("id"): f
        for f in ((state.static_report or {}).get("prioritized") or [])
        if f.get("id")
    }
    cve_by_id = {
        c.get("cve_id"): c
        for c in ((state.cve_report or {}).get("findings") or [])
        if c.get("cve_id")
    }

    nodes = dag.get("nodes") or []
    nodes_by_id = {n.get("issue_id"): n for n in nodes if n.get("issue_id")}
    execution_order = list(dag.get("execution_order") or [])

    # The LLM ordering path is used unvalidated (`a6_fix_dag_planner.py`), so
    # `execution_order` is not guaranteed to name every node A6 built. Nodes
    # it omits are appended rather than dropped — silently shrinking the plan
    # to whatever the model happened to list would misrepresent what A6 built.
    unordered = [nid for nid in nodes_by_id if nid not in execution_order]
    ordered_ids = execution_order + sorted(unordered)

    conflict_batches = [list(b) for b in (dag.get("conflict_batches") or [])]
    conflicts_by_issue: dict[str, set[str]] = {}
    for batch in conflict_batches:
        for issue in batch:
            conflicts_by_issue.setdefault(issue, set()).update(i for i in batch if i != issue)

    incoming_by_issue: dict[str, list[dict]] = {}
    for edge in dag.get("dependency_edges") or []:
        incoming_by_issue.setdefault(edge.get("to_issue"), []).append(
            {"fromIssue": edge.get("from_issue"), "reason": edge.get("reason")}
        )

    # The one fact `a7_code_generation.py` actually reads from this whole
    # plan: `execution_order[0]`. Named here once so the frontend never has
    # to re-derive it — an empty `execution_order` means there is no handoff
    # target, not that the first node in `ordered_ids` (which may be an
    # LLM-omitted node appended after the real order) silently becomes one.
    handoff_issue_id = execution_order[0] if execution_order else None

    steps = []
    for position, issue_id in enumerate(ordered_ids, start=1):
        node = nodes_by_id.get(issue_id, {})
        steps.append(
            {
                "issueId": issue_id,
                "position": position,
                # True only for a node the LLM path actually named — the
                # honest label for what "position" means when the plan came
                # from an unvalidated model response.
                "ordered": issue_id in execution_order,
                "files": list(node.get("files") or []),
                "dependsOn": list(node.get("depends_on") or []),
                "incomingEdges": incoming_by_issue.get(issue_id, []),
                "conflictsWith": sorted(conflicts_by_issue.get(issue_id, set())),
                "why": _repair_plan_why(issue_id, findings_by_id, cve_by_id),
                "isHandoffTarget": issue_id == handoff_issue_id,
                "evidence": step_evidence(state, node),
            }
        )

    return {
        "steps": steps,
        "conflictBatches": conflict_batches,
        "orderingSource": dag.get("ordering_source"),
        "orderingRationale": dag.get("ordering_rationale") or "",
        # The real topological order, computed unconditionally by A6 — present
        # even when `orderingSource == "llm"`, so a client can compare the
        # model's chosen order against the order the dependency graph itself
        # supports rather than taking the LLM's order on faith. Empty on a run
        # predating this field, same convention as every other list here.
        "deterministicOrder": list(dag.get("deterministic_order") or []),
        "totalDependencyEdges": len(dag.get("dependency_edges") or []),
        # Real and load-bearing for the UI's honesty banner: A7 reads only
        # `execution_order[0]` as a label today. See the docstring above.
        "executionAuthority": {
            "consumedBy": "A7",
            "field": "execution_order[0]",
            "note": (
                "A7 reads only the first step's identifier as a label for its patch "
                "bundle. It derives its actual patch targets from A5's blast scope "
                "and A4's root cause, not from this plan's order or dependencies."
            ),
        },
    }


def build_blast_impact(state: RunStateModel) -> dict | None:
    """A5's blast-radius impact, projected for the workspace board.

    Distinct from A2's dependency-risk projection: A2 answers whether a
    third-party advisory reaches this repository's code; A5 answers what could
    be affected if the file A4 is investigating changes. Every field traces to
    `BlastGraphResult` (`models/blast.py`), which A5 alone writes, joined
    read-only against A1's SIG (role/criticality/churn — A5 does not own those)
    and A3's findings (whether an impacted file was independently flagged).

    Returns `None` before A5 has run. A run whose target could not be resolved
    still returns a payload with an empty `scope` and `origin: null` — A5 ran
    and had nothing to traverse, which is a different fact from A5 never
    having run at all.
    """
    blast = state.blast_graph
    if blast is None:
        return None

    sig_files: dict = (state.sig or {}).get("files") or {}
    flagged_files = {
        f.get("file") for f in ((state.static_report or {}).get("prioritized") or [])
        if f.get("file")
    }
    auto_patch = set(blast.get("auto_patch_scope") or [])
    human_review = set(blast.get("human_review_required") or [])

    scope_out = []
    for item in blast.get("scope") or []:
        path = item.get("path")
        node = sig_files.get(path) or {}
        scope_out.append(
            {
                "path": path,
                "hopCount": item.get("hop_count"),
                "directions": list(item.get("directions") or [item.get("direction")]),
                "reachedVia": item.get("reached_via"),
                "edgeBasis": item.get("edge_basis"),
                "propagationConfidence": item.get("propagation_confidence"),
                "priorityScore": item.get("risk_score"),
                "origin": item.get("origin"),
                "role": node.get("role"),
                "criticality": node.get("criticality"),
                "churnWeight": node.get("churn_weight"),
                "autoPatchable": path in auto_patch,
                "humanReviewRequired": path in human_review,
                "hasStaticFinding": path in flagged_files,
            }
        )
    scope_out.sort(key=lambda s: (s["hopCount"] if s["hopCount"] is not None else 99, s["path"]))

    edges_out = [
        {
            "from": e.get("from_path"),
            "to": e.get("to_path"),
            "direction": e.get("direction"),
            "basis": e.get("basis"),
            "hopCount": e.get("hop_count"),
        }
        for e in (blast.get("edges") or [])
    ]

    resolution = blast.get("target_resolution")
    origin = None
    if resolution:
        origin = {
            "originalPath": resolution.get("original_path"),
            "normalizedPath": resolution.get("normalized_path"),
            "resolvedPath": resolution.get("resolved_path"),
            "source": resolution.get("source"),
            "confidence": resolution.get("confidence"),
            "runtimeConfirmed": resolution.get("runtime_confirmed"),
            "pinned": resolution.get("pinned"),
        }
    elif blast.get("origins"):
        # A run predating `target_resolution`, or one where A5 traversed from
        # a citation rather than a resolved target — the origin path is still
        # real, only its resolution provenance is unavailable.
        origin = {
            "originalPath": blast["origins"][0],
            "normalizedPath": blast["origins"][0],
            "resolvedPath": blast["origins"][0],
            "source": None,
            "confidence": None,
            "runtimeConfirmed": None,
            "pinned": None,
        }

    overlap = sorted(auto_patch & human_review)

    return {
        "origin": origin,
        "origins": list(blast.get("origins") or []),
        "scope": scope_out,
        "edges": edges_out,
        "maxHop": max((s["hopCount"] for s in scope_out if s["hopCount"] is not None), default=0),
        "autoPatchScope": sorted(auto_patch),
        "humanReviewRequired": sorted(human_review),
        # Real and expected, not a bug to hide: `pin_resolved_target` can force
        # a runtime-confirmed target into `auto_patch_scope` while the
        # traversal's own confidence threshold independently placed it in
        # `human_review_required`. Both facts are true at once.
        "patchAuthorityOverlap": overlap,
        "riskMeasurementCaveat": RISK_MEASUREMENT_CAVEAT,
    }


def build_investigation(state: RunStateModel) -> dict | None:
    """A4's evidence investigation, projected for the workspace board.

    A pure rename of `InvestigationReport` (`models/investigation.py`) into the
    UI's camelCase, and nothing more: no field is recomputed, defaulted or
    summarised here, because every judgement in the report — what supports the
    finding, what contradicts it, how the confidence was reached — is A4's to
    make and must survive to the screen unchanged.

    Returns `None` before A4 has run. A report with `status: "no_finding"` is a
    different fact: A4 ran and had nothing to investigate.
    """
    report = state.investigation
    if report is None:
        return None

    completeness = report.get("completeness") or {}
    return {
        "status": report.get("status"),
        "subjectKind": report.get("subject_kind"),
        "findingId": report.get("finding_id"),
        "title": report.get("title"),
        "file": report.get("file"),
        "line": report.get("line"),
        "severity": report.get("severity"),
        "severityMeasured": bool(report.get("severity_measured", False)),
        "reproductionStatus": report.get("reproduction_status"),
        "rootCause": report.get("root_cause"),
        "summary": report.get("summary"),
        "rootCauseSource": report.get("root_cause_source"),
        "diagnosisStatus": report.get("diagnosis_status"),
        "confidence": report.get("confidence"),
        "confidenceBreakdown": [
            {
                "component": c.get("component"),
                "points": c.get("points"),
                "basis": c.get("basis"),
            }
            for c in (report.get("confidence_breakdown") or [])
        ],
        "evidence": [
            {
                "id": e.get("id"),
                "category": e.get("category"),
                "source": e.get("source"),
                "description": e.get("description"),
                "status": e.get("status"),
                "stance": e.get("stance"),
                "strength": e.get("strength"),
                "strengthBasis": e.get("strength_basis"),
                "detail": e.get("detail") or {},
            }
            for e in (report.get("evidence") or [])
        ],
        "completeness": {
            "measuredCategories": completeness.get("measured_categories"),
            "totalCategories": completeness.get("total_categories"),
            "ratio": completeness.get("ratio"),
            "categoryStatus": completeness.get("category_status") or {},
        },
        "unavailableSources": [
            {"source": u.get("source"), "reason": u.get("reason")}
            for u in (report.get("unavailable_sources") or [])
        ],
        "errors": list(report.get("errors") or []),
    }


def build_agent_entries(
    state: RunStateModel,
    events: list[AgentStatusEvent],
    *,
    surface: str = SURFACE_V1,
) -> list[dict]:
    """Project the pipeline into the UI's agent card list.

    `surface` selects which registry entries are published. It defaults to V1 so
    the existing workspace keeps receiving exactly the eleven cards it has
    renderers for; V2 asks for the full pipeline.
    """
    grouped = _event_index(events)
    entries: list[dict] = []

    for position, definition in enumerate(agents_for_surface(surface), start=1):
        card = definition.card
        agent_events = grouped.get(definition.agent_id, [])
        duration = _agent_duration(agent_events)
        stage = STAGE_BY_ID[definition.stage]
        entry: dict[str, Any] = {
            "id": card,
            "index": position,
            "agent": definition.name,
            "purpose": definition.purpose,
            # The registry has carried this label all along; the UI was reading
            # its own copy out of the mock fixtures instead, so a live run
            # labelled its evidence handoffs from a hardcoded table.
            "handoff": definition.handoff,
            # Identity and stage membership travel with the card so no client
            # rebuilds the registry locally.
            "agentId": definition.agent_id,
            "stage": stage.id,
            "stageLabel": stage.label,
            "stageOrder": stage.order,
            "status": _agent_status(agent_events, state, card),
            "duration": duration,
            "lines": _lines_for(card, state, agent_events),
            "metrics": _metrics_for(card, state, duration, agent_events),
            "evidence": _evidence_for(card, state, agent_events),
        }

        visualization = _visualization_for(card, state, agent_events)
        if visualization:
            entry["visualization"] = visualization

        if card == "patch":
            entry["modifiedFiles"] = [
                p.get("file") for p in (state.patch_bundle or {}).get("patches", []) if p.get("file")
            ]
        if card == "blast":
            entry["pills"] = list((state.blast_graph or {}).get("auto_patch_scope") or [])

        entries.append(entry)

    return entries


# What a validation field reads when the pipeline never measured it. Distinct
# from both outcomes so a UI can render absence as absence.
NOT_MEASURED = "not measured"


def _tristate(value: Any, when_true: str, when_false: str) -> str:
    """Project a `bool | None` validation flag without collapsing `None`.

    `None` means the check did not run — a scoped validation that never reached
    the regression phase, or a result recorded before the field existed.
    Rendering that as the failing branch reported a failure the pipeline never
    observed, which is the one thing this projection layer must never do.
    """
    if value is None:
        return NOT_MEASURED
    return when_true if value else when_false


def _learning_observed(events: list[AgentStatusEvent]) -> bool:
    """Whether the learning pipeline actually ran during this run.

    A0.5 attaches a `learning` section to its completed event only when
    `learning_enabled` is on *and* the pipeline returned a profile. Absence of
    that section is the observable fact that learning did not run — which is
    why this reads the timeline rather than inferring from run status.
    """
    return any(
        isinstance((event.payload or {}).get("learning"), dict) for event in events
    )


def _stage_status(
    agent_statuses: list[str],
    state: RunStateModel,
    *,
    executed: bool | None = None,
) -> str:
    """Roll agent statuses up to their stage.

    Order matters: a stage containing a failure has failed regardless of what
    else finished, and a stage still running outranks one that finished. A
    stage whose agents were all routed around is `skipped`, not `completed` —
    reporting work that never happened as done is the error this whole layer
    exists to avoid.

    `executed` covers stages that own no agent on this surface: it must be the
    observed fact of whether that stage's work ran, never an assumption. `None`
    means the question is not applicable (the stage has agents to speak for it).
    """
    if not agent_statuses:
        # No agents on this surface. The stage exists in the workflow but has
        # nothing in the graph to report — `learning` is the standing case.
        # Completion is claimed only on evidence that it actually ran; a
        # terminal run is not itself evidence, and previously reporting
        # `completed` here asserted work that may never have happened.
        if executed:
            return "completed"
        if state.status in RUN_TERMINAL_STATES:
            return "skipped"
        return "waiting"
    if "failed" in agent_statuses:
        return "failed"

    terminal_run = state.status in RUN_TERMINAL_STATES
    # `running` and `retrying` describe work in flight. Once the run has
    # settled nothing is in flight, so reporting either would leave a finished
    # run showing a live spinner. The outcome is carried by the decision, not
    # by the stage.
    if "running" in agent_statuses and not terminal_run:
        return "running"
    if "retry" in agent_statuses and not terminal_run:
        return "retrying"
    if all(s == "skipped" for s in agent_statuses):
        return "skipped"
    return "completed"


def build_stage_progress(
    state: RunStateModel,
    events: list[AgentStatusEvent],
    *,
    surface: str = SURFACE_V2,
) -> list[dict]:
    """The stage registry with this run's live status folded in."""
    grouped = _event_index(events)
    entries = {
        definition.card: definition for definition in agents_for_surface(surface)
    }
    stage_executed = {"learning": _learning_observed(events)}

    stages: list[dict] = []
    for stage in sorted(STAGE_REGISTRY, key=lambda s: s.order):
        members = [d for d in entries.values() if d.stage == stage.id]
        agents: list[dict] = []
        for definition in members:
            agent_events = grouped.get(definition.agent_id, [])
            agents.append(
                {
                    "id": definition.card,
                    "agentId": definition.agent_id,
                    "name": definition.name,
                    "purpose": definition.purpose,
                    "handoff": definition.handoff,
                    "status": _agent_status(agent_events, state, definition.card),
                    "duration": _agent_duration(agent_events),
                    "message": agent_events[-1].message if agent_events else "",
                }
            )
        stages.append(
            {
                "id": stage.id,
                "label": stage.label,
                "order": stage.order,
                "purpose": stage.purpose,
                "status": _stage_status(
                    [a["status"] for a in agents],
                    state,
                    executed=stage_executed.get(stage.id),
                ),
                "agents": agents,
            }
        )
    return stages


# ------------------------------------------------------------- view models --


def repository_identity(state: RunStateModel, index_pointer: dict | None = None) -> dict:
    """Stable identity of the repository under repair.

    `repository_id` is the key every cross-run store is keyed by — repair
    memory, learned profiles, the digital twin. It is preferred from the index
    A0.5 published; when that layer has not run, it is derived with the same
    canonical function A0.5 itself uses, so the two can never disagree.

    `head_sha` is only ever reported when a run actually recorded one. A commit
    the pipeline never observed is left null rather than guessed.
    """
    pointer = index_pointer or {}

    repository_id = pointer.get("repository_id") or None
    if not repository_id and state.repo_path:
        from backend.services.repair_memory import repository_id as compute_repository_id

        repository_id = compute_repository_id(state.repo_path)

    head_sha = pointer.get("head_sha") or state.base_commit_sha or None

    return {
        "repositoryId": repository_id,
        "repositoryName": repo_display_name(state),
        "headSha": head_sha,
        "repositoryHash": pointer.get("repository_hash") or None,
    }


def build_workspace_header(
    state: RunStateModel,
    events: list[AgentStatusEvent],
    index_pointer: dict | None = None,
) -> dict:
    _decision, label = run_decision(state)
    return {
        "repository": repo_display_name(state),
        "branch": repo_branch(state),
        "shortRunId": short_run_id(state.run_id),
        "retries": state.retry_count,
        "executionTime": _fmt_duration(elapsed_seconds(state, events)),
        "decisionLabel": label,
        # The precheck's report, published verbatim. A blocked run's entire
        # explanation lives here — without it the client knows the run stopped
        # and not why, which is the state this sprint was meant to remove.
        "environment": state.environment,
        # A0.7 itself can fail to run (a crash in the probe, not a verdict about
        # the repository) — `environment_precheck` catches that and records it
        # in `state.errors` rather than blocking, so `environment` stays null.
        # Without this flag that reads identically to "the precheck never ran",
        # when it actually means "it ran and could not conclude anything".
        "environmentProbeError": any(e.get("agent") == "A0.7" for e in state.errors),
        **repository_identity(state, index_pointer),
    }


def _severity_label(state: RunStateModel) -> str:
    """Severity of the top-ranked static finding, or absence when none exist.

    The old floor was `"LOW"`, returned both for a genuine low-severity finding
    and for a run where A3 never produced one — a run blocked at A0.7 reported
    `LOW` severity for a scan that never happened. Same class of defect as the
    `0.0` axis defaults: an unmeasured value rendered as a measured one. An
    empty `prioritized` list now says so.
    """
    findings = (state.static_report or {}).get("prioritized") or []
    if not findings:
        return NOT_MEASURED
    top = float(findings[0].get("severity") or 0)
    for threshold, label in _SEVERITY_BY_SCORE:
        if top >= threshold:
            return label
    return "LOW"


def _trust_score(state: RunStateModel) -> float | None:
    """The composite trust score, or `None` when nothing was measured.

    Previously this summed four axes and divided by four regardless of how many
    had been measured, with `_pct` substituting `0.0` for the rest. A run whose
    security re-scan was skipped was therefore penalised for it twice: the axis
    showed 0%, and that 0 dragged the composite down by a quarter. The score
    that gates merges was being reduced by work nobody performed.

    Now the denominator is the number of real measurements. A measured zero
    still counts — it is a result.
    """
    axis = (state.pr_decision or {}).get("axis_scores") or {}
    if not axis:
        return None

    mean = measured_mean(
        [
            _score(axis.get("correctness")),
            _score(axis.get("security")),
            _score(axis.get("fidelity")),
            _score(axis.get("scope_risk")),
        ]
    )
    return None if mean is None else round(mean / 100, 2)


def build_executive_summary(state: RunStateModel, events: list[AgentStatusEvent]) -> dict:
    root = state.root_cause or {}
    repro = state.reproduction or {}
    mutation = state.mutation_result or {}
    decision, _label = run_decision(state)
    blast = state.blast_graph or {}
    score = mutation.get("mutation_score")

    bug = repro.get("exception_type") or "Static finding"
    if repro.get("failing_test"):
        bug = str(repro["failing_test"]).split("::")[-1]

    return {
        "repository": repo_display_name(state),
        "bug": bug[:48],
        "severity": _severity_label(state),
        "rootCause": str(root.get("root_cause") or root.get("summary") or "Pending analysis"),
        "issueTitle": (state.issue_contract or {}).get("description") or root.get("summary") or "Repository finding",
        "failingTest": repro.get("failing_test"),
        "changedFiles": sorted({p["file"] for p in (state.patch_bundle or {}).get("patches", []) if p.get("file")}),
        "impactScopeCount": len(blast.get("scope") or []) if state.blast_graph is not None else None,
        "recordedAttempts": len(state.recovery_attempts),
        "executedValidations": sum(a.get("validation_status") == "executed" for a in state.recovery_attempts),
        "latestFailure": next((a.get("failure") for a in reversed(state.recovery_attempts) if a.get("failure")), None),
        "verification": _summary_verification(state),
        # A4 publishes a confidence; a run where A4 never ran has none. The
        # `or 0` turned that absence into "0.0%", which reads as a measured
        # no-confidence verdict rather than an investigation that never happened.
        "confidence": (
            "not measured" if root.get("confidence") is None else f"{_pct(root['confidence'] * 100)}%"
        ),
        "filesAffected": len(blast.get("scope") or []),
        "attempts": total_attempts(state),
        # No 0.85 mutation gate exists anywhere in the pipeline — that figure was
        # invented for this string. The real gate A10 gates on is the correctness
        # score against SCORE_THRESHOLD, so show the measurement unqualified.
        "mutationScore": ("not scored" if score is None else f"{float(score):.2f}"),
        "runtime": _fmt_duration(elapsed_seconds(state, events)),
        # `_trust_score` is now nullable — formatting it unconditionally would
        # raise on a run where no axis was measured.
        "trustScore": (lambda t: "not measured" if t is None else f"{t:.2f}")(_trust_score(state)),
        "decision": decision,
        "decisionReason": _summary_decision_reason(state, decision),
    }


def _summary_verification(state: RunStateModel) -> list[dict]:
    """Explicit evidence states; absence is not a failed measurement."""
    repro = state.reproduction or {}
    root = state.root_cause or {}
    mutation = state.mutation_result or {}
    security = state.security_result or {}
    rows = []

    def row(label, status, detail):
        rows.append({"label": label, "status": status, "detail": detail})

    reproduced = repro.get("status")
    row("Reproduction", "passed" if reproduced == "CONFIRMED" else "unavailable" if reproduced == "INFRA_ERROR" else "incomplete" if reproduced else "not_run",
        "Bug reproduced" if reproduced == "CONFIRMED" else repro.get("infra_detail") or "Not confirmed" if reproduced else "Not run")
    diagnosis = root.get("diagnosis_status")
    citations = root.get("citations") or []
    supported = diagnosis == "supported" and bool(citations) and all(c.get("claim_support_status") == "supported" for c in citations)
    row("Diagnosis", "passed" if supported else "failed" if diagnosis == "contradicted" else "unavailable" if diagnosis == "unavailable" else "incomplete" if root else "not_run",
        "Claims supported" if supported else "Contradicted" if diagnosis == "contradicted" else "Unavailable" if diagnosis == "unavailable" else "Unconfirmed" if root else "Not run")
    for label, key in (("Target tests", "target_test_passed"), ("Regression tests", "regression_tests_passed")):
        value = mutation.get(key)
        unavailable = mutation.get("pytest_available") is False
        row(label, "unavailable" if unavailable else "passed" if value is True else "failed" if value is False else "not_run",
            "Execution unavailable" if unavailable else "Passed" if value is True else "Failed" if value is False else "Not run")
    status = mutation.get("mutation_status")
    if status == "scored":
        killed, survived = mutation.get("killed_mutants"), mutation.get("survived_mutants")
        mutation_state = (
            "failed" if mutation.get("mutant_survived") or (survived or 0) > 0
            else "passed" if survived == 0 and killed is not None and killed > 0
            else "incomplete"
        )
        row("Mutation testing", mutation_state,
            f"{killed if killed is not None else 'Unknown'} killed · {survived if survived is not None else 'unknown'} survived")
    else:
        row("Mutation testing", "not_run" if not status or status == "not_run" else "unavailable",
            mutation.get("mutation_unavailable_reason") or ("Not run — prerequisite tests failed" if mutation.get("target_test_passed") is False or mutation.get("regression_tests_passed") is False else "Not run" if not status or status == "not_run" else "Unavailable"))
    row("Security re-scan", "not_run" if not security else "unavailable" if security.get("security_score") is None else "failed" if security.get("rejected") else "passed",
        "Not run" if not security else "Unmeasured" if security.get("security_score") is None else "New findings rejected" if security.get("rejected") else "Passed")
    return rows


def _summary_decision_reason(state: RunStateModel, decision: str) -> str:
    """Why the run ended where it did.

    "All trust gates satisfied" was the default for *any* run with no review
    note — including a blocked run that never reached a trust gate, and a draft
    PR that was drafted precisely because a gate was not satisfied. It is only
    true of an auto-mergeable run, so only an auto-mergeable run may say it.
    """
    note = str((state.pr_decision or {}).get("review_note") or "").strip()
    if note:
        return note[:160]
    # `run_decision` speaks the UI's vocabulary, where `auto_mergeable` is
    # "merge". Testing for the router's word here would have meant the sentence
    # never appeared on the one run entitled to it.
    if decision == "merge":
        return "All trust gates satisfied."
    if decision == "blocked":
        if state.recovery_blocked_reason:
            return state.recovery_blocked_reason
        return "The repository's environment was not prepared, so the pipeline stopped before validation."
    return "No review note was recorded for this decision."


def _pytest_unavailable(mutation: dict) -> bool:
    """True when validation never ran because the target repo had no usable pytest.

    This is the difference between "the patch is wrong" and "we could not tell",
    and the report must not present the second as the first.

    A8 now records this as a fact (`pytest_available`), decided where pytest is
    actually invoked. The string search below is retained only for runs stored
    before that field existed — deciding it here was always a guess about a
    subprocess this layer never saw, and it left the projection printing an
    explanation that contradicted the score sitting next to it.
    """
    if mutation.get("pytest_available") is False:
        return True

    brief = mutation.get("failure_brief") or {}
    failure = brief.get("validation_failure") or {}
    blob = " ".join(
        str(part or "")
        for part in (brief.get("stack_trace"), failure.get("traceback"), failure.get("pytest_stderr"))
    )
    return "No module named pytest" in blob or "no tests ran" in blob.lower()


def _mutation_evidence(mutation: dict) -> dict:
    """Evidence line whose text matches its own boolean.

    The label used to read "Mutation validation passed" whether or not it had,
    so a red cross sat next to the word "passed".
    """
    if not mutation:
        return {"ok": False, "text": "Mutation validation did not run"}
    if _pytest_unavailable(mutation):
        return {"ok": False, "text": "Mutation validation blocked — pytest unavailable in target repo"}
    if not mutation.get("pytest_passed"):
        return {"ok": False, "text": "Mutation validation failed — tests did not pass"}
    if mutation.get("mutant_survived"):
        return {"ok": False, "text": "Mutation validation failed — mutant survived"}
    if mutation.get("mutation_score") is None:
        return {"ok": False, "text": "Mutation validation incomplete — no score produced"}
    return {"ok": True, "text": "Mutation validation passed"}


def _rejection_reason(state: RunStateModel, mutation: dict) -> str:
    """Plain-language account of why no patch cleared validation.

    The UI used to assert "rejected by mutation testing" unconditionally, which
    was wrong whenever mutation testing never got to run.
    """
    attempts = total_attempts(state)
    if not mutation:
        return f"{attempts} patch attempts were made; none reached validation."
    if _pytest_unavailable(mutation):
        return (
            f"{attempts} patch attempts could not be validated: pytest was unavailable in the "
            "target repository, so neither the test suite nor mutation testing ran. The axis "
            "scores below reflect unverified work, not failed work."
        )
    if not mutation.get("pytest_passed"):
        return f"{attempts} patch attempts were rejected: the test suite did not pass."
    if mutation.get("mutant_survived"):
        return (
            f"{attempts} patch attempts were rejected by mutation testing — a mutant survived, "
            "meaning the tests do not actually constrain the repaired behaviour."
        )
    return f"{attempts} patch attempts were made and validation passed."


def _decision_reason(state: RunStateModel) -> str:
    """Why the run routed where it did, taken from the router's own note."""
    if state.status == "blocked" and state.recovery_blocked_reason:
        return state.recovery_blocked_reason
    decision = state.pr_decision or {}
    note = decision.get("review_note") or decision.get("description_why")
    return str(note).strip() if note else ""


def _agents_run(events: list[AgentStatusEvent]) -> int:
    """Agents that actually emitted work, rather than the registry's length.

    A pipeline that routes around a stage — a skipped security re-scan, say —
    did not run eleven agents, and saying so overstated the run.
    """
    return len({e.agent_id for e in events if e.agent_id in CARD_BY_BACKEND_ID})


def _axis_view(name: str, label: str, value: float | None) -> dict:
    """One axis row: value plus a tri-state threshold verdict.

    `meetsLowThreshold` is `None`, not `False`, when the axis was never
    measured — `meets_threshold` itself returns `False` for `None` because
    that is the right answer for a gate ("absent evidence does not clear a
    bar"), but a *display* that shows "does not meet threshold" for an axis
    nobody measured reads as a failure the pipeline never observed.
    """
    return {
        "name": name,
        "label": label,
        "value": value,
        "measured": value is not None,
        "lowThreshold": SCORE_THRESHOLD,
        "meetsLowThreshold": None if value is None else value >= SCORE_THRESHOLD,
    }


def build_mergeability_decision(state: RunStateModel) -> dict | None:
    """A10's routing decision, projected for the workspace panel.

    A10 does not re-verify correctness or security — `axes.correctness` and
    `axes.security` are A8's and A9's own scores, passed through unchanged.
    A10 verifies exactly one thing itself (the MCI phantom check) and
    computes two axes of its own (fidelity, scope risk); everything else
    here is A10 reading evidence other agents produced.

    `hardGates` is `a10_routing.gate_checks`'s trace of the same ten-gate
    chain `hard_draft_reason` uses to route the PR — same order, same wording
    when a gate fires, and it stops changing the moment one does: gates after
    the firing one are `checked: false`, never drawn as passed.

    `routingModifiers` only carries real values once every hard gate is
    clear; a run that hard-blocked earlier never reached these facts, so they
    are `None` rather than a guess about what they would have been.

    Returns `None` before A10 has produced a routing decision.
    """
    decision_data = state.pr_decision
    if decision_data is None:
        return None

    axis_dict = decision_data.get("axis_scores") or {}
    axis = AxisScores(
        correctness=axis_dict.get("correctness"),
        security=axis_dict.get("security"),
        fidelity=axis_dict.get("fidelity"),
        scope_risk=axis_dict.get("scope_risk"),
    )
    phantom_detected = bool(decision_data.get("phantom_changes_detected"))
    # `gate_checks` only tests `bool(phantoms)` — the actual entity names are
    # not part of the persisted decision, so a non-empty placeholder is the
    # honest re-derivation of "phantoms were detected", not a fabrication of
    # what they were.
    phantoms = {"phantom"} if phantom_detected else set()

    checks = gate_checks(state, axis, phantoms)
    hard_gates_clear = all(c.passed for c in checks)

    repository_evidence = _repository_evidence(state)

    proof = state.proof_bundle or {}
    steps = [
        {
            "name": s.get("name"),
            "command": s.get("command"),
            "baseCommit": s.get("base_commit"),
            "patchCommit": s.get("patch_commit"),
            "expectedResult": s.get("expected_result"),
            "timeoutSeconds": s.get("timeout_seconds"),
            "isTargeted": bool(s.get("is_targeted", True)),
        }
        for s in proof.get("steps") or []
    ]

    _decision, label = run_decision(state)

    return {
        "prType": decision_data.get("pr_type") or "draft",
        "decisionLabel": label,
        "reviewNote": decision_data.get("review_note"),
        "trust": _trust_score(state),
        "axes": [
            _axis_view("correctness", "Correctness", axis.correctness),
            {
                **_axis_view("security", "Security", axis.security),
                # Security alone is held to a stricter bar for auto-merge
                # eligibility (`technical_validation_passed`'s 90) than the
                # generic "low axis" gate above uses (80) — a score can clear
                # 80 and still block auto-merge. Nothing else in the product
                # shows this asymmetry, so it is its own field rather than
                # folded into `lowThreshold`.
                "autoMergeThreshold": SECURITY_TECHNICAL_THRESHOLD,
                "meetsAutoMergeThreshold": (
                    None
                    if axis.security is None
                    else meets_threshold(axis.security, SECURITY_TECHNICAL_THRESHOLD)
                ),
            },
            _axis_view("fidelity", "Fidelity", axis.fidelity),
            _axis_view("scope_risk", "Scope Safety", axis.scope_risk),
        ],
        "hardGates": [
            {
                "code": c.code,
                "label": c.label,
                "checked": c.checked,
                "passed": c.passed,
                "detail": c.detail,
            }
            for c in checks
        ],
        "routingModifiers": {
            "hardGatesClear": hard_gates_clear,
            "citationReviewNeeded": citation_review_needed(state) if hard_gates_clear else None,
            "reproductionConfidence": state.reproduction_confidence if hard_gates_clear else None,
            "securityMeetsAutoMergeThreshold": (
                meets_threshold(axis.security, SECURITY_TECHNICAL_THRESHOLD)
                if hard_gates_clear
                else None
            ),
        },
        "phantomChangesDetected": phantom_detected,
        "prUrl": decision_data.get("pr_url"),
        "descriptionWhy": str(decision_data.get("description_why") or ""),
        "descriptionWhat": str(decision_data.get("description_what") or ""),
        "proofBundle": (
            {
                "bundleHash": proof.get("bundle_hash") or None,
                "reproductionConfidence": proof.get("reproduction_confidence"),
                "steps": steps,
            }
            if proof
            else None
        ),
        "repositoryEvidence": repository_evidence,
    }


def _module_of(path: str) -> str:
    """Directory grouping used only for the mergeability panel's compact
    "affected modules" chip — not a first-class concept anywhere else in the
    pipeline. Real path prefixes only, no inference about package structure.
    """
    head, _, _ = path.rpartition("/")
    return head or "."


def _repository_evidence(state: RunStateModel) -> dict | None:
    """A compact scale-invariant summary for the mergeability panel's
    "Repository Evidence" strip. Every count here is a real, already-computed
    fact re-read from other agents' own state — A1's SIG size, the files A7
    actually patched, A5's blast-radius scope and traversal edges. Nothing is
    sampled, estimated, or recomputed with different logic than the panel
    that owns each fact (`build_semantic_graph`, `build_blast_impact`).

    A count is `None` only when the agent that would have produced it never
    ran — never a fabricated 0 standing in for "not measured", the same
    discipline `services/measurement.py` established for the axis scores.
    Returns `None` only when every one of A1/A5/A7 is silent, i.e. before the
    run has produced anything this strip could summarize.
    """
    sig_files: dict = (state.sig or {}).get("files") or {}
    files_analyzed = len(sig_files) if state.sig is not None else None

    patches = (state.patch_bundle or {}).get("patches") or []
    changed_files = sorted({p.get("file") for p in patches if p.get("file")})

    blast = state.blast_graph
    blast_scope_files = sorted(
        {s.get("path") for s in ((blast or {}).get("scope") or []) if s.get("path")}
    )
    dependency_edge_count = len((blast or {}).get("edges") or []) if blast is not None else None

    if state.sig is None and blast is None and not patches:
        return None

    affected_modules = sorted({_module_of(p) for p in {*changed_files, *blast_scope_files}})

    return {
        "filesAnalyzed": files_analyzed,
        "changedFiles": changed_files,
        "affectedModules": affected_modules,
        "blastScopeFiles": blast_scope_files,
        "dependencyEdgeCount": dependency_edge_count,
    }


def build_run_report(state: RunStateModel, events: list[AgentStatusEvent]) -> dict:
    decision, label = run_decision(state)
    axis = (state.pr_decision or {}).get("axis_scores") or {}
    root = state.root_cause or {}
    citations = root.get("citations") or []
    mutation = state.mutation_result or {}
    repro = state.reproduction or {}
    proof = state.proof_bundle or {}
    primary = citations[0] if citations else {}

    security = state.security_result or {}
    evidence = [
        {"ok": repro.get("status") == "CONFIRMED", "text": "Runtime reproduced"},
        {"ok": root.get("diagnosis_status") == "supported" and bool(citations)
         and all(c.get("claim_support_status") == "supported" for c in citations),
         "text": "Root-cause claims supported"},
        {"ok": bool(state.blast_graph), "text": "Blast radius analyzed"},
        _mutation_evidence(mutation),
        # A skipped re-scan is not a clean re-scan — report it as not run. The
        # same applies when A9 ran but no scanner was installed: it rejects
        # nothing because it examined nothing.
        {"ok": False, "text": "Security re-scan not run"}
        if not security or security.get("security_score") is None
        else {"ok": not security.get("rejected"), "text": "Security re-scan clean"},
    ]
    if state.validation_exhausted:
        evidence.append({"ok": False, "text": f"Retry exhausted ({state.retry_count} retries)"})

    return {
        "runId": state.run_id,
        "reportedIssue": state.issue_contract,
        "generatedTest": state.generated_test,
        "recoveryAttempts": [
            {
                "number": attempt.get("number"),
                "target": attempt.get("target"),
                "diagnosis": attempt.get("diagnosis"),
                "patchHash": attempt.get("patch_hash"),
                "failingTest": attempt.get("failing_test"),
                "failure": attempt.get("failure"),
                "validationStatus": attempt.get("validation_status"),
                "category": attempt.get("failure_category"),
                "decision": attempt.get("recovery_decision"),
                "reason": attempt.get("reason"),
                "changedFromPrevious": attempt.get("changed_from_previous"),
            }
            for attempt in state.recovery_attempts
        ],
        "shortRunId": short_run_id(state.run_id),
        "repository": repo_display_name(state),
        "branch": repo_branch(state),
        "decision": decision,
        "decisionLabel": label,
        "prUrl": (state.pr_decision or {}).get("pr_url"),
        "publication": state.publication_result,
        "trustScore": _trust_score(state),
        # The gate A10 actually applies, normalised to the 0-1 scale the trust
        # score uses. The previous 0.9 matched no threshold in the codebase, so
        # runs were reported as falling short of a bar that did not exist.
        "trustThreshold": round(SCORE_THRESHOLD / 100, 2),
        # Prose stays whole. These fields were previously sliced at 60 and 180
        # characters and dropped into a fixed English sentence in the UI, which
        # produced half-words glued into a template that described a different
        # bug than the one the run actually found.
        "rootCause": {
            "summary": str(root.get("summary") or "").strip(),
            "statement": str(root.get("root_cause") or "").strip(),
            "location": f"{primary.get('file', '—')}:{primary.get('line', '')}",
            "claim": str(primary.get("claim") or "").strip(),
            "verified": bool(primary.get("verified")),
        },
        "rejection": {
            "attempts": total_attempts(state),
            "survivors": 1 if mutation.get("mutant_survived") else 0,
            # None means "never measured". Coercing it to 0.0 rendered a 0.00
            # that read as a real measurement of a worthless patch.
            "score": (
                float(mutation["mutation_score"])
                if mutation.get("mutation_score") is not None
                else None
            ),
            # The real validation gate: A10 rejects on `correctness_score` against
            # SCORE_THRESHOLD. The 0.85 "mutation threshold" shown here before
            # existed nowhere in the pipeline.
            "correctnessScore": (
                float(mutation["correctness_score"])
                if mutation.get("correctness_score") is not None
                else None
            ),
            "correctnessThreshold": SCORE_THRESHOLD,
            "reason": _rejection_reason(state, mutation),
        },
        "decisionReason": _decision_reason(state),
        # Every reason this run cannot be auto-merged, from the module that
        # decides it. A10's `review_note` carries only the *first* one it hit,
        # so a run blocked for three reasons showed one and the other two were
        # unrecoverable from the UI. These come from `trust_gating.draft_reasons`
        # — the same computation that sets `force_draft_pr`, so the explanation
        # and the routing cannot disagree.
        "draftReasons": [
            {"code": r.code, "detail": r.detail} for r in draft_reasons(state)
        ],
        # `value: null` where the pipeline measured nothing, and tone
        # `"unknown"` alongside it. An axis that never ran is neither passing
        # nor failing, and colouring it red accused the run of failing a check
        # nobody performed.
        #
        # Scope Safety note: `compute_scope_risk` returns 90 when nothing needs
        # human review and falls toward 20 as review items pile up, so high is
        # good — the same direction as the other three axes, and the same
        # direction A10 gates on. Labelling it "Scope Risk" inverted that: 20%
        # read as low risk when it actually meant the scope was barely contained.
        "trust": [
            {"label": label, "value": value, "tone": _tone(value)}
            for label, value in [
                ("Correctness", _score(axis.get("correctness"))),
                ("Security", _score(axis.get("security"))),
                ("Fidelity", _score(axis.get("fidelity"))),
                ("Scope Safety", _score(axis.get("scope_risk"))),
            ]
        ],
        "files": [p.get("file") for p in (state.patch_bundle or {}).get("patches", []) if p.get("file")],
        "evidence": evidence,
        "proofBundle": f"sha256:{str(proof.get('bundle_hash') or '')[:8]}…{str(proof.get('bundle_hash') or '')[-4:]}"
        if proof.get("bundle_hash")
        else "—",
        "agentCount": _agents_run(events),
        "totalDurationSeconds": round(elapsed_seconds(state, events), 1),
    }


def _attempt_action_clause(patch_payload: dict) -> str:
    """What this attempt generated, and what prompted it."""
    metrics = patch_payload.get("a7_patch_metrics") or {}
    target = metrics.get("target_file")
    reason = metrics.get("retry_reason")
    on_target = f" on {target}" if target else ""

    if not reason:
        return f"Initial patch{on_target}"
    prompt = " from runtime evidence" if metrics.get("runtime_prompt") else ""
    return f"Regenerated after {str(reason).replace('_', ' ')}{prompt}{on_target}"


def _attempt_outcome_clause(validation_payload: dict) -> str:
    """Why this attempt was rejected, in the most specific terms available."""
    if not validation_payload:
        return "awaiting validation"

    assertion = validation_payload.get("assertion_message")
    failing = validation_payload.get("failing_test")
    if assertion:
        return f"{failing}: {assertion}" if failing else str(assertion)
    if failing:
        return f"failing test {failing}"

    new_failures = validation_payload.get("new_failures") or []
    if new_failures:
        listed = ", ".join(str(f) for f in new_failures[:2])
        return f"introduced {len(new_failures)} new test failure(s): {listed}"

    if validation_payload.get("mutant_survived"):
        return "mutant survived — the test passes without validating the fix"
    if validation_payload.get("regression_tests_passed") is False:
        return "regression tests failed"
    if validation_payload.get("pytest_passed") is False:
        return "pytest failed"
    if validation_payload.get("pytest_passed"):
        return "validation passed"
    return "validation inconclusive"


def _attempt_detail(patch_payload: dict, validation_payload: dict) -> str:
    """Say what this specific attempt did and how it turned out."""
    action = _attempt_action_clause(patch_payload)
    outcome = _attempt_outcome_clause(validation_payload)
    return f"{action} — {outcome}"[:160]


def _attempt_score_label(validation_payload: dict) -> str:
    """Report the score that was actually measured, or say none was."""
    mutation_score = validation_payload.get("mutation_score")
    if mutation_score is not None:
        return f"mutation {float(mutation_score):.2f}"
    # mutmut only runs once pytest passes, so a failed attempt has no mutation
    # score at all. Fall back to the correctness score, which is always computed.
    correctness = validation_payload.get("correctness_score")
    if correctness is not None:
        return f"correctness {float(correctness) / 100:.2f}"
    return "not scored"


def build_repair_attempts(state: RunStateModel, events: list[AgentStatusEvent]) -> dict:
    """Reconstruct each repair attempt from its own patch/validation event pair.

    `state` only ever holds the *last* attempt's mutation result, so deriving
    the rows from it made every attempt render identically. The per-attempt
    truth is in the event history: one A7 (generate) and one A8 (validate)
    completion per cycle, each carrying that cycle's own payload.
    """
    grouped = _event_index(events)
    patch_events = [e for e in grouped.get("A7", []) if e.status == "completed"]
    validation_events = [e for e in grouped.get("A8", []) if e.status == "completed"]

    mutation = state.mutation_result or {}
    attempts: list[dict] = []

    for n, patch_event in enumerate(patch_events, start=1):
        patch_payload = patch_event.payload or {}
        validation_event = validation_events[n - 1] if n <= len(validation_events) else None
        validation_payload = (validation_event.payload or {}) if validation_event else {}

        if validation_event is None:
            result = "Validation Pending"
        elif validation_payload.get("pytest_passed") and not validation_payload.get(
            "patch_retry_required"
        ):
            result = "Validation Passed"
        else:
            result = "Validation Failed"

        attempts.append(
            {
                "n": n,
                "action": "Generate Patch",
                "detail": _attempt_detail(patch_payload, validation_payload),
                "result": result,
                # Kept numeric for the existing model; `scoreLabel` is what the
                # UI shows, so an unmeasured attempt never renders as 0.00.
                "mutation": float(validation_payload.get("mutation_score") or 0),
                "scoreLabel": _attempt_score_label(validation_payload),
            }
        )

    # Older runs predate the per-attempt payload fields; fall back to the
    # attempt count so the section is never silently empty.
    if not attempts and mutation:
        for n in range(1, (total_attempts(state)) + 1):
            attempts.append(
                {
                    "n": n,
                    "action": "Generate Patch",
                    "detail": "Attempt detail unavailable for this run.",
                    "result": "Validation Failed",
                    "mutation": 0.0,
                    "scoreLabel": "not scored",
                }
            )

    if state.validation_exhausted:
        message = f"Validation did not pass after {len(attempts)} attempts."
    elif mutation.get("mutant_survived"):
        message = "A surviving mutant showed the patch was not validated by the test."
    elif mutation.get("pytest_passed"):
        message = "Validation passed."
    else:
        message = "Validation incomplete."

    return {
        "attempts": attempts,
        "failureMessage": message,
        "nextStepLabel": "Proceed to Mergeability Assessment",
    }


def build_sidebar_entry(state: RunStateModel) -> dict:
    return {
        "id": state.run_id,
        "name": run_display_name(state),
        "status": sidebar_status(state),
        "time": _relative_time(state.created_at),
    }


def group_runs_by_repository(states: list[RunStateModel]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for state in states:
        grouped.setdefault(repo_display_name(state), []).append(build_sidebar_entry(state))
    return [{"name": name, "runs": runs} for name, runs in grouped.items()]
