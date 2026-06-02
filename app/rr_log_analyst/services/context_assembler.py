from __future__ import annotations

import json
from typing import Any

from app.rr_log_analyst.services.token_manager import estimate_tokens, trim_messages_to_budget


def build_context(
    messages: list[dict[str, Any]],
    system_prompt: str,
) -> list[dict[str, Any]]:
    """Apply token budget trimming to messages already in Anthropic format."""
    annotated = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            text = json.dumps(content)
        else:
            text = content or ""
        annotated.append({**msg, "_token_estimate": estimate_tokens(text)})

    system_tokens = estimate_tokens(system_prompt)
    trimmed = trim_messages_to_budget(annotated, system_tokens)

    for msg in trimmed:
        msg.pop("_token_estimate", None)

    return trimmed
