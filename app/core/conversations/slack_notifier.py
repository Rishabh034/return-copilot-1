# app/core/conversations/slack_notifier.py

from app.core.conversations.store import to_display_messages
from app.core.conversations.conversation_model import Conversation
import logging
from app.config import settings
import requests

logger = logging.getLogger(__name__)

def post_feedback(
    *,
    conversation_id: str,
    seq: int,
    enterprise_id: int,
    user_id: int,
    positive: bool,
    comment: str | None,
    is_internal_user: bool = False,
    internal_user_id: str | None = None,
) -> None:
    """Post a single feedback entry to Slack. Best-effort — never raises.
    Synchronous WebClient call; invoke via asyncio.to_thread from async code so
    it doesn't block the event loop.
    """

    verdict = "Thumbs up" if positive else "Thumbs down"
    lines = [
        f"*Feedback:* {verdict}",
        f"*Conversation ID:* `{conversation_id}`",
        f"*Turn (seq):* `{seq}`",
        f"*Enterprise ID:* `{enterprise_id}`",
        f"*User ID:* `{user_id}`",
    ]
    if comment:
        lines.append(f"*Comment:* {comment}")

    if is_internal_user:
        lines.append(f"*Internal User ID:* `{internal_user_id}`")
        lines.append(f"*Internal User:* `{is_internal_user}`")  
    try:
        requests.post(
            "https://hooks.slack.com/services/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",
            json={"text": "\n".join(lines)},
            timeout=5,
        )
    except requests.RequestException as e:
        logger.error("Failed to post feedback to Slack: %s", e)