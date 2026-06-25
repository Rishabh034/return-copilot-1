import asyncio
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.clickpost import ClickpostClient
from app.config import settings
from app.exceptions import AppError, app_error_handler
from app.routers import rr_log_analyst_router
from app.rr_log_analyst.memory.store import conversation_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


_CLEANUP_INTERVAL_SECONDS = 3600  # run every hour

_ALLOWED_ORIGINS = settings.allowed_origins
logger = logging.getLogger(__name__)


async def _periodic_cleanup() -> None:
    while True:
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
        removed = conversation_store.purge_stale()
        if removed:
            logger.info("Purged %d stale conversations", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.clickpost_client = ClickpostClient(
        base_url=settings.clickpost_base_url,
        timeout=settings.rr_analyst_tool_timeout,
    )
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    try:
        yield
    finally:
        cleanup_task.cancel()
        await app.state.clickpost_client.aclose()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Clickpost AI Projects",
        description="AI-powered tools for Clickpost",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS.split(","),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["infra"])
    async def health():
        return {"status": "ok"}

    application.include_router(
        rr_log_analyst_router,
        prefix="/api/rr-log-analyst",
        tags=["rr-log-analyst"],
    )

    application.add_exception_handler(AppError, app_error_handler)

    return application


app = create_app()
