from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class StoredToolResult:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any]
    scratchpad: str
    scratchpad_batches: list[str] = field(default_factory=list)
    raw_data_parts: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ToolResultStore:
    """In-process store for full tool JSON keyed by conversation and tool_call_id."""

    def __init__(self) -> None:
        self._results: dict[str, dict[str, StoredToolResult]] = {}

    def put(self, conversation_id: str, stored: StoredToolResult) -> None:
        self._results.setdefault(conversation_id, {})[stored.tool_call_id] = stored

    def get(self, conversation_id: str, tool_call_id: str) -> StoredToolResult | None:
        return self._results.get(conversation_id, {}).get(tool_call_id)

    def list(self, conversation_id: str) -> list[StoredToolResult]:
        return list(self._results.get(conversation_id, {}).values())

    def purge_conversation(self, conversation_id: str) -> None:
        self._results.pop(conversation_id, None)


tool_result_store = ToolResultStore()
