import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.clients.clickpost import ClickpostClient
from app.clients.observability import init_langfuse, shutdown_langfuse
from app.config import settings
from app.dependencies.auth import AgentAuthError
from app.exceptions import AppError, app_error_handler
from app.log_uploader import S3LogUploader
from app.logging_config import setup_logging
from app.request_context import request_id_var
from app.response_envelope import ENVELOPE_STATUS_HEADER, ResponseEnvelopeMiddleware
from app.routers import rr_log_analyst_router
from app.utilities.alerts import alert_slack

setup_logging()


class RequestIdMiddleware:
    """
    assigns a unique id to every request and injects in contextvar same as MDC in java.
    in this way request id will be added to each log line in our request and we can just grep request id 
    to get complete flow.
    also this is complete async middleware so it can be easily integrated in our sse streaming.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())
        request_id_var.set(request_id)

        async def send_with_header(message: dict) -> None:
            if message["type"] == "http.response.start":
                extra = [(b"x-request-id", request_id.encode())]
                message = {**message, "headers": list(message.get("headers", [])) + extra}
            await send(message)

        await self.app(scope, receive, send_with_header)


_ALLOWED_ORIGINS = settings.allowed_origins
logger = logging.getLogger(__name__)


async def agent_auth_error_handler(_request, exc: AgentAuthError) -> JSONResponse:
    # 200 on the wire, real status declared to the envelope middleware. The body
    # matches FastAPI's {"detail": ...} shape so the message is lifted into
    # meta.message the same way every other error's is.
    return JSONResponse(
        status_code=200,
        content={"detail": exc.detail},
        headers={ENVELOPE_STATUS_HEADER: str(exc.status_code)},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Catch-all fallback: anything that isn't an AppError/AgentAuthError/
    # HTTPException ends up here instead of Starlette's default handler, which
    # sits outside our envelope/CORS/request-id middleware and would otherwise
    # reach the browser as an unwrapped, header-less response.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    await asyncio.to_thread(alert_slack, f"{request.method} {request.url.path}", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong. Please try again."},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_langfuse()
    app.state.clickpost_client = ClickpostClient(
        base_url=settings.clickpost_base_url,
        timeout=settings.rr_analyst_tool_timeout,
    )
    log_uploader = None
    if settings.log_to_file:
        log_uploader = S3LogUploader()
        log_uploader.start()
    try:
        yield
    finally:
        await app.state.clickpost_client.aclose()
        if log_uploader:
            # last so shutdown logs from the lines above still make the flush
            await log_uploader.aclose()
        shutdown_langfuse()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Clickpost AI Projects",
        description="AI-powered tools for Clickpost",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(ResponseEnvelopeMiddleware)
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
    application.add_exception_handler(AgentAuthError, agent_auth_error_handler)
    application.add_exception_handler(Exception, unhandled_error_handler)

    return application


app = create_app()
