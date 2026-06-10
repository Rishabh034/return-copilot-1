from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools.context import ToolContext

logger = logging.getLogger(__name__)

_MAX_RANGE_DAYS = 7
_DEFAULT_PAGE_SIZE = 100
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


class FetchAuditLogsInput(BaseModel):
    start_date: str = Field(description="Start date (inclusive), e.g. 2026-06-01.")
    end_date: str = Field(
        description="End date (inclusive), e.g. 2026-06-07. Must be within 7 days of start_date."
    )
    action_taken: ActionTaken = Field(
        description=(
            "Dashboard module category to filter audit logs. Use 'Allocation' for "
            "filter/ordered-list rule changes; 'Carrier' for courier account changes."
        )
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


def _shape_audit_logs(raw: Any, start_date: str, end_date: str, action_taken: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {
            "start_date": start_date,
            "end_date": end_date,
            "action_taken": action_taken,
            "raw": raw,
        }

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

    return {
        "start_date": start_date,
        "end_date": end_date,
        "action_taken": action_taken,
        "count": raw.get("totalCount", len(logs)),
        "page_size": raw.get("pageSize"),
        "logs": logs,
    }


async def _fetch_audit_logs(
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
    return _shape_audit_logs(raw, api_start, api_end, action_taken)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def fetch_audit_logs(
        start_date: str,
        end_date: str,
        action_taken: ActionTaken,
    ) -> dict[str, Any]:
        return await _fetch_audit_logs(context, start_date, end_date, action_taken)

    return [
        StructuredTool.from_function(
            coroutine=fetch_audit_logs,
            name="fetch_audit_logs",
            description=(
                "Fetch dashboard activity/audit logs for a date range and module category. Use when "
                "investigating who changed allocation rules or config — use action_taken='Allocation' "
                "for filter/OL rule changes, 'Carrier' for account changes. "
                "action_taken must be one of the allowed module names (see enum). "
                "IMPORTANT: each call is limited to a maximum of 7 days — never request a longer range."
            ),
            args_schema=FetchAuditLogsInput,
        ),
    ]
