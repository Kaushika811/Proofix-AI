import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.deps import get_runner, get_store
from backend.models.issue import IssueFields
from backend.orchestrator.runner import PipelineRunner
from backend.state.redis_store import RedisStore

router = APIRouter(prefix="/runs", tags=["runs"])


class CreateRunRequest(IssueFields):
    repo_path: str = Field(..., description="Local path or URL to target repo")


class CreateRunResponse(BaseModel):
    run_id: str
    status: str


class RunSummary(BaseModel):
    run_id: str
    status: str
    current_agent: str
    force_draft_pr: bool
    retry_count: int
    pr_decision: dict | None = None
    errors: list[dict] = Field(default_factory=list)


@router.post("", response_model=CreateRunResponse)
async def create_run(
    body: CreateRunRequest,
    background_tasks: BackgroundTasks,
    store: Annotated[RedisStore, Depends(get_store)],
    runner: Annotated[PipelineRunner, Depends(get_runner)],
) -> CreateRunResponse:
    run_id = str(uuid.uuid4())
    await store.init_run(run_id, body.repo_path, body.issue_hint, body.issue_contract())
    background_tasks.add_task(runner.execute, run_id)
    return CreateRunResponse(run_id=run_id, status="pending")


@router.get("/{run_id}", response_model=RunSummary)
async def get_run(
    run_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> RunSummary:
    state = await store.load_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunSummary(
        run_id=state.run_id,
        status=state.status,
        current_agent=state.current_agent,
        force_draft_pr=state.force_draft_pr,
        retry_count=state.retry_count,
        pr_decision=state.pr_decision,
        errors=state.errors,
    )


@router.get("/{run_id}/sig")
async def get_sig(run_id: str, store: Annotated[RedisStore, Depends(get_store)]) -> dict:
    sig = await store.get_json(run_id, "sig")
    if sig is None:
        state = await store.load_state(run_id)
        if not state:
            raise HTTPException(status_code=404, detail="Run not found")
        return state.sig or {}
    return sig


@router.get("/{run_id}/events")
async def get_events(run_id: str, store: Annotated[RedisStore, Depends(get_store)]) -> list[dict]:
    state = await store.load_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")
    events = await store.get_events(run_id)
    return [e.model_dump(mode="json") for e in events]


@router.get("/{run_id}/proof/{issue_id}")
async def get_proof_bundle(
    run_id: str,
    issue_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
) -> dict:
    from pathlib import Path

    import json

    from backend.models.proof import VerificationBundle

    cached = await store.get_json(run_id, f"proof:{issue_id}")
    if cached:
        return VerificationBundle.model_validate(cached).model_dump(mode="json")

    state = await store.load_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    if state.proof_bundle and state.proof_bundle.get("issue_id") == issue_id:
        return state.proof_bundle

    repo_path = state.repo_clone_path or state.repo_path
    proof_file = Path(repo_path) / ".proof-of-fix" / f"{issue_id}.json"
    if proof_file.exists():
        data = json.loads(proof_file.read_text(encoding="utf-8"))
        return VerificationBundle.model_validate(data).model_dump(mode="json")

    raise HTTPException(status_code=404, detail="Proof bundle not found")
