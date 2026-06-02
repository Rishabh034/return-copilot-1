from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def trim_messages_to_budget(
    messages: list[dict],
    system_prompt_tokens: int,
) -> list[dict]:
    """Drop oldest non-protected messages until within token budget.

    Keeps the first message (initial user query context) and the last N
    (protected_tail_messages) intact. Trims from the middle.
    """
    budget = settings.max_context_tokens - settings.reserved_output_tokens - system_prompt_tokens
    tail_count = settings.protected_tail_messages

    total = sum(m.get("_token_estimate", 0) for m in messages)
    if total <= budget:
        return messages

    if len(messages) <= tail_count + 1:
        return messages

    protected_head = messages[:1]
    protected_tail = messages[-tail_count:]
    trimmable = messages[1:-tail_count] if tail_count else messages[1:]

    protected_cost = sum(m.get("_token_estimate", 0) for m in protected_head + protected_tail)
    remaining = budget - protected_cost

    kept: list[dict] = []
    for msg in reversed(trimmable):
        cost = msg.get("_token_estimate", 0)
        if remaining - cost >= 0:
            kept.insert(0, msg)
            remaining -= cost

    trimmed = protected_head + kept + protected_tail
    logger.info("Token budget trim: %d → %d messages", len(messages), len(trimmed))
    return trimmed
