import logging
from contextlib import asynccontextmanager

import anthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.exceptions import AppError, app_error_handler
from app.routers import rr_log_analyst_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.anthropic_client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
    )
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="Clickpost AI Projects",
        description="AI-powered tools for Clickpost",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
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
