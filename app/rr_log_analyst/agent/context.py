from __future__ import annotations

import json
import logging

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.messages.utils import count_tokens_approximately

from app.config import settings
from app.rr_log_analyst.memory.tool_results import tool_result_store

logger = logging.getLogger(__name__)


def build_system_message(
    system_prompt: str, context_note: str | None = None
) -> SystemMessage:
    """System message split at the prompt-cache breakpoint. """
    if not settings.prompt_caching_enabled:
        joined = f"{system_prompt}\n{context_note}" if context_note else system_prompt
        return SystemMessage(content=joined)

    blocks: list[dict] = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if context_note:
        blocks.append({"type": "text", "text": context_note})
    return SystemMessage(content=blocks)


def serialize_usage(usage_metadata: UsageMetadata | None) -> dict[str, int] | None:
    """Normalize Anthropic usage_metadata from a ChatAnthropic response."""
    if not usage_metadata:
        return None

    input_tokens = usage_metadata.get("input_tokens") or 0
    output_tokens = usage_metadata.get("output_tokens") or 0
    total_tokens = usage_metadata.get("total_tokens") or (input_tokens + output_tokens)
    # LangChain folds cache reads/writes into input_tokens, so the only way to see
    # whether caching actually hit is to carry these details through separately.
    details = usage_metadata.get("input_token_details") or {}
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cache_read_input_tokens": details.get("cache_read") or 0,
        "cache_creation_input_tokens": details.get("cache_creation") or 0,
    }


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def merge_usage(
    left: dict[str, int] | None,
    right: dict[str, int] | None,
) -> dict[str, int] | None:
    if not left:
        return right
    if not right:
        return left
    return {field: (left.get(field) or 0) + (right.get(field) or 0) for field in _USAGE_FIELDS}


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


def _compact_tool_message_content(content: str, conversation_id: str) -> str:
    from app.rr_log_analyst.agent.scratchpad import build_compact_envelope

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return content

    if not isinstance(parsed, dict) or "result_id" not in parsed:
        return content

    result_id = parsed.get("result_id")
    stored = tool_result_store.get(conversation_id, str(result_id)) if result_id else None

    if stored is not None:
        return json.dumps(
            build_compact_envelope(
                result_id=str(result_id),
                tool_name=stored.tool_name,
                scratchpad=stored.scratchpad,
                batches=stored.scratchpad_batches,
                raw_data_parts=stored.raw_data_parts,
            )
        )

    summary = parsed.get("evidence_summary", parsed.get("scratchpad"))
    if isinstance(summary, str) and len(summary) > settings.scratchpad_inline_max_chars:
        return json.dumps(
            {
                "result_id": result_id,
                "source": parsed.get("source", parsed.get("tool", "")),
                "summary_available": True,
                "summary_chars": len(summary),
                "summary_segments": parsed.get(
                    "summary_segments", parsed.get("scratchpad_batches", 1)
                ),
                "note": parsed.get(
                    "note", "Use inspect_evidence for the complete evidence summary."
                ),
            }
        )

    return content


def compact_tool_messages_for_llm(
    messages: list[BaseMessage],
    conversation_id: str,
) -> list[BaseMessage]:
    """Replace oversized scratchpad ToolMessages with compact envelopes for the main LLM."""
    out: list[BaseMessage] = []
    for message in messages:
        if not isinstance(message, ToolMessage) or not isinstance(message.content, str):
            out.append(message)
            continue

        compact = _compact_tool_message_content(message.content, conversation_id)
        if compact == message.content:
            out.append(message)
            continue

        out.append(
            ToolMessage(
                content=compact,
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
    conversation_id: str | None = None,
) -> list[BaseMessage]:
    """Compact stored tool envelopes and trim conversation to the token budget."""
    if conversation_id:
        messages = compact_tool_messages_for_llm(messages, conversation_id)
    system_tokens = count_tokens_approximately([SystemMessage(content=system_prompt)])
    return trim_messages_to_budget(messages, system_tokens)
