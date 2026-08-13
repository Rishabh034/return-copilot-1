from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools.context import ToolContext
from app.rr_log_analyst.tools.rules import _fetch_all_raw_rules, _resolve_rules_by_query

logger = logging.getLogger(__name__)

_MAX_RANGE_DAYS = 7
_DEFAULT_PAGE_SIZE = 100
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ")

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
    start_date: str = Field(description="Start date (inclusive), YYYY-MM-DD.")
    end_date: str = Field(
        description=(
            "End date (inclusive), YYYY-MM-DD. This tool allows at most 7 days "
            "between start_date and end_date."
        )
    )
    action_taken: ActionTaken = Field(
        description=(
            "Filters logs by section_name (exact match). For allocation rule "
            "investigations, use 'Allocation'."
        )
    )


class FetchAuditLogsForRuleInput(BaseModel):
    rule_id: str = Field(
        description="Rule id, exact rule name, or text containing a rule name."
    )
    action_taken: ActionTaken = Field(
        default="Allocation",
        description="section_name filter passed to fetch_audit_logs.",
    )


def _parse_date(value: str) -> date:
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.")


def _normalize_datetime(value: datetime) -> datetime:
    """Normalize to UTC-aware so naive and Z-suffixed timestamps can be compared."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


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
                f"Date range is {span_days} days. This tool allows at most "
                f"{_MAX_RANGE_DAYS} days per call. Use shorter windows or separate "
                "non-overlapping calls."
            )
        }
    return start, end


def _shape_log_entry(log: dict[str, Any]) -> dict[str, Any]:
    payload = log.get("payload") if isinstance(log.get("payload"), dict) else {}
    shaped: dict[str, Any] = {
        "updated_at": log.get("updated_at"),
        "username": log.get("username"),
        "section_name": log.get("section_name"),
        "service_name": log.get("service_name"),
        "ip_address": log.get("ip_address"),
    }
    if payload:
        shaped["payload"] = payload
        payload_updated_at = payload.get("updated_at")
        if payload_updated_at:
            shaped["payload_updated_at"] = payload_updated_at
    return {k: v for k, v in shaped.items() if v is not None}


def _updated_at_bounds(logs: list[dict[str, Any]]) -> dict[str, str | None]:
    timestamps: list[datetime] = []
    for log in logs:
        for key in ("updated_at", "payload_updated_at"):
            parsed = _parse_datetime(str(log.get(key) or ""))
            if parsed is not None:
                timestamps.append(_normalize_datetime(parsed))
    if not timestamps:
        return {"latest_updated_at": None, "earliest_updated_at": None}
    timestamps.sort()
    return {
        "earliest_updated_at": timestamps[0].isoformat(),
        "latest_updated_at": timestamps[-1].isoformat(),
    }


def _sort_logs_by_updated_at(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(log: dict[str, Any]) -> datetime:
        parsed = _parse_datetime(str(log.get("updated_at") or ""))
        if parsed is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return _normalize_datetime(parsed)

    return sorted(logs, key=sort_key, reverse=True)


def _shape_audit_logs(raw: Any, start_date: str, end_date: str, action_taken: str) -> dict[str, Any]:
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

    shaped_logs = [_shape_log_entry(log) for log in logs if isinstance(log, dict)]
    shaped_logs = _sort_logs_by_updated_at(shaped_logs)
    bounds = _updated_at_bounds(shaped_logs)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "action_taken": action_taken,
        "count": raw.get("totalCount", len(shaped_logs)),
        "page_size": raw.get("pageSize"),
        **bounds,
        "logs": shaped_logs,
    }


def _log_matches_rule(log: dict[str, Any], rule: dict[str, Any]) -> bool:
    payload = log.get("payload")
    if not isinstance(payload, dict):
        return False

    rule_id = rule.get("id")
    rule_name = rule.get("name")
    payload_id = payload.get("id")
    payload_name = payload.get("name")

    if rule_id is not None and payload_id is not None and str(rule_id) == str(payload_id):
        return True
    if rule_name and payload_name and str(rule_name).lower() == str(payload_name).lower():
        return True
    return False


async def _fetch_all_audit_log_pages(
    context: ToolContext,
    start_date: str,
    end_date: str,
    action_taken: str,
) -> dict[str, Any]:
    all_logs: list[dict[str, Any]] = []
    page = 1
    last_raw: dict[str, Any] = {}
    total_count = 0

    while True:
        raw = await context.clickpost_client.get_audit_logs(
            token=context.token,
            start_date=start_date,
            end_date=end_date,
            action_taken=action_taken,
            page_size=_DEFAULT_PAGE_SIZE,
            page=page,
        )
        if not isinstance(raw, dict):
            return {"data": all_logs, "totalCount": len(all_logs), "pageSize": _DEFAULT_PAGE_SIZE}

        last_raw = raw
        page_data = raw.get("data")
        if isinstance(page_data, list):
            all_logs.extend(entry for entry in page_data if isinstance(entry, dict))

        total_count = int(raw.get("totalCount") or len(all_logs))
        if raw.get("nextUrl") is None or len(all_logs) >= total_count:
            break

        page += 1
        if page > max(1, (total_count // _DEFAULT_PAGE_SIZE) + 2):
            logger.warning(
                "activity-log pagination stopped early: page=%s totalCount=%s fetched=%s",
                page,
                total_count,
                len(all_logs),
            )
            break

    return {
        **last_raw,
        "data": all_logs,
        "totalCount": total_count or len(all_logs),
        "pageSize": last_raw.get("pageSize", _DEFAULT_PAGE_SIZE),
        "nextUrl": None,
        "previousUrl": None,
    }


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

    raw = await _fetch_all_audit_log_pages(context, api_start, api_end, action_taken)
    logger.debug("activity-log response: totalCount=%s fetched=%s", raw.get("totalCount"), len(raw.get("data", [])))
    return _shape_audit_logs(raw, api_start, api_end, action_taken)


async def _fetch_audit_logs_for_rule(
    context: ToolContext,
    rule_id: str,
    action_taken: str,
) -> dict[str, Any]:
    resolved = _resolve_rules_by_query(await _fetch_all_raw_rules(context), rule_id.strip())
    if "error" in resolved:
        return resolved

    if "rules" in resolved:
        return {
            **resolved,
            "hint": "Resolve to a single rule first, then retry fetch_audit_logs_for_rule.",
        }

    rule = resolved["rule"]
    rule_updated_at = rule.get("updated_at")
    if not rule_updated_at:
        return {
            "error": (
                f"Rule '{rule.get('name')}' has no updated_at timestamp. "
                "Use fetch_audit_logs with an explicit date range instead."
            ),
            "rule": rule,
        }

    parsed = _parse_datetime(str(rule_updated_at))
    if parsed is None:
        return {
            "error": f"Could not parse rule updated_at: {rule_updated_at!r}",
            "rule": rule,
        }

    target_date = parsed.date().isoformat()
    audit = await _fetch_audit_logs_raw(context, target_date, target_date, action_taken)
    if "error" in audit:
        return {**audit, "rule": rule, "rule_last_updated_at": parsed.isoformat()}

    matching_logs = [log for log in audit.get("logs", []) if _log_matches_rule(log, rule)]
    return {
        "rule": rule,
        "rule_last_updated_at": parsed.isoformat(),
        "audit_window": {"start_date": target_date, "end_date": target_date},
        "action_taken": action_taken,
        "matching_log_count": len(matching_logs),
        "matching_logs": matching_logs,
        "all_logs_in_window": audit,
    }


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def fetch_audit_logs(
        start_date: str,
        end_date: str,
        action_taken: ActionTaken,
    ) -> dict[str, Any]:
        return await _fetch_audit_logs_raw(context, start_date, end_date, action_taken)

    async def fetch_audit_logs_for_rule(
        rule_id: str,
        action_taken: ActionTaken = "Allocation",
    ) -> dict[str, Any]:
        return await _fetch_audit_logs_for_rule(context, rule_id, action_taken)

    return [
        StructuredTool.from_function(
            coroutine=fetch_audit_logs,
            name="fetch_audit_logs",
            description=(
                "Fetch Clickpost dashboard activity logs for a date range. "
                "action_taken filters by section_name (e.g. 'Allocation' for allocation APIs). "
                "Each log has updated_at, username, service_name, and a payload snapshot "
                "(not a guaranteed before/after diff). Fetches all API pages for the window. "
                "This tool allows at most 7 days per call."
            ),
            args_schema=FetchAuditLogsInput,
        )
    ]
