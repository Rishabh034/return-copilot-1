from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from opentelemetry import trace

from app.config import settings
from app.request_context import request_id_var

ACTIVE_LOG_FILENAME = "app.log"

_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] "
    "[trace_id=%(trace_id)s span_id=%(span_id)s] %(message)s"
)


class _RequestIdFilter(logging.Filter):
    """Injects the current request_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _TraceContextFilter(logging.Filter):
    """Injects the active OTel trace_id/span_id into every log record, so a
    trace in SigNoz can be grepped straight to the app log lines emitted
    during it. Empty when nothing is active (startup logs, background work
    outside a span) or when otel is disabled - get_current_span() always
    returns a valid-but-non-recording span in that case, never raises.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            record.trace_id = format(ctx.trace_id, "032x")
            record.span_id = format(ctx.span_id, "016x")
        else:
            record.trace_id = ""
            record.span_id = ""
        return True


class _SkipHealthFilter(logging.Filter):
    """Drops access-log lines for the /health endpoint (pure noise from probes)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def setup_logging() -> None:
    formatter = logging.Formatter(_FORMAT)
    request_id_filter = _RequestIdFilter()
    trace_context_filter = _TraceContextFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(request_id_filter)
    stream_handler.addFilter(trace_context_filter)
    stream_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [stream_handler]

    logging.captureWarnings(True)

    if settings.log_to_file:
        os.makedirs(settings.log_dir, exist_ok=True)
        # Rotates on the hour (UTC) to app.log.YYYY-MM-DD_HH; the S3 uploader
        # picks up rotated files and deletes them after upload. backupCount
        # bounds disk usage if S3 stays unreachable for days.
        file_handler = TimedRotatingFileHandler(
            os.path.join(settings.log_dir, ACTIVE_LOG_FILENAME),
            when="H",
            interval=1,
            backupCount=72,
            utc=True,
            delay=True,
        )
        file_handler.addFilter(request_id_filter)
        file_handler.addFilter(trace_context_filter)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if settings.capture_access_logs:
        # uvicorn ships its own handlers; strip them and propagate to root so
        # access + server logs get our format/request_id and land in the file
        # (and therefore in S3) alongside application logs.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uv_logger = logging.getLogger(name)
            uv_logger.handlers = []
            uv_logger.propagate = True
        logging.getLogger("uvicorn.access").addFilter(_SkipHealthFilter())
