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
    agent_slug: str | None = None,
) -> tuple[str | None, dict | None]:
    """Make a small Haiku call to title a conversation from its first exchange.

    Returns `(title, usage)`. The usage half exists because this is a real,
    billed Anthropic call: it used to be discarded here, which left every
    titling call charged by Anthropic and traced by Langfuse but absent from
    `llm_usage_ledger`, so it counted against nobody's cap and showed up as a
    permanent unexplained gap in any reconciliation.

    Best-effort: returns `(None, usage)` on any failure so the caller can carry
    on without a title rather than surface an error. Note the usage is still
    returned when the title itself is unusable - the tokens were spent whether
    or not we could use the answer.
    """
    user_text = _coerce_text(user_message).strip()
    if not user_text:
        return None, None

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
            config=langchain_trace_config(conversation_id, agent_slug=agent_slug),
        )
        usage = getattr(response, "usage_metadata", None) or None
        title = _coerce_text(response.content).strip().strip('"').strip()
        if not title:
            return None, usage
        return title[:_MAX_TITLE_CHARS], usage
    except Exception:
        # The call may have been billed before it failed, but no usage is
        # recoverable from an exception, so this path under-counts by design.
        logger.exception("Failed to generate conversation title")
        return None, None
