from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools.context import ToolContext
logger = logging.getLogger(__name__)

_MAX_RANGE_DAYS = 7
_DEFAULT_PAGE_SIZE = 100
_MAX_SUMMARY_LOGS = 10
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S")

ALLOWED_ACTION_TAKEN = (
    "Track",
    "Channel",
    "Cash Reconciliation",
    "Carrier",
    "Login",
    "Manifest",
    "Allocation",
    "Notifications",
    "Return & Exchange",
    "Control Tower",
    "NDR",
    "Analytics",
    "Dashboard",
    "Settings",
    "Dispatch",
    "Access Control",
    "Cancel Orders",
    "QC Dashboard",
    "Quick Commerce",
)

ActionTaken = Literal[
    "Track",
    "Channel",
    "Cash Reconciliation",
    "Carrier",
    "Login",
    "Manifest",
    "Allocation",
    "Notifications",
    "Return & Exchange",
    "Control Tower",
    "NDR",
    "Analytics",
    "Dashboard",
    "Settings",
    "Dispatch",
    "Access Control",
    "Cancel Orders",
    "QC Dashboard",
    "Quick Commerce",
]


class AuditLogRangeInput(BaseModel):
    start_date: str = Field(description="Start date (inclusive), e.g. 2026-06-01.")
    end_date: str = Field(
        description="End date (inclusive), e.g. 2026-06-07. Must be within 7 days of start_date."
    )
    action_taken: ActionTaken = Field(
        description=(
            "Dashboard module category. Use 'Allocation' for filter/OL rule changes; "
            "'Carrier' for courier account changes."
        )
    )


class SearchAuditLogsInput(AuditLogRangeInput):
    query: str = Field(
        description=(
            "Required search text — matched against username, action, description, rule names, "
            "or any string field in the log entry (case-insensitive substring)."
        ),
    )


def _parse_date(value: str) -> date:
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.")


def _validate_date_range(start_date: str, end_date: str) -> tuple[date, date] | dict[str, str]:
    try:
        start = _parse_date(start_date)
        end = _parse_date(end_date)
    except ValueError as e:
        return {"error": str(e)}

    if end < start:
        return {"error": "end_date must be on or after start_date"}

    span_days = (end - start).days + 1
    if span_days > _MAX_RANGE_DAYS:
        return {
            "error": (
                f"Date range is {span_days} days. Audit logs may only be fetched for up to "
                f"{_MAX_RANGE_DAYS} days per request (to avoid overloading the system). "
                "Use a shorter window or make separate calls for non-overlapping 7-day chunks."
            )
        }
    return start, end


def _extract_logs(raw: Any) -> list[dict]:
    if not isinstance(raw, dict):
        return []

    logs = raw.get("data")
    if not isinstance(logs, list):
        result = raw.get("result")
        if isinstance(result, list):
            logs = result
        elif isinstance(result, dict):
            nested = result.get("logs") or result.get("audit_logs") or result.get("data")
            logs = nested if isinstance(nested, list) else []
        else:
            logs = []
    return [entry for entry in logs if isinstance(entry, dict)]


def _compact_audit_entry(entry: dict) -> dict[str, Any]:
    preferred = (
        "id",
        "userName",
        "username",
        "user",
        "action",
        "actionTaken",
        "module",
        "description",
        "details",
        "createdAt",
        "created_at",
        "timestamp",
        "date",
        "ipAddress",
        "entity",
        "entityName",
    )
    compact = {key: entry.get(key) for key in preferred if entry.get(key) is not None}
    if compact:
        return compact

    trimmed: dict[str, Any] = {}
    for key, value in entry.items():
        if isinstance(value, (str, int, float, bool)) and value != "":
            text = str(value)
            trimmed[key] = text[:300] + "..." if len(text) > 300 else value
    return trimmed


def _audit_entry_text(entry: dict) -> str:
    return json.dumps(entry, default=str).lower()


def _matches_audit_query(entry: dict, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return False
    return q in _audit_entry_text(entry)


async def _fetch_audit_logs_raw(
    context: ToolContext,
    start_date: str,
    end_date: str,
    action_taken: str,
) -> dict[str, Any]:
    start_date = start_date.strip()
    end_date = end_date.strip()
    action_taken = action_taken.strip()

    if not start_date:
        return {"error": "start_date is required"}
    if not end_date:
        return {"error": "end_date is required"}
    if not action_taken:
        return {"error": "action_taken is required"}
    if action_taken not in ALLOWED_ACTION_TAKEN:
        return {
            "error": (
                f"Invalid action_taken '{action_taken}'. "
                f"Must be one of: {', '.join(ALLOWED_ACTION_TAKEN)}"
            )
        }

    validated = _validate_date_range(start_date, end_date)
    if isinstance(validated, dict):
        return validated

    start, end = validated
    api_start = start.isoformat()
    api_end = end.isoformat()

    raw = await context.clickpost_client.get_audit_logs(
        token=context.token,
        start_date=api_start,
        end_date=api_end,
        action_taken=action_taken,
        page_size=_DEFAULT_PAGE_SIZE,
    )
    logger.debug(
        "activity-log response: totalCount=%s",
        raw.get("totalCount") if isinstance(raw, dict) else "?",
    )

    logs = _extract_logs(raw)
    return {
        "start_date": api_start,
        "end_date": api_end,
        "action_taken": action_taken,
        "count": raw.get("totalCount", len(logs)) if isinstance(raw, dict) else len(logs),
        "page_size": raw.get("pageSize") if isinstance(raw, dict) else None,
        "logs": logs,
    }


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def fetch_audit_logs(
        start_date: str,
        end_date: str,
        action_taken: ActionTaken,
    ) -> dict[str, Any]:
        return await _fetch_audit_logs_raw(context, start_date, end_date, action_taken)

    async def summarize_audit_logs(
        start_date: str,
        end_date: str,
        action_taken: ActionTaken,
    ) -> dict[str, Any]:
        payload = await _fetch_audit_logs_raw(context, start_date, end_date, action_taken)
        if "error" in payload:
            return payload

        logs = payload.get("logs") or []
        compact = [_compact_audit_entry(entry) for entry in logs[:_MAX_SUMMARY_LOGS]]
        return {
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
            "action_taken": payload["action_taken"],
            "count": payload["count"],
            "sample_size": len(compact),
            "logs_sample": compact,
            "hint": (
                "Compact sample only. Use search_audit_logs with a query to find specific changes "
                "(rule name, username, action text)."
            ),
        }

    async def search_audit_logs(
        start_date: str,
        end_date: str,
        action_taken: ActionTaken,
        query: str,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {
                "error": (
                    "query is required. Pass a rule name, username, or action text to search for. "
                    "Use summarize_audit_logs for a date-range overview."
                )
            }

        payload = await _fetch_audit_logs_raw(context, start_date, end_date, action_taken)
        if "error" in payload:
            return payload

        logs = payload.get("logs") or []
        matching = [entry for entry in logs if _matches_audit_query(entry, query)]
        return {
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
            "action_taken": payload["action_taken"],
            "query": query,
            "total_in_range": payload["count"],
            "total_matching": len(matching),
            "logs": [_compact_audit_entry(entry) for entry in matching],
        }

    return [
        StructuredTool.from_function(
            coroutine=fetch_audit_logs,
            name="fetch_audit_logs",
            description=(
                "PRIMARY — Full audit/activity logs for a date range and module category. Max 7-day "
                "window. action_taken='Allocation' for rule changes, 'Carrier' for account changes. "
                "If too large, use summarize_audit_logs or search_audit_logs."
            ),
            args_schema=AuditLogRangeInput,
        ),
        StructuredTool.from_function(
            coroutine=summarize_audit_logs,
            name="summarize_audit_logs",
            description=(
                "SUB-TOOL — Audit log count plus a small sample when fetch_audit_logs is too large."
            ),
            args_schema=AuditLogRangeInput,
        ),
        StructuredTool.from_function(
            coroutine=search_audit_logs,
            name="search_audit_logs",
            description=(
                "SUB-TOOL — Search audit logs by query substring (username, rule name, action text)."
            ),
            args_schema=SearchAuditLogsInput,
        ),
    ]
