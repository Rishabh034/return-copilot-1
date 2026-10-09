from __future__ import annotations

from datetime import date, datetime
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.return_exchange.tools._common import rne_client, token
from app.core.tool_context import ToolContext

_ACTION = "Return & Exchange"
_MAX_DAYS = 7
_LIMIT = 25
_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


class AuditInput(BaseModel):
    start_date: str = Field(description="Start date, YYYY-MM-DD, inclusive.")
    end_date: str = Field(description="End date, YYYY-MM-DD, inclusive. At most 7 days.")


def _parse(value: str) -> date:
    value = value.strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.")


def _rows(raw: Any) -> list:
    if not isinstance(raw, dict):
        return []
    data = raw.get("data")
    if isinstance(data, list):
        return data
    result = raw.get("result")
    return result if isinstance(result, list) else []


def _compact(log: dict) -> dict[str, Any]:
    payload = log.get("payload") if isinstance(log.get("payload"), dict) else {}
    entry = {
        "updated_at": log.get("updated_at"),
        "username": log.get("username"),
        "service_name": log.get("service_name"),
        "section_name": log.get("section_name"),
    }
    entry.update({f"payload_{key}": payload.get(key) for key in ("id", "name", "status", "action", "type") if payload.get(key) is not None})
    return {key: value for key, value in entry.items() if value is not None}


def build_tools(context: ToolContext) -> list[StructuredTool]:
    client = rne_client(context)

    async def fetch_return_exchange_audit_logs(start_date: str, end_date: str) -> dict[str, Any]:
        try:
            start, end = _parse(start_date), _parse(end_date)
        except ValueError as exc:
            return {"error": str(exc)}
        if end < start:
            return {"error": "end_date must be on or after start_date"}
        span = (end - start).days + 1
        if span > _MAX_DAYS:
            return {"error": f"Date range is {span} days. At most {_MAX_DAYS} days per call."}
        raw = await client.get(
            "audit", token(context), start_date=start_date, end_date=end_date, action_taken=_ACTION
        )
        logs = [row for row in _rows(raw) if isinstance(row, dict)]
        changes = [_compact(row) for row in logs[:_LIMIT]]
        return {
            "start_date": start_date,
            "end_date": end_date,
            "action_taken": _ACTION,
            "count": len(logs),
            "summarized": len(changes),
            "truncated": len(logs) > _LIMIT,
            "changes": changes,
            "hint": "Payloads are snapshots, not guaranteed before/after diffs.",
        }

    return [
        StructuredTool.from_function(
            coroutine=fetch_return_exchange_audit_logs,
            name="fetch_return_exchange_audit_logs",
            description="Who changed Return & Exchange settings. Max 7 days. Dates are YYYY-MM-DD.",
            args_schema=AuditInput,
        )
    ]
