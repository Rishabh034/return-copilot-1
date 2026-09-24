from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app import clock


def utc_now_iso_ms() -> str:
    """Current UTC time as 2026-06-14T10:28:01.000Z (ms precision, Z suffix)."""
    return to_iso_ms(clock.now_utc())


def to_iso_ms(dt: datetime) -> str:
    """Format a datetime as 2026-06-14T10:28:01.000Z (ms precision, Z suffix)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _extract_text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _extract_thinking(content: str | list) -> str:
    """Pull the model's extended-thinking text out of an AI message's content blocks."""
    if isinstance(content, list):
        return "".join(
            block.get("thinking", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "thinking"
        )
    return ""


def _message_time(message: BaseMessage) -> str | None:
    ts = message.additional_kwargs.get("message_time")
    return ts if isinstance(ts, str) else None


def to_display_messages(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Collapse LangChain messages to simple user/assistant pairs for the API and UI.

    An agentic turn produces one AI message per tool iteration; they are merged
    into the single assistant reply the user saw streamed, so replayed history
    keeps one assistant message per question. The merged entry carries the seq
    of the turn's last AI message — the same anchor the stream's `done` event
    reports for feedback.
    """
    display: list[dict[str, Any]] = []

    for message in messages:
        if isinstance(message, HumanMessage):
            text = _extract_text(message.content)
            if text:
                display.append(
                    {
                        "role": "user",
                        "content": text,
                        "message_time": _message_time(message),
                        "seq": message.additional_kwargs.get("seq"),
                    }
                )
        elif isinstance(message, AIMessage):
            message_time = _message_time(message)
            thinking = _extract_thinking(message.content)
            text = _extract_text(message.content)
            if text:
                seq = message.additional_kwargs.get("seq")
                previous = display[-1] if display else None
                if previous is not None and previous["role"] == "assistant":
                    previous["content"] = f"{previous['content']}\n\n{text}"
                    if thinking:
                        previous["reasoning"] = (
                            f"{previous['reasoning']}\n\n{thinking}"
                            if previous["reasoning"]
                            else thinking
                        )
                    previous["message_time"] = message_time or previous["message_time"]
                    previous["seq"] = seq if seq is not None else previous["seq"]
                else:
                    display.append(
                        {
                            "role": "assistant",
                            "content": text,
                            "message_time": message_time,
                            "reasoning": thinking if thinking else None,
                            "seq": seq,
                        }
                    )
        # ToolMessage and SystemMessage are internal — not shown in the chat UI.

    return display


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, ToolMessage):
        return "tool"
    return message.type


def messages_storage_stats(messages: list[BaseMessage]) -> dict[str, Any]:
    """Approximate serialized byte size for a message list (logging/metrics)."""
    by_role: dict[str, int] = {}
    for message in messages:
        role = _message_role(message)
        by_role[role] = by_role.get(role, 0) + 1

    payload = [message.model_dump() for message in messages]
    storage_bytes = len(json.dumps(payload, default=str).encode("utf-8"))
    return {
        "message_count": len(messages),
        "storage_bytes": storage_bytes,
        "messages_by_role": by_role,
    }
