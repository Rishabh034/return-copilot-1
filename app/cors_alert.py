"""Alerting for CORS preflights that CORSMiddleware turns away.
"""

from __future__ import annotations

import asyncio
import logging
import time

from starlette.types import ASGIApp, Receive, Scope, Send

from app.utilities.alerts import post_alert

logger = logging.getLogger(__name__)


class CorsRejectionAlertMiddleware:
    """
    alerts when CORSMiddleware turns away a preflight.

    this has to wrap CORSMiddleware (so it is registered *after* it, ending up
    further out) because that rejection is returned, not raised - it never
    reaches an exception handler, so unhandled_error_handler/alert_slack never
    sees it.

    a browser on a correctly listed origin never gets this response, so every
    hit is a real config bug and there is no rate to tune — we only dedupe per
    origin so one broken frontend cannot flood the channel.
    """

    _ALERT_INTERVAL_SECONDS = 3600
    # Origin is attacker-controlled, and this host is scanned: without a cap the
    # dedupe map grows once per distinct Origin header for the life of the
    # process.
    _MAX_TRACKED_ORIGINS = 256

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._last_alert: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "OPTIONS":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        origin = headers.get(b"origin", b"").decode()
        # a preflight is Origin + Access-Control-Request-Method together; without
        # both, CORSMiddleware passes the request through untouched and any 400
        # that comes back belongs to the route, not to CORS.
        if not origin or b"access-control-request-method" not in headers:
            await self.app(scope, receive, send)
            return

        async def send_watching_status(message: dict) -> None:
            if message["type"] == "http.response.start" and message["status"] == 400:
                self._report(origin, scope.get("path", ""))
            await send(message)

        await self.app(scope, receive, send_watching_status)

    def _report(self, origin: str, path: str) -> None:
        logger.warning(
            "CORS preflight rejected origin=%s path=%s (not in ALLOWED_ORIGINS)",
            origin, path,
        )
        now = time.monotonic()
        last = self._last_alert.get(origin)
        if last is not None and now - last < self._ALERT_INTERVAL_SECONDS:
            return
        self._remember(origin, now)

        # fire-and-forget so the preflight response is not held up; keep a
        # reference until it finishes or the task can be garbage collected.
        task = asyncio.create_task(
            asyncio.to_thread(
                post_alert,
                "CORS preflight rejected",
                {
                    "Origin": f"`{origin}`",
                    "Path": path,
                    "Fix": "add this origin to ALLOWED_ORIGINS and recreate the app container",
                },
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _remember(self, origin: str, now: float) -> None:
        """Record the alert time, keeping the map bounded.

        Entries older than the dedupe window carry no information, so they go
        first. If everything is still recent the map is genuinely saturated —
        drop the oldest, which at worst re-alerts for one origin.
        """
        if len(self._last_alert) >= self._MAX_TRACKED_ORIGINS:
            cutoff = now - self._ALERT_INTERVAL_SECONDS
            self._last_alert = {
                seen: at for seen, at in self._last_alert.items() if at > cutoff
            }
            if len(self._last_alert) >= self._MAX_TRACKED_ORIGINS:
                del self._last_alert[min(self._last_alert, key=self._last_alert.get)]
        self._last_alert[origin] = now
