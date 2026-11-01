"""User-triggered publication of immutable, locally prepared repair commits."""

import asyncio
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from redis.exceptions import WatchError

from backend.models.pr import PublicationResult
from backend.models.proof import VerificationBundle
from backend.services.git_service import CLONE_PREFIX, discard_clone
from backend.services.github_pr import GitHubPRService

DEADLINE_FILE = "proofix-publication-deadline"


def retain_checkout(path: str, ttl: int) -> None:
    git = Path(path) / ".git"
    if git.is_dir():
        (git / DEADLINE_FILE).write_text(str(time.time() + ttl), encoding="utf-8")


def cleanup_expired_publications() -> None:
    for path in Path(tempfile.gettempdir()).glob(f"{CLONE_PREFIX}*"):
        marker = path / ".git" / DEADLINE_FILE
        try:
            if marker.is_file() and float(marker.read_text()) < time.time():
                discard_clone(str(path))
        except (OSError, ValueError):
            continue


async def _lease(client, key, token, release=False):
    try:
        async with client.pipeline(transaction=True) as pipe:
            await pipe.watch(key)
            current = await pipe.get(key)
            if isinstance(current, bytes):
                current = current.decode()
            if current != token:
                return False
            pipe.multi()
            pipe.delete(key) if release else pipe.expire(key, 120)
            await pipe.execute()
            return True
    except WatchError:
        return False


async def publish_draft(store, settings, run_id):
    state = await store.load_state(run_id)
    if state is None:
        raise HTTPException(404, "Run not found")
    previous = state.publication_result or {}
    if previous.get("status") == "published" and previous.get("pr_url"):
        return state
    if state.status != "completed":
        raise HTTPException(409, "Repair must finish before creating a draft PR")
    if not previous.get("prepared_head_sha") or not state.proof_bundle:
        raise HTTPException(409, "No prepared repair is available. Start a new repair run.")
    if not state.repo_clone_path or not Path(state.repo_clone_path).is_dir():
        raise HTTPException(410, "The prepared repair has expired. Start a new repair run.")
    key = f"bugfix:{run_id}:publication-lock"
    token = str(uuid.uuid4())
    if not await store.client.set(key, token, nx=True, ex=120):
        raise HTTPException(409, "Draft PR creation is already in progress")

    async def renew():
        while True:
            await asyncio.sleep(30)
            if not await _lease(store.client, key, token):
                return

    heartbeat = asyncio.create_task(renew())
    try:
        # Re-read under the lock: another request may just have completed.
        state = await store.load_state(run_id)
        previous = PublicationResult.model_validate(state.publication_result)
        if previous.status == "published" and previous.pr_url:
            return state
        retain_checkout(state.repo_clone_path, store.ttl)
        state.publication_result = previous.model_copy(
            update={"status": "publishing", "reason": None}
        ).model_dump()
        state.pr_decision["publication"] = state.publication_result
        await store.save_state(state)
        decision = state.pr_decision
        service = GitHubPRService(settings)

        async def execute():
            try:
                result = await asyncio.to_thread(
                    service.publish_prepared_draft,
                    state.repo_clone_path,
                    f"proofix-fix-{run_id[:8]}",
                    VerificationBundle.model_validate(state.proof_bundle),
                    previous,
                    f"[ProoFix] {(state.root_cause or {}).get('root_cause', 'Repair')[:60]}",
                    decision.get("description_why", ""),
                    decision.get("description_what", ""),
                    decision.get("review_note") or "Human review required before merging.",
                )
            except Exception as exc:
                result = previous.model_copy(
                    update={
                        "status": "failed",
                        "reason": f"Draft publication failed ({type(exc).__name__}). Retry publication.",
                    }
                )
            state.publication_result = result.model_dump(mode="json")
            state.pr_decision["publication"] = state.publication_result
            state.pr_decision["pr_url"] = result.pr_url if result.status == "published" else None
            await store.save_state(state)
            if result.status == "published":
                discard_clone(state.repo_clone_path)
            return state

        job = asyncio.create_task(execute())
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            # Do not release the lease while a GitHub request is still running.
            await job
            raise
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        await _lease(store.client, key, token, release=True)


async def refresh_publication_status(store, state):
    """A killed server must not leave the UI polling 'publishing' forever."""
    publication = state.publication_result or {}
    if (
        publication.get("prepared_head_sha")
        and publication.get("status") != "published"
        and (not state.repo_clone_path or not Path(state.repo_clone_path).is_dir())
    ):
        state.publication_result = {
            **publication,
            "status": "disabled",
            "prepared_head_sha": None,
            "reason": "The prepared repair is no longer available. Start a new repair run.",
        }
        state.pr_decision["publication"] = state.publication_result
        return state
    if publication.get("status") == "publishing" and not await store.client.exists(
        f"bugfix:{state.run_id}:publication-lock"
    ):
        state = await store.load_state(state.run_id) or state
        publication = state.publication_result or {}
        if publication.get("status") != "publishing":
            return state
        state.publication_result = {
            **publication,
            "status": "failed",
            "reason": "Publication was interrupted. Retry to resume; an existing PR will be reused.",
        }
        state.pr_decision["publication"] = state.publication_result
        # Projection only: never overwrite a publisher that starts after our
        # lease check. The POST re-reads persisted state under its own lease.
    return state
