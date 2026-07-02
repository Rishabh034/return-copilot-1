# app/rr_log_analyst/memory/slack_notifier.py

from app.rr_log_analyst.memory.store import to_display_messages
from app.rr_log_analyst.models.conversation import Conversation
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

    try:
        requests.post(
            "https://hooks.slack.com/services/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",
            json={"text": "\n".join(lines)},
            timeout=5,
        )
    except requests.RequestException as e:
        logger.error("Failed to post feedback to Slack: %s", e)