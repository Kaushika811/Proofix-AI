"""Backend-for-frontend routes consumed by the React workspace.

These endpoints mirror `frontend/src/lib/api.ts` exactly. They return view
models (never raw pipeline JSON) built by `services/ui_projection.py`, so the
UI stays decoupled from agent internals.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.agents.a5_5_context_engineering import load_context_package
from backend.agents.repository_intelligence import (
    INTELLIGENCE_STORE_KEY,
    load_repository_intelligence,
)
from backend.api.deps import get_runner, get_settings_dep, get_store
from backend.config import Settings
from backend.models.issue import IssueFields
from backend.orchestrator.runner import PipelineRunner
from backend.services.capability_layer import infer_capabilities
from backend.services.graph_cache import get_knowledge_graph
from backend.services.run_chat import answer_question
from backend.services.ui_projection import (
    SURFACE_V1,
    SURFACE_V2,
    build_agent_entries,
    build_blast_impact,
    build_dependency_risk,
    build_executive_summary,
    build_investigation,
    build_repair_attempts,
    build_repair_plan,
    build_reproduction_evidence,
    build_mergeability_decision,
    build_mutation_validation,
    build_run_report,
    build_security_rescan,
    build_semantic_graph,
    build_stage_progress,
    build_static_findings,
    build_workspace_header,
    group_runs_by_repository,
    repo_display_name,
)
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ui"])


class CreateRunBody(IssueFields):
    repo_path: str | None = Field(None, description="Local path to the target repo")
    url: str | None = Field(None, description="Repository URL, used when repo_path is absent")


class ValidateRepoBody(BaseModel):
    url: str


class ChatBody(BaseModel):
    question: str


def _local_branch(path: Path) -> str | None:
    """Branch checked out at `path`, read straight from `.git/HEAD`."""
    try:
        head = (path / ".git" / "HEAD").read_text().strip()
    except OSError:
        return None
    if head.startswith("ref: refs/heads/"):
        return head.split("refs/heads/", 1)[1]
    return head[:7] or None


async def _load_run(store: RedisStore, run_id: str) -> RunStateModel:
    state = await store.load_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    return state


async def _load_events(store: RedisStore, run_id: str):
    return await store.get_events(run_id, count=500)


async def _index_pointer(store: RedisStore, run_id: str) -> dict | None:
    """The repository-index pointer A0.5 published, when the layer ran.

    Read directly rather than through `load_repository_intelligence`: the header
    needs identity only, and following the pointer into the cross-run cache
    would deserialize the whole index on every header request.
    """
    try:
        pointer = await store.get_json(run_id, INTELLIGENCE_STORE_KEY)
    except Exception:  # noqa: BLE001 — identity is additive, never fatal
        logger.warning("index_pointer_read_failed", extra={"run_id": run_id})
        return None
    return pointer if isinstance(pointer, dict) else None


@router.get("/repositories")
async def list_repositories(store: Annotated[RedisStore, Depends(get_store)]) -> list[dict]:
    """Runs grouped by repository, most recent first — powers the sidebar."""
    run_ids = await store.list_run_ids(limit=100)
    states: list[RunStateModel] = []
    stale: list[str] = []
    for run_id in run_ids:
        state = await store.load_state(run_id)
        if state is None:
            stale.append(run_id)
            continue
        states.append(state)
    await store.prune_run_index(stale)
    return group_runs_by_repository(states)


@router.post("/repositories/validate")
async def validate_repository(body: ValidateRepoBody) -> dict:
    """Resolve a repo reference to display metadata without hitting GitHub."""
    raw = body.url.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Repository reference required")

    local = Path(raw).expanduser()
    if local.exists() and local.is_dir():
        return {
            "owner": local.resolve().parent.name,
            "name": local.resolve().name,
            "language": "Python" if any(local.rglob("*.py")) else None,
            # Read the checked-out branch rather than assuming "main".
            "branch": _local_branch(local) or "—",
            # A directory on disk has no visibility to report. Saying "Private"
            # stated something we never checked.
            "visibility": "Unknown",
            "htmlUrl": str(local.resolve()),
        }

    cleaned = raw.removesuffix(".git").rstrip("/")
    parts = [p for p in cleaned.replace("https://", "").replace("http://", "").split("/") if p]
    if len(parts) < 3 or "github.com" not in parts[0]:
        raise HTTPException(status_code=404, detail="Repository not found")

    owner, name = parts[1], parts[2]
    # Nothing here has contacted GitHub, so branch, language and visibility are
    # genuinely unknown. They were previously reported as "main"/"Public",
    # which was a guess presented as metadata.
    return {
        "owner": owner,
        "name": name,
        "language": None,
        "branch": None,
        "visibility": "Unknown",
        "htmlUrl": f"https://github.com/{owner}/{name}",
    }


@router.post("/runs")
async def create_run(
    body: CreateRunBody,
    background_tasks: BackgroundTasks,
    store: Annotated[RedisStore, Depends(get_store)],
    runner: Annotated[PipelineRunner, Depends(get_runner)],
) -> dict:
    target = body.repo_path or body.url
    if not target:
        raise HTTPException(status_code=400, detail="repo_path or url is required")

    run_id = str(uuid.uuid4())
    state = await store.init_run(run_id, target, body.issue_hint, body.issue_contract())
    background_tasks.add_task(runner.execute, run_id)
    return {
        "run_id": run_id,
        "runId": run_id,
        "status": "pending",
        "repository": repo_display_name(state),
    }


@router.get("/runs")
async def list_runs(store: Annotated[RedisStore, Depends(get_store)]) -> list[dict]:
    run_ids = await store.list_run_ids(limit=50)
    out: list[dict] = []
    for run_id in run_ids:
        state = await store.load_state(run_id)
        if state is None:
            continue
        events = await _load_events(store, run_id)
        out.append(build_workspace_header(state, events) | {"runId": run_id, "status": state.status})
    return out


@router.get("/runs/{run_id}")
async def get_run_header(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    state = await _load_run(store, run_id)
    events = await _load_events(store, run_id)
    pointer = await _index_pointer(store, run_id)
    lifecycle = await store.get_lifecycle_events(run_id)
    return build_workspace_header(state, events, pointer) | {
        "runId": state.run_id,
        "status": state.status,
        "currentAgent": state.current_agent,
        # The authoritative lifecycle record, so a client opening a finished run
        # learns how it ended without replaying the socket.
        "lifecycle": [e.model_dump(mode="json") for e in lifecycle],
    }


@router.get("/runs/{run_id}/summary")
async def get_run_summary(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    state = await _load_run(store, run_id)
    events = await _load_events(store, run_id)
    return build_executive_summary(state, events)


@router.get("/runs/{run_id}/agents")
async def get_run_agents(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    surface: Annotated[
        str,
        Query(
            pattern="^(v1|v2)$",
            description=(
                "Which registry surface to publish. `v1` (default) returns the "
                "agents the workspace has cards for; `v2` returns the full "
                "pipeline. They differ by A5.5 alone — A0.5 joined the product "
                "surface in Phase 2 — and collapse to one value when it lands."
            ),
        ),
    ] = SURFACE_V1,
) -> list[dict]:
    state = await _load_run(store, run_id)
    events = await _load_events(store, run_id)
    return build_agent_entries(state, events, surface=surface)


@router.get("/runs/{run_id}/stages")
async def get_run_stages(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    surface: Annotated[str, Query(pattern="^(v1|v2)$")] = SURFACE_V2,
) -> dict:
    """Stage → agent structure with each stage's live status.

    Published so no client hardcodes stage membership, ordering or labels; the
    registry is the single source of truth for all three.
    """
    state = await _load_run(store, run_id)
    events = await _load_events(store, run_id)
    return {
        "runId": run_id,
        "stages": build_stage_progress(state, events, surface=surface),
    }


@router.get("/runs/{run_id}/semantic-graph")
async def get_run_semantic_graph(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A1's Semantic Intent Graph — architectural role per file.

    Returns the projection built by `build_semantic_graph`: every file A1
    indexed with its role, import edges, criticality and churn signal, plus
    the role counts computed over the whole set. This is A1's own artifact,
    distinct from A0.5's Repository Knowledge Graph — A0.5 answers what exists
    and how it is wired, A1 answers what the code appears to do.

    A 404 means A1 has not published a SIG for this run yet, which the caller
    renders as pending rather than substituting an empty graph.
    """
    state = await _load_run(store, run_id)
    projection = build_semantic_graph(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No semantic graph for this run — A1 has not completed",
        )
    return projection


@router.get("/runs/{run_id}/dependency-risk")
async def get_run_dependency_risk(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A2's dependency/CVE reachability report.

    Returns the projection built by `build_dependency_risk`: every advisory A2
    found, narrowed by reachability against A1's import graph, plus the total
    dependency count the manifest declared. This is A2's own artifact — a
    reachability verdict over OSV advisories — distinct from A1's semantic
    roles and A0.5's structural graph.

    A 404 means A2 has not run for this run yet. A 200 with an empty
    `findings` list means A2 ran and found nothing — those are different facts
    and the caller must render them differently.
    """
    state = await _load_run(store, run_id)
    projection = build_dependency_risk(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No dependency analysis for this run — A2 has not completed",
        )
    return projection


@router.get("/runs/{run_id}/static-findings")
async def get_run_static_findings(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A3's static-analysis findings.

    Returns the projection built by `build_static_findings`: which scanners
    ran and how (`scannerStatus`), the raw finding count before clustering,
    and the ranked findings with every factor of `blast_radius_score`
    (`severity`, `criticality`, `churnWeight`, tool count) so the client can
    show why a finding outranked another without recomputing the formula.

    A 404 means A3 has not produced a durable result for this run yet. A 200
    with an empty `findings` list means A3 ran and ranked nothing — those are
    different facts and the caller must render them differently.
    """
    state = await _load_run(store, run_id)
    projection = build_static_findings(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No static analysis for this run — A3 has not completed",
        )
    return projection


@router.get("/runs/{run_id}/security")
async def get_run_security_rescan(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A9's post-patch security re-scan.

    Returns the projection built by `build_security_rescan`: whether the
    re-scan measured anything at all (`scannersRun`), the real differential
    verdict against A3's baseline (`verdict`, `rejected`), every newly
    introduced finding A9 found, and `reconciliation` — the comparison itself,
    one lane per scanner, pairing each carried finding with its baseline
    occurrence and the number of lines the patch moved it by. No severity: A9
    never measures it.

    A 404 means A9 has not produced a durable result for this run yet. A 200
    with an empty `newFindings` list means A9 ran and found nothing new —
    that is different from A9 never having run, and the caller must render
    them differently.
    """
    state = await _load_run(store, run_id)
    projection = build_security_rescan(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No security re-scan for this run — A9 has not completed",
        )
    return projection


@router.get("/runs/{run_id}/mutation")
async def get_run_mutation_validation(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A8's scoped-validation + mutation gauntlet.

    Returns the projection built by `build_mutation_validation`: which of the
    three gates (target test, regression suite, mutmut) the run reached
    (`stage`), the real mutant tallies A8 measured (`mutationStatus` says
    whether a score exists at all), and the regression diff against A3.5's
    baseline (`newFailures` vs `preExistingFailures`).

    A 404 means A8 has not produced a durable result for this run yet.
    """
    state = await _load_run(store, run_id)
    projection = build_mutation_validation(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No mutation validation for this run — A8 has not completed",
        )
    return projection


@router.get("/runs/{run_id}/decision")
async def get_run_mergeability_decision(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A10's routing decision.

    Returns the projection built by `build_mergeability_decision`: the four
    axes (with A10's own stricter security auto-merge threshold alongside the
    generic one), the ten-gate hard-gate trace in the same order and wording
    the router itself uses, the routing modifiers that only apply once every
    gate clears, and the PR/proof-bundle outcome A10 actually produced.

    A 404 means A10 has not produced a routing decision for this run yet.
    """
    state = await _load_run(store, run_id)
    projection = build_mergeability_decision(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No mergeability decision for this run — A10 has not completed",
        )
    return projection


@router.get("/runs/{run_id}/reproduction")
async def get_run_reproduction(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A3.5's failure-reproduction evidence.

    Returns the projection built by `build_reproduction_evidence`: the real
    outcome of A3.5's full-suite pytest gate — command, exit code, stdout,
    stderr, timing, the failing test's exception and traceback when one was
    found, and a deterministic five-stage breakdown of what that outcome
    proves happened. A3.5 does not target a specific A3 finding, so this
    endpoint never claims one.

    A 404 means A3.5 has not run for this run yet.
    """
    state = await _load_run(store, run_id)
    projection = build_reproduction_evidence(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No reproduction result for this run — A3.5 has not completed",
        )
    return projection


@router.get("/runs/{run_id}/investigation")
async def get_run_investigation(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A4's evidence investigation.

    Returns the projection built by `build_investigation`: what A4
    investigated, every upstream source it consulted with what that source
    actually said, the citations the verifier could and could not anchor, the
    root cause and where it came from, and the itemised sum behind the
    confidence. Nothing is recomputed here.

    A 404 means A4 has not produced a report for this run. That is distinct
    from a 200 carrying `status: "no_finding"` — A4 ran, consulted its sources
    and had no subject to investigate — and from `status: "error"`, where A4
    ran and degraded. All three are real outcomes the caller renders
    differently; none of them is a reason to substitute a value.
    """
    state = await _load_run(store, run_id)
    projection = build_investigation(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No investigation for this run — A4 has not completed",
        )
    return projection


async def _capability_rollup(
    store: RedisStore, settings: Settings, run_id: str, scope_paths: set[str]
) -> list[dict] | None:
    """A0.5's business-capability inference, narrowed to a blast scope.

    Best-effort and additive, same pattern as `_index_pointer`: A0.5 is an
    optional layer, so its absence is not an error, and a failure reading its
    cached index must never take the blast endpoint down with it. Returns
    `None` when A0.5 has not produced an index for this run — the caller
    renders that as "not available", never as zero capabilities.
    """
    try:
        index = await load_repository_intelligence(store, run_id, settings)
    except Exception:  # noqa: BLE001 — capability rollup is additive, never fatal
        logger.warning("blast_capability_rollup_failed", extra={"run_id": run_id})
        return None
    if index is None:
        return None

    graph = get_knowledge_graph(index)
    rollup = []
    classified: set[str] = set()
    for capability in infer_capabilities(graph):
        in_scope = sorted(set(capability.files) & scope_paths)
        classified |= set(capability.files)
        if not in_scope:
            continue
        rollup.append(
            {
                "name": capability.name,
                "slug": capability.slug,
                "confidence": capability.confidence,
                "filesInScope": in_scope,
                "totalFilesInCapability": len(capability.files),
                "why": capability.explanation.summary,
            }
        )
    rollup.sort(key=lambda c: len(c["filesInScope"]), reverse=True)
    unclassified = sorted(scope_paths - classified)
    return rollup + (
        [
            {
                "name": "Unclassified",
                "slug": "unclassified",
                "confidence": None,
                "filesInScope": unclassified,
                "totalFilesInCapability": None,
                "why": "No capability signal (filename, import, route, doc or config) matched these files.",
            }
        ]
        if unclassified
        else []
    )


@router.get("/runs/{run_id}/blast")
async def get_run_blast_impact(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """A5's blast-radius impact.

    Returns the projection built by `build_blast_impact`: the resolved origin
    and how A5 got there, every file in scope with its hop distance,
    direction(s), how it was reached, and the propagation/priority scores A5
    computed — joined read-only against A1's role/criticality/churn and A3's
    findings — plus, when A0.5 has run, the business capabilities the scope
    touches. Nothing here is a second analysis: every judgement is A5's,
    A1's or A3's, carried through unchanged.

    A 404 means A5 has not produced a result for this run. That is distinct
    from a 200 with an empty `scope` and `origin: null` — A5 ran and had
    nothing to traverse (no SIG, or no citations to start from).
    """
    state = await _load_run(store, run_id)
    projection = build_blast_impact(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No blast-radius impact for this run — A5 has not completed",
        )

    scope_paths = {s["path"] for s in projection["scope"] if s.get("path")}
    projection["capabilities"] = await _capability_rollup(store, settings, run_id, scope_paths)
    return projection


@router.get("/runs/{run_id}/context")
async def get_run_context(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """The context package A5.5 engineered for this run.

    Returns exactly what A5.5 stored — ranking, extracted symbols, redactions
    and measurements. Nothing is recomputed here, so a 404 means the stage has
    not produced a package yet, which is a fact the caller must render rather
    than a value to substitute.
    """
    await _load_run(store, run_id)
    package = await load_context_package(store, run_id)
    if package is None:
        raise HTTPException(
            status_code=404,
            detail="No context package for this run — A5.5 has not completed, or resolved no target",
        )
    return package.to_storage_dict()


@router.get("/runs/{run_id}/plan")
async def get_run_plan(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """The repair plan A6 sequenced for this run.

    Returns `state.fix_dag` verbatim: fix nodes with their files and
    dependencies, the execution order, the dependency edges with the reason
    each was drawn, and the conflict batches. The agent projection publishes
    only counts and a six-node visualization, so this is the sole route by
    which the full plan reaches a client.

    Nothing is recomputed. A 404 means A6 has not produced a plan, which the
    caller renders as absent rather than substituting an empty one — an empty
    DAG and a stage that never ran are different facts.
    """
    state = await _load_run(store, run_id)
    if not state.fix_dag:
        raise HTTPException(
            status_code=404,
            detail="No repair plan for this run — A6 has not completed",
        )
    return state.fix_dag


@router.get("/runs/{run_id}/repair-plan")
async def get_run_repair_plan(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """A6's repair plan, joined with why each step exists and what A5.5 requires of it.

    Returns the projection built by `build_repair_plan`: every planned step in
    order, its files, its dependencies, which other steps it conflicts with
    over a shared file, and — joined read-only against A3's findings and A2's
    CVE records — why the step exists, since `FixNode` itself carries no
    message. When A5.5 has produced a context package, its
    `acceptance_criteria` and `patch_constraints` are attached as A5.5's own
    evidence, not recomputed or claimed as A6's.

    This is not `/plan`: `/plan` returns `state.fix_dag` verbatim for API
    consumers that already depend on that exact shape. This route is the one
    the workspace board reads, and it is explicit that A7 currently consumes
    only `execution_order[0]` as a label — the ordered list here is A6's
    proposal, not a guarantee of what will execute.

    A 404 means A6 has not produced a plan for this run.
    """
    state = await _load_run(store, run_id)
    projection = build_repair_plan(state)
    if projection is None:
        raise HTTPException(
            status_code=404,
            detail="No repair plan for this run — A6 has not completed",
        )

    package = await load_context_package(store, run_id)
    projection["carriedForward"] = (
        {
            "acceptanceCriteria": list(package.acceptance_criteria),
            "patchConstraints": list(package.patch_constraints),
            # A5.5's own field, attached as-is. A6 has no function-level
            # analysis of its own — this must never read as A6's finding.
            "targetFunction": package.target_function,
        }
        if package is not None
        else None
    )
    return projection


@router.get("/runs/{run_id}/patch")
async def get_run_patch(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    """The patch bundle A7 generated for this run.

    Returns `state.patch_bundle` verbatim: every candidate with its original
    and patched source, the write method A7 actually used, the behavioural
    contracts it recorded, the unified diff it generated and the style exemplar
    commit it learned from. The agent projection publishes an eight-line diff
    preview and two filenames, so this is the sole route by which the full
    patch reaches a client.

    Nothing is recomputed or summarised — in particular the `method` field is
    passed through untouched, because it is what the integrity badges are
    allowed to claim. A 404 means A7 produced no bundle, which is a different
    fact from a bundle containing no patches: the first is "generation never
    completed", the second is "generation completed and changed nothing".
    """
    state = await _load_run(store, run_id)
    if not state.patch_bundle:
        raise HTTPException(
            status_code=404,
            detail="No patch bundle for this run — A7 has not completed",
        )
    return state.patch_bundle


@router.get("/runs/{run_id}/attempts")
async def get_repair_attempts(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    state = await _load_run(store, run_id)
    events = await _load_events(store, run_id)
    return build_repair_attempts(state, events)


@router.get("/runs/{run_id}/report")
async def get_run_report(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    from backend.services.draft_publication import refresh_publication_status

    state = await _load_run(store, run_id)
    state = await refresh_publication_status(store, state)
    events = await _load_events(store, run_id)
    return build_run_report(state, events)


@router.post("/runs/{run_id}/draft-pr")
async def create_draft_pr(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    from backend.services.draft_publication import publish_draft

    state = await publish_draft(store, settings, run_id)
    return build_run_report(state, await _load_events(store, run_id))


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    after: Annotated[
        int | None,
        Query(ge=0, description="Exclusive cursor: return events with a greater `sequence`."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[dict]:
    """Raw agent timeline. The UI replays this before attaching the WebSocket.

    Two modes, and which one you get depends on whether `after` is present:

    * **omitted** — the most recent `limit` events. Unchanged behaviour, so
      every existing client keeps working, and it is the right answer for
      "show me where this run is now".
    * **present** — events with a `sequence` strictly greater than the cursor,
      in order, up to `limit`. Start from `after=0` to walk the timeline from
      the beginning and stop when a short page comes back.

    The second mode is what B-B15 was about: without it a run that emitted more
    than `limit` events could only ever expose its tail, so a client replaying
    from the start silently lost the beginning of the run.

    The response stays a plain array — the cursor a caller needs is the
    `sequence` already on the events it receives.
    """
    await _load_run(store, run_id)
    events = await store.get_events(run_id, count=limit, after=after)
    return [e.model_dump(mode="json") for e in events]


@router.post("/runs/{run_id}/chat")
async def chat(
    run_id: str,
    body: ChatBody,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    state = await _load_run(store, run_id)
    answer = await answer_question(state, body.question, settings)
    return {"answer": answer}
