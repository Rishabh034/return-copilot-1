from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.rr_log_analyst.services.token_manager import estimate_tokens, trim_messages_to_budget


async def load_messages(conn: asyncpg.Connection, conversation_id: str) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, seq, role, content, tool_call_id, tool_name, tool_args, tool_result
        FROM rr_log_analyst_message
        WHERE conversation_id = $1
        ORDER BY seq ASC
        """,
        conversation_id,
    )
    return [dict(r) for r in rows]


def db_rows_to_anthropic_messages(rows: list[dict]) -> list[dict[str, Any]]:
    """Convert DB message rows into Anthropic API message format.

    Handles user, assistant (with optional tool_use blocks), and tool results.
    Per Anthropic API: tool_result blocks go in a user-role message.
    """
    if not rows:
        return []

    api_messages: list[dict[str, Any]] = []

    for row in rows:
        role = row["role"]

        if role == "user":
            api_messages.append({"role": "user", "content": row["content"] or ""})

        elif role == "assistant":
            if row.get("tool_name") and row.get("tool_call_id"):
                blocks: list[dict] = []
                if row.get("content"):
                    blocks.append({"type": "text", "text": row["content"]})
                blocks.append({
                    "type": "tool_use",
                    "id": row["tool_call_id"],
                    "name": row["tool_name"],
                    "input": row.get("tool_args") or {},
                })
                api_messages.append({"role": "assistant", "content": blocks})
            else:
                api_messages.append({"role": "assistant", "content": row["content"] or ""})

        elif role == "tool":
            result_str = json.dumps(row.get("tool_result") or {"error": "no result"})
            api_messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": row["tool_call_id"],
                        "content": result_str,
                    }
                ],
            })

    return api_messages


def build_context(
    rows: list[dict],
    system_prompt: str,
) -> list[dict[str, Any]]:
    """Build Anthropic messages with token budget trimming."""
    annotated = []
    for row in rows:
        text = row.get("content") or ""
        if row.get("tool_args"):
            text += json.dumps(row["tool_args"])
        if row.get("tool_result"):
            text += json.dumps(row["tool_result"])
        annotated.append({**row, "_token_estimate": estimate_tokens(text)})

    system_tokens = estimate_tokens(system_prompt)
    trimmed = trim_messages_to_budget(annotated, system_tokens)

    return db_rows_to_anthropic_messages(trimmed)
