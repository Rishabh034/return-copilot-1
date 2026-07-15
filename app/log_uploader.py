from __future__ import annotations

import asyncio
import gzip
import logging
import os
import re
import shutil
import socket
import time
from datetime import datetime, timezone

import boto3

from app.config import settings
from app.logging_config import ACTIVE_LOG_FILENAME

logger = logging.getLogger(__name__)

# TimedRotatingFileHandler(when="H", utc=True) names rotated files
# app.log.YYYY-MM-DD_HH — that timestamp drives the S3 partition.
_ROTATED_RE = re.compile(r"^app\.log\.(\d{4})-(\d{2})-(\d{2})_(\d{2})$")


class S3LogUploader:
    """Ships rotated log files to S3 from a background asyncio task.

    Every `log_upload_interval_minutes` it scans `log_dir` for *rotated* files
    (never the active app.log, so half-written files are never shipped), gzips
    each and uploads it to a time-partitioned key:

        s3://<bucket>/<prefix>/YYYY/MM/DD/HH/<hostname>_<epoch>.log.gz

    A local file is deleted only after its upload succeeds, and `log_dir` is
    volume-mounted, so an S3 outage or a container restart just delays the
    upload — leftovers are picked up on the next cycle / next boot. On graceful
    shutdown the active (partial-hour) file is uploaded too.
    """

    def __init__(self) -> None:
        self._hostname = socket.gethostname()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._s3 = None  # created lazily inside the worker thread

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="s3-log-uploader")

    async def aclose(self) -> None:
        """Stop the loop, then flush everything including the active file."""
        self._stop_event.set()
        if self._task:
            await self._task
        try:
            await asyncio.to_thread(self._upload_pending, True)
        except Exception:
            logger.exception("Final log upload on shutdown failed")

    async def _run(self) -> None:
        interval = settings.log_upload_interval_minutes * 60
        # First pass right away: catches files left over from a previous run.
        while True:
            try:
                await asyncio.to_thread(self._upload_pending)
            except Exception:
                logger.exception("Log upload cycle failed; retrying next cycle")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                return
            except asyncio.TimeoutError:
                pass

    # -- blocking parts, always run via asyncio.to_thread ---------------------

    def _client(self):
        if self._s3 is None:
            self._s3 = boto3.client("s3")
        return self._s3

    def _upload_pending(self, include_active: bool = False) -> None:
        log_dir = settings.log_dir
        if not os.path.isdir(log_dir):
            return
        for name in sorted(os.listdir(log_dir)):
            if name.endswith(".gz.tmp"):
                # leftover from a crash mid-gzip; the source file still exists
                os.remove(os.path.join(log_dir, name))
                continue
            match = _ROTATED_RE.match(name)
            if not match:
                continue
            yyyy, mm, dd, hh = match.groups()
            self._upload_one(os.path.join(log_dir, name), f"{yyyy}/{mm}/{dd}/{hh}")
        if include_active:
            active = os.path.join(log_dir, ACTIVE_LOG_FILENAME)
            if os.path.isfile(active) and os.path.getsize(active) > 0:
                hour = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H")
                self._upload_one(active, hour, tag="partial")

    def _upload_one(self, path: str, hour_prefix: str, tag: str = "") -> None:
        filename = f"{self._hostname}_{int(time.time())}"
        if tag:
            filename += f"_{tag}"
        key = f"{settings.log_s3_prefix}/{hour_prefix}/{filename}.log.gz"

        gz_path = path + ".gz.tmp"
        with open(path, "rb") as src, gzip.open(gz_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            with open(gz_path, "rb") as body:
                self._client().put_object(
                    Bucket=settings.log_s3_bucket, Key=key, Body=body
                )
        finally:
            os.remove(gz_path)
        os.remove(path)
        logger.info(
            "Uploaded %s to s3://%s/%s",
            os.path.basename(path), settings.log_s3_bucket, key,
        )
