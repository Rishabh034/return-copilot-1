"""Best-effort Slack alerting for unhandled backend exceptions.

Separate from app/rr_log_analyst/memory/slack_notifier.py, which posts user
feedback to a different channel. Synchronous (uses `requests`); call via
`asyncio.to_thread` from async code so it never blocks the event loop.
"""

import logging

import requests

from app.config import settings
from app.request_context import request_id_var

logger = logging.getLogger(__name__)


def alert_slack(context: str, exc: BaseException) -> None:
    """Post a short summary of an unhandled exception to Slack. Never raises."""
    if not settings.slack_error_webhook_url:
        return

    lines = [
        f"*Unhandled exception:* {context}",
        f"*Type:* `{type(exc).__name__}`",
        f"*Message:* {exc}",
        f"*Request ID:* `{request_id_var.get()}`",
    ]

    try:
        requests.post(
            settings.slack_error_webhook_url,
            json={"text": "\n".join(lines)},
            timeout=5,
        )
    except requests.RequestException:
        logger.exception("Failed to post error alert to Slack")
