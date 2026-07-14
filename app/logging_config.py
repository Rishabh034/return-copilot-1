from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from app.config import settings
from app.request_context import request_id_var

ACTIVE_LOG_FILENAME = "app.log"

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s"


class _RequestIdFilter(logging.Filter):
    """Injects the current request_id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class _SkipHealthFilter(logging.Filter):
    """Drops access-log lines for the /health endpoint (pure noise from probes)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def setup_logging() -> None:
    formatter = logging.Formatter(_FORMAT)
    request_id_filter = _RequestIdFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(request_id_filter)
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
