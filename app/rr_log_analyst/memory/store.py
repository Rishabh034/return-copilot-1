from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def utc_now_iso_ms() -> str:
    """Current UTC time as 2026-06-14T10:28:01.000Z (ms precision, Z suffix)."""
    return to_iso_ms(datetime.now(timezone.utc))


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


def to_display_messages(messages: list[BaseMessage]) -> list[dict[str, str | None]]:
    """Collapse LangChain messages to simple user/assistant pairs for the API and UI."""
    display: list[dict[str, str | None]] = []

    for message in messages:
        if isinstance(message, HumanMessage):
            text = _extract_text(message.content)
            if text:
                display.append(
                    {"role": "user", "content": text, "message_time": _message_time(message)}
                )
        elif isinstance(message, AIMessage):
            message_time = _message_time(message)
            thinking = _extract_thinking(message.content)
            if thinking:
                display.append(
                    {
                        "role": "thinking",
                        "content": thinking,
                        "message_time": message_time,
                    }
                )
            text = _extract_text(message.content)
            if text:
                display.append(
                    {
                        "role": "assistant",
                        "content": text,
                        "message_time": message_time,
                    }
                )
        # ToolMessage and SystemMessage are internal — not shown in the chat UI.

    return display


@dataclass
class Conversation:
    id: str
    token: str
    messages: list[BaseMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "human"
    if isinstance(message, AIMessage):
        return "ai"
    if isinstance(message, ToolMessage):
        return "tool"
    return message.type


def messages_storage_stats(messages: list[BaseMessage]) -> dict[str, Any]:
    """Approximate in-memory storage for a message list (JSON-serialized byte size)."""
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


class ConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}

    @staticmethod
    def new_id() -> str:
        """Allocate a conversation id without persisting anything."""
        return str(uuid.uuid4())

    def create(self, token: str) -> Conversation:
        conversation = Conversation(
            id=str(uuid.uuid4()),
            token=token,
        )
        self._conversations[conversation.id] = conversation
        return conversation

    def save(
        self,
        conversation_id: str,
        token: str,
        messages: list[BaseMessage],
    ) -> Conversation:
        """Upsert a conversation. Creates it on first successful turn so failed
        turns never leave an empty conversation behind."""
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            conversation = Conversation(
                id=conversation_id,
                token=token,
                messages=messages,
            )
            self._conversations[conversation_id] = conversation
        else:
            conversation.messages = messages
            conversation.updated_at = datetime.now(timezone.utc)
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def update_messages(
        self,
        conversation_id: str,
        messages: list[BaseMessage],
    ) -> Conversation | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            return None
        conversation.messages = messages
        conversation.updated_at = datetime.now(timezone.utc)
        return conversation

    def list_by_token(self, token: str) -> list[Conversation]:
        conversations = [
            conversation
            for conversation in self._conversations.values()
            if conversation.token == token
        ]
        return sorted(conversations, key=lambda c: c.updated_at, reverse=True)

    def storage_stats(self) -> dict[str, Any]:
        conversations = list(self._conversations.values())
        message_count = sum(len(conversation.messages) for conversation in conversations)
        storage_bytes = sum(
            messages_storage_stats(conversation.messages)["storage_bytes"]
            for conversation in conversations
        )
        return {
            "conversation_count": len(conversations),
            "message_count": message_count,
            "storage_bytes": storage_bytes,
        }

    def purge_stale(self, max_age: timedelta = timedelta(hours=24)) -> int:
        """Remove conversations not updated within *max_age*. Returns count deleted."""
        from app.rr_log_analyst.memory.tool_results import tool_result_store

        cutoff = datetime.now(timezone.utc) - max_age
        stale_ids = [
            cid
            for cid, conv in self._conversations.items()
            if conv.updated_at < cutoff
        ]
        for cid in stale_ids:
            tool_result_store.purge_conversation(cid)
            del self._conversations[cid]
        return len(stale_ids)

    def storage_stats_for_token(self, token: str) -> dict[str, Any]:
        conversations = [
            conversation
            for conversation in self._conversations.values()
            if conversation.token == token
        ]
        message_count = sum(len(conversation.messages) for conversation in conversations)
        storage_bytes = sum(
            messages_storage_stats(conversation.messages)["storage_bytes"]
            for conversation in conversations
        )
        return {
            "conversation_count": len(conversations),
            "message_count": message_count,
            "storage_bytes": storage_bytes,
        }


conversation_store = ConversationStore()
