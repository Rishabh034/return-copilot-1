from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    message_to_dict,
    messages_from_dict,
)

from app.rr_log_analyst.memory.store import _extract_thinking
from app.rr_log_analyst.memory.tool_results import (
    stored_tool_result_from_dict,
    stored_tool_result_to_dict,
    tool_result_store,
)
from app.rr_log_analyst.models.message import Message

logger = logging.getLogger(__name__)


def langchain_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, ToolMessage):
        return "tool"
    return message.type


def _text_content(content: str | list) -> str | None:
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        return text or None
    return None


def _resolve_tool_result(
    message: BaseMessage,
    conversation_id: str,
) -> dict[str, Any] | None:
    if not isinstance(message, ToolMessage):
        return None

    stored = tool_result_store.get(conversation_id, message.tool_call_id)
    if stored is not None:
        return stored_tool_result_to_dict(stored)

    if isinstance(message.content, str):
        try:
            parsed = json.loads(message.content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"content": message.content}

    if isinstance(message.content, dict):
        return message.content

    return None


def _is_persisted_tool_result(data: dict[str, Any]) -> bool:
    return isinstance(data.get("result"), dict) and "tool_call_id" in data


def hydrate_tool_results_from_rows(
    rows: list[Message],
    *,
    conversation_id: str,
) -> None:
    """Restore in-memory tool results from persisted message rows."""
    for row in rows:
        if row.role != "tool" or not row.tool_result or not row.tool_call_id:
            continue
        if not _is_persisted_tool_result(row.tool_result):
            continue
        payload = dict(row.tool_result)
        payload.setdefault("tool_call_id", row.tool_call_id)
        payload.setdefault("tool_name", row.tool_name or "")
        tool_result_store.put(conversation_id, stored_tool_result_from_dict(payload))


def build_partial_ai_message(text: str) -> AIMessage:
    """A text-only assistant message for a stopped/disconnected turn.

    Display-only: it carries no tool_use or thinking blocks, so it never
    produces a dangling tool_use when reconstructed. Persisted with
    status='partial' and excluded from the replay history.
    """
    return AIMessage(content=text)


def message_to_row(
    conversation_id: uuid.UUID,
    seq: int,
    message: BaseMessage,
    *,
    status: str = "complete",
) -> Message:
    conversation_key = str(conversation_id)
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    token_count: int | None = None

    if isinstance(message, AIMessage):
        if message.usage_metadata:
            token_count = message.usage_metadata.get("output_tokens")
        if message.tool_calls:
            tool_call = message.tool_calls[0]
            tool_call_id = tool_call.get("id")
            tool_name = tool_call.get("name")
            tool_args = tool_call.get("args")

    if isinstance(message, ToolMessage):
        tool_call_id = message.tool_call_id
        tool_name = message.name
        tool_result = _resolve_tool_result(message, conversation_key)
        usage_metadata = message.additional_kwargs.get("usage_metadata")
        if isinstance(usage_metadata, dict):
            token_count = usage_metadata.get("output_tokens")

    role = langchain_role(message)
    if role == "tool" and tool_result is None:
        logger.warning(
            "Tool message missing tool_result conversation_id=%s seq=%d tool_call_id=%s",
            conversation_key,
            seq,
            tool_call_id,
        )

    return Message(
        conversation_id=conversation_id,
        seq=seq,
        role=role,
        status=status,
        content=_text_content(message.content),
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=tool_result,
        token_count=token_count,
        raw_response=message_to_dict(message),
    )


def _trim_tool_result(tool_result: dict[str, Any]) -> dict[str, Any]:
    """Drop the bulky raw-data payloads from a persisted tool result envelope.

    Compact envelopes carry raw_data_parts/scratchpad_batches that can run to
    hundreds of KB per tool call — the transcript keeps the actual result and
    the scratchpad summary, which is what a reader needs.
    """
    if not _is_persisted_tool_result(tool_result):
        return tool_result
    trimmed = {
        key: value
        for key, value in tool_result.items()
        if key not in ("raw_data_parts", "scratchpad_batches")
    }
    return trimmed


def rows_to_transcript(
    rows: list[Message],
    *,
    include_raw: bool = False,
) -> list[dict[str, Any]]:
    """Full ordered transcript: every persisted row (human/ai/tool) in seq order.

    Unlike to_display_messages, nothing is collapsed or hidden — AI turns keep
    their tool_calls and thinking, and tool rows keep their results. Reads only
    from the DB rows (raw_response + columns), so it works without the
    in-memory tool result store.

    Every row carries a `turn` index that increments on each human row, so a
    client can group the several ai/tool rows an agentic loop produces back
    into the single answer the live stream displayed.
    """
    entries: list[dict[str, Any]] = []
    turn = 0
    for row in sorted(rows, key=lambda r: r.seq):
        if row.role == "human":
            turn += 1
        content = row.content
        reasoning: str | None = None
        tool_calls: list[dict[str, Any]] | None = None
        message_time: str | None = None

        if row.raw_response:
            try:
                message = messages_from_dict([row.raw_response])[0]
            except Exception:
                logger.warning(
                    "Failed to reconstruct message conversation_id=%s seq=%d",
                    row.conversation_id,
                    row.seq,
                )
                message = None
            if message is not None:
                text = _text_content(message.content)
                if text:
                    content = text
                ts = message.additional_kwargs.get("message_time")
                message_time = ts if isinstance(ts, str) else None
                if isinstance(message, AIMessage):
                    thinking = _extract_thinking(message.content)
                    reasoning = thinking or None
                    if message.tool_calls:
                        tool_calls = [
                            {
                                "id": tool_call.get("id"),
                                "name": tool_call.get("name"),
                                "args": tool_call.get("args") or {},
                            }
                            for tool_call in message.tool_calls
                        ]

        tool_result = row.tool_result
        if tool_result is not None and not include_raw:
            tool_result = _trim_tool_result(tool_result)

        entries.append(
            {
                "seq": row.seq,
                "turn": turn,
                "role": row.role,
                "status": row.status,
                "content": content,
                "reasoning": reasoning,
                "tool_calls": tool_calls,
                "tool_call_id": row.tool_call_id if row.role == "tool" else None,
                "tool_name": row.tool_name if row.role == "tool" else None,
                "tool_result": tool_result,
                "message_time": message_time,
                "created_at": row.created_at,
            }
        )
    return entries


def rows_to_messages(
    rows: list[Message],
    *,
    conversation_id: str | None = None,
) -> list[BaseMessage]:
    if conversation_id:
        hydrate_tool_results_from_rows(rows, conversation_id=conversation_id)

    rows_with_payload = [row for row in rows if row.raw_response]
    if not rows_with_payload:
        return []
    messages = messages_from_dict([row.raw_response for row in rows_with_payload])
    # seq is a column, not part of the serialized message — stamp it back onto the
    # reconstructed message (same channel as message_time) so the display layer can
    # expose a per-turn anchor for feedback.
    for row, message in zip(rows_with_payload, messages):
        message.additional_kwargs["seq"] = row.seq
    return messages
