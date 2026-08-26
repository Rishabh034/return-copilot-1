"""Slack alerting for backend problems that need a human.

Separate from app/rr_log_analyst/memory/slack_notifier.py, which posts user
feedback to a different channel. Synchronous (uses `requests`); call via
`asyncio.to_thread` from async code so it never blocks the event loop.
"""

import logging

import requests

from app.config import settings
from app.request_context import request_id_var
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SlackAlertTool(BaseModel):
    conversation_id: str = Field(..., description="The Slack conversation ID to post the alert to.")
    error :str = Field(..., description="The error message to include in the alert.")
    tool_name :str = Field(..., description="The name of the tool that failed.")



def post_alert(title: str, fields: dict[str, str]) -> None:
    """Post a titled, field-per-line alert to the error webhook. Never raises."""
    if not settings.slack_error_webhook_url:
        return

    lines = [f"*{title}*"] + [f"*{k}:* {v}" for k, v in fields.items()]

    try:
        requests.post(
            settings.slack_error_webhook_url,
            json={"text": "\n".join(lines)},
            timeout=5,
        )
    except requests.RequestException:
        logger.exception("Failed to post alert to Slack")
 
 
 
def alert_slack(context: str, exc: BaseException) -> None:
    """Post a short summary of an unhandled exception to Slack. Never raises."""
    post_alert(
        f"Unhandled exception: {context}",
        {
            "Type": f"`{type(exc).__name__}`",
            "Message": str(exc),
            "Request ID": f"`{request_id_var.get()}`",
        },
    )


def tool_errors_slack_update(exc:SlackAlertTool) -> None:
    """Post a short summary of an unhandled exception to Slack. Never raises."""
    if not settings.slack_error_webhook_url:
        return

    lines = [
        f"*Conversation ID:* `{exc.conversation_id}`",
        f"*Tool Name:* `{exc.tool_name}`",
        f"*Message:* {exc.error}",
        
    ]

    try:
        requests.post(
            settings.slack_error_webhook_url,
            json={"text": "\n".join(lines)},
            timeout=5,
        )
    except requests.RequestException:
        logger.exception("Failed to post error alert to Slack")
