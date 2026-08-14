from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.clients.llm import create_secondary_chat_model
from app.clients.observability import langchain_trace_config

logger = logging.getLogger(__name__)

_TITLE_SYSTEM_PROMPT = (
    "You are clickhelp, a helpful assistant. Generate a short, descriptive title for a conversation. "
    "Respond with the title only — no quotes, no punctuation at the end, "
    "no more than 6 words. Capture what the user is asking about. and use only user message to generate the title. "
)

_MAX_TITLE_CHARS = 120


def _coerce_text(content: str | list) -> str:
    """Flatten LangChain message content (str or block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return ""


async def generate_conversation_title(
    *,
    user_message: str | list,
    conversation_id: str | None = None,
) -> str | None:
    """Make a small Haiku call to title a conversation from its first exchange.

    Best-effort: returns None on any failure so the caller can carry on without
    a title rather than surface an error.
    """
    user_text = _coerce_text(user_message).strip()
    if not user_text:
        return None

    prompt = (
        "Title this conversation based on its opening exchange.\n\n"
        f"User:\n{user_text[:2000]}\n"
    )

    try:
        llm = create_secondary_chat_model()
        response = await llm.ainvoke(
            [
                SystemMessage(content=_TITLE_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ],
            config=langchain_trace_config(conversation_id),
        )
        title = _coerce_text(response.content).strip().strip('"').strip()
        if not title:
            return None
        return title[:_MAX_TITLE_CHARS]
    except Exception:
        logger.exception("Failed to generate conversation title")
        return None
