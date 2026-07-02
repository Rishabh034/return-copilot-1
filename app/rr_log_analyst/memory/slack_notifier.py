# app/rr_log_analyst/memory/slack_notifier.py
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from app.rr_log_analyst.memory.store import to_display_messages
from app.rr_log_analyst.models.conversation import Conversation
import logging
from app.config import settings

logger = logging.getLogger(__name__)

slack_client = WebClient(token=settings.slack_token)
SLACK_CHANNEL = settings.slack_channel


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
    if not settings.slack_token:
        logger.debug("Slack token not configured; skipping feedback notification")
        return

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
        slack_client.chat_postMessage(channel=SLACK_CHANNEL, text="\n".join(lines))
    except SlackApiError as e:
        logger.error("Failed to post feedback to Slack: %s", e.response["error"])