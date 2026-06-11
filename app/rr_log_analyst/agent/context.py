from __future__ import annotations

import logging

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.messages.utils import count_tokens_approximately

from app.config import settings

logger = logging.getLogger(__name__)


def serialize_usage(usage_metadata: UsageMetadata | None) -> dict[str, int] | None:
    """Normalize Anthropic usage_metadata from a ChatAnthropic response."""
    if not usage_metadata:
        return None

    input_tokens = usage_metadata.get("input_tokens") or 0
    output_tokens = usage_metadata.get("output_tokens") or 0
    total_tokens = usage_metadata.get("total_tokens") or (input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def merge_usage(
    left: dict[str, int] | None,
    right: dict[str, int] | None,
) -> dict[str, int] | None:
    if not left:
        return right
    if not right:
        return left
    return {
        "input_tokens": left["input_tokens"] + right["input_tokens"],
        "output_tokens": left["output_tokens"] + right["output_tokens"],
        "total_tokens": left["total_tokens"] + right["total_tokens"],
    }


MAX_TOOL_RESULT_CHARS = 80_000


def estimate_tokens(text: str | None) -> int:
    """Approximate token count for plain text (roles, tool metadata not included)."""
    if not text:
        return 0
    return count_tokens_approximately([HumanMessage(content=text)])


def message_token_count(message: BaseMessage) -> int:
    """Approximate token count for a single LangChain message, including tool calls."""
    return count_tokens_approximately([message])


def count_conversation_tokens(messages: list[BaseMessage]) -> int:
    """Approximate total token count for all messages in a stored conversation."""
    return sum(message_token_count(message) for message in messages)


def _truncate_tool_results(messages: list[BaseMessage]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            out.append(message)
            continue

        content = message.content
        if not isinstance(content, str) or len(content) <= MAX_TOOL_RESULT_CHARS:
            out.append(message)
            continue

        truncated = content[:MAX_TOOL_RESULT_CHARS]
        truncated += "\n\n[... TRUNCATED — result too large to fit in context ...]"
        logger.info(
            "Truncated tool_result %s from %d to %d chars",
            message.tool_call_id,
            len(content),
            len(truncated),
        )
        out.append(
            ToolMessage(
                content=truncated,
                tool_call_id=message.tool_call_id,
                name=message.name,
            )
        )
    return out


def trim_messages_to_budget(
    messages: list[BaseMessage],
    system_prompt_tokens: int,
) -> list[BaseMessage]:
    budget = settings.max_context_tokens - settings.reserved_output_tokens - system_prompt_tokens
    tail_count = settings.protected_tail_messages

    annotated = [(message, message_token_count(message)) for message in messages]
    total = sum(cost for _, cost in annotated)
    if total <= budget:
        return messages

    if len(messages) <= tail_count + 1:
        return messages

    protected_head = annotated[:1]
    protected_tail = annotated[-tail_count:]
    trimmable = annotated[1:-tail_count] if tail_count else annotated[1:]

    protected_cost = sum(cost for _, cost in protected_head + protected_tail)
    remaining = budget - protected_cost

    kept: list[BaseMessage] = []
    for message, cost in reversed(trimmable):
        if remaining - cost >= 0:
            kept.insert(0, message)
            remaining -= cost

    trimmed = [message for message, _ in protected_head] + kept + [message for message, _ in protected_tail]
    logger.info("Token budget trim: %d → %d messages", len(messages), len(trimmed))
    return trimmed


def prepare_messages(
    messages: list[BaseMessage],
    system_prompt: str,
) -> list[BaseMessage]:
    """Truncate oversized tool results and trim to the token budget."""
    messages = _truncate_tool_results(messages)
    system_tokens = count_tokens_approximately([SystemMessage(content=system_prompt)])
    return trim_messages_to_budget(messages, system_tokens)
