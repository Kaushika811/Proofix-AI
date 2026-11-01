import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import knowledge, learning, runs, security, speech, ui, ws
from backend.config import Settings, get_settings
from backend.state.redis_store import create_redis_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings

    # Surface a production misconfiguration immediately in startup logs,
    # not at the first pipeline run minutes later.
    if not settings.stub_mode and not settings.llm_configured():
        logger.warning(
            "production_llm_not_configured",
            extra={
                "stub_mode": False,
                "llm_provider": settings.llm_provider,
                "hint": (
                    f"STUB_MODE is false but no credential is set for provider "
                    f"'{settings.llm_provider}'. Patch generation will fail. "
                    f"Set the appropriate API key / base URL or set STUB_MODE=true."
                ),
            },
        )

    try:
        app.state.redis = await create_redis_client(settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis_connect_failed_at_startup", extra={"error": str(exc)})
        app.state.redis = None
    async def cleanup_publications():
        from backend.services.draft_publication import cleanup_expired_publications
        while True:
            try:
                await asyncio.to_thread(cleanup_expired_publications)
            except Exception:
                logger.exception("publication_cleanup_failed")
            await asyncio.sleep(60)

    cleanup_task = asyncio.create_task(cleanup_publications())
    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        if app.state.redis is not None:
            await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ProoFix Repair Verification API",
        version="0.1.0",
        lifespan=lifespan,
    )
    # Browsers reject `allow_origins=["*"]` together with credentials, so the
    # dev origins are listed explicitly. Override with CORS_ORIGINS in .env.
    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(runs.router)
    app.include_router(ui.router)
    app.include_router(ws.router)
    app.include_router(knowledge.router)
    app.include_router(security.router)
    app.include_router(learning.router)
    app.include_router(speech.router)

    @app.get("/health")
    async def health():
        settings: Settings = app.state.settings
        redis_ok = False
        try:
            redis_ok = await app.state.redis.ping()
        except Exception:
            pass
        return {
            "status": "ok" if redis_ok else "degraded",
            "redis": redis_ok,
            "stub_mode": settings.stub_mode,
            "llm_provider": settings.llm_provider,
            "llm_configured": settings.llm_configured(),
            "llm_ready": not settings.stub_mode and settings.llm_configured(),
        }

    return app


app = create_app()
