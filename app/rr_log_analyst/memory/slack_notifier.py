# app/rr_log_analyst/memory/slack_notifier.py
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from app.rr_log_analyst.memory.store import Conversation, to_display_messages
import logging
from app.config import settings

logger = logging.getLogger(__name__)

slack_client = WebClient(token=settings.slack_token)
SLACK_CHANNEL = settings.slack_channel

def post_conversation_summary(conversation: Conversation) -> None:
    if not settings.slack_token:
        logger.debug("Slack token not configured; skipping conversation summary")
        return

    messages = to_display_messages(conversation.messages)
    if not messages:
        return

    lines = [f"*Conversation ID:* `{conversation.id}`",
             f"*Enterprise ID:* `{conversation.enterprise_id}`",
             f"*Token:* `{conversation.token}`",
             f"*Started:* {conversation.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
             "---"]

    for msg in messages:
        role = "🧑 User" if msg["role"] == "user" else "🤖 Bot"
        lines.append(f"*{role}:* {msg['content']}")

    try:
        slack_client.chat_postMessage(
            channel=SLACK_CHANNEL,
            text="\n".join(lines)
        )
    except SlackApiError as e:
        logger.error("Failed to post conversation to Slack: %s", e.response["error"])