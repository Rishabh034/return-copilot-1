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


def stored_tool_result_to_dict(stored: StoredToolResult) -> dict[str, Any]:
    return {
        "tool_call_id": stored.tool_call_id,
        "tool_name": stored.tool_name,
        "args": stored.args,
        "result": stored.result,
        "scratchpad": stored.scratchpad,
        "scratchpad_batches": list(stored.scratchpad_batches),
        "raw_data_parts": list(stored.raw_data_parts),
        "created_at": stored.created_at.isoformat(),
    }


def stored_tool_result_from_dict(data: dict[str, Any]) -> StoredToolResult:
    created_at_raw = data.get("created_at")
    if isinstance(created_at_raw, str):
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    elif isinstance(created_at_raw, datetime):
        created_at = created_at_raw
    else:
        created_at = datetime.now(timezone.utc)

    return StoredToolResult(
        tool_call_id=str(data["tool_call_id"]),
        tool_name=str(data.get("tool_name") or ""),
        args=data.get("args") if isinstance(data.get("args"), dict) else {},
        result=data.get("result") if isinstance(data.get("result"), dict) else {},
        scratchpad=str(data.get("scratchpad") or ""),
        scratchpad_batches=[
            batch
            for batch in (data.get("scratchpad_batches") or [])
            if isinstance(batch, str)
        ],
        raw_data_parts=[
            part for part in (data.get("raw_data_parts") or []) if isinstance(part, str)
        ],
        created_at=created_at,
    )


tool_result_store = ToolResultStore()
