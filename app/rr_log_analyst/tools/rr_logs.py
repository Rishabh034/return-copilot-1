from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import FILTERS_RAN_KEYS, shape_allocation_result, shape_user_request
from app.rr_log_analyst.tools.allocation_views import (
    compact_allocation_summary,
    extract_order,
    extract_pipeline,
    extract_ranking,
    search_courier_in_allocation,
)
from app.rr_log_analyst.tools._common import FILTERS_RAN_KEYS, shape_allocation_result, shape_user_request
from app.rr_log_analyst.tools.allocation_views import (
    compact_allocation_summary,
    extract_order,
    extract_pipeline,
    extract_ranking,
    search_courier_in_allocation,
)
from app.rr_log_analyst.tools.context import ToolContext

_IST = ZoneInfo("Asia/Kolkata")
_DEFAULT_LOOKUP_DAYS = 90
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S")

PipelineStage = Literal[
    "1_active_accounts_filter",
    "1_blacklisted_accounts",
    "2_serviceable_accounts_filter",
    "2_1_serviceable_oda_accounts_filter",
    "clickpost_defined_and_custom_rule_matched",
    "3_clickpost_filters_rules_list",
    "3_clickpost_define_and_custom_filters_accounts_applied",
    "3_clickpost_define_and_custom_filters_accounts",
    "accounts_after_load_distribution_filter",
    "accounts_after_load_distribution",
    "priority_list_account_names",
    "4_ordered_list_rules_list",
    "order_list_matched_rule",
    "4_ordered_list_account_names_filters_applied",
    "ordered_list_filter_accounts",
    "5_accounts_after_intelligent_performance_allocation",
]


class ReferenceInput(BaseModel):
    reference_number: str = Field(description="The shipment/order reference number to look up.")


class ReferenceBatchInput(BaseModel):
    reference_numbers: list[str] = Field(
        description="List of shipment reference numbers to fetch (max 20).",
        min_length=1,
        max_length=20,
    )


class PipelineStageInput(BaseModel):
    reference_number: str = Field(description="Shipment reference number.")
    stage: PipelineStage = Field(description="Pipeline stage key from filters_ran to inspect.")
    account_id: int | None = Field(
        default=None,
        description="When checking if a specific carrier survived this stage, pass its account_id.",
    )
    cp_name: str = Field(
        default="",
        description="Alternatively match carrier by cp_name substring in this stage's account list.",
    )


class RankingInput(BaseModel):
    reference_number: str = Field(description="Shipment reference number.")
    account_id: int | None = Field(
        default=None,
        description="Return only this account_id from the final ranking.",
    )
    cp_name: str = Field(
        default="",
        description="Return only couriers whose cp_name or account_code contains this substring.",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="When no account_id/cp_name filter, return top N ranked couriers (default 5).",
    )


class SearchCourierInLogInput(BaseModel):
    reference_number: str = Field(description="Shipment reference number.")
    account_id: int | None = Field(default=None, description="Carrier account_id to trace.")
    cp_name: str = Field(default="", description="Carrier brand substring (e.g. 'Delhivery').")


class FetchRRLogByShipmentInput(BaseModel):
    awb: str | None = Field(
        default=None,
        description=(
            "AWB / waybill number. Not mandatory when both start_date and end_date are "
            "supplied; otherwise provide this OR order_id. Never provide both awb and order_id."
        ),
    )
    order_id: str | None = Field(
        default=None,
        description=(
            "Dashboard order ID. Not mandatory when both start_date and end_date are "
            "supplied; otherwise provide this OR awb. Never provide both awb and order_id."
        ),
    )
    start_date: str | None = Field(
        default=None,
        description=(
            "Shipment registration start (inclusive), e.g. 2026-06-01 or "
            "2026-06-01T00:00:00+05:30. Supplying start_date together with end_date makes "
            "awb/order_id optional. If omitted with end_date, defaults to the last 90 days."
        ),
    )
    end_date: str | None = Field(
        default=None,
        description=(
            "Shipment registration end (inclusive), e.g. 2026-06-13 or "
            "2026-06-13T23:59:59+05:30. Supplying end_date together with start_date makes "
            "awb/order_id optional. If omitted with start_date, defaults to the last 90 days."
        ),
    )


def _normalize_shipment_lookup_input(
    awb: str | None,
    order_id: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any] | tuple[str | None, str | None, str | None, str | None]:
    awb_value = (awb or "").strip() or None
    order_id_value = (order_id or "").strip() or None
    start = (start_date or "").strip() or None
    end = (end_date or "").strip() or None

    if awb_value and order_id_value:
        return {"error": "Provide exactly one of awb or order_id, not both"}

    if bool(start) != bool(end):
        return {
            "error": (
                "Provide both start_date and end_date, or omit both for the default "
                f"{_DEFAULT_LOOKUP_DAYS}-day window"
            ),
        }

    if not awb_value and not order_id_value and not (start and end):
        return {
            "error": (
                "Provide awb or order_id, or a start_date + end_date range to look up by "
                "date alone."
            ),
        }

    return awb_value, order_id_value, start, end


def _format_ist_datetime(value: datetime) -> str:
    local = value.astimezone(_IST)
    return local.strftime("%Y-%m-%dT%H:%M:%S") + "+05:30"


def _parse_lookup_date(value: str, *, end_of_day: bool) -> datetime:
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
            if end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            else:
                parsed = parsed.replace(hour=0, minute=0, second=0)
            return parsed.replace(tzinfo=_IST)
        except ValueError:
            continue

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as e:
        raise ValueError(
            f"Invalid date '{value}'. Use YYYY-MM-DD or an ISO timestamp with +05:30 offset."
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_IST)
    return parsed.astimezone(_IST)


def _default_lookup_date_range() -> tuple[str, str, date, date]:
    now = datetime.now(_IST)
    end_dt = now.replace(hour=23, minute=59, second=59)
    start_dt = (now - timedelta(days=_DEFAULT_LOOKUP_DAYS - 1)).replace(
        hour=0, minute=0, second=0
    )
    return (
        _format_ist_datetime(start_dt),
        _format_ist_datetime(end_dt),
        start_dt.date(),
        end_dt.date(),
    )


def _resolve_lookup_date_range(
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, str, date, date, bool]:
    if not start_date and not end_date:
        start_ist, end_ist, start_day, end_day = _default_lookup_date_range()
        return start_ist, end_ist, start_day, end_day, True

    start_dt = _parse_lookup_date(start_date or "", end_of_day=False)
    end_dt = _parse_lookup_date(end_date or "", end_of_day=True)
    if end_dt < start_dt:
        raise ValueError("end_date must be on or after start_date")
    return (
        _format_ist_datetime(start_dt),
        _format_ist_datetime(end_dt),
        start_dt.date(),
        end_dt.date(),
        False,
    )


def _extract_shipments_from_track_order_response(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    results = raw.get("results")
    if not isinstance(results, dict):
        return []
    shipments = results.get("shipment_details")
    if not isinstance(shipments, list):
        return []
    return [s for s in shipments if isinstance(s, dict)]


def _shape_shipment(shipment: dict[str, Any], reference_number: str) -> dict[str, Any]:
    return {
        "awb": shipment.get("awb"),
        "order_id": shipment.get("order_id"),
        "reference_number": reference_number,
        "cp_id": shipment.get("cp_id"),
        "cp_name": shipment.get("cp_name"),
        "created_at": shipment.get("created_at"),
    }


def _shipment_not_found_response(
    *,
    awb: str | None,
    order_id: str | None,
    used_default_date_range: bool,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    if awb:
        identifier = f"awb={awb}"
    elif order_id:
        identifier = f"order_id={order_id}"
    else:
        identifier = "the given date range"
    if used_default_date_range:
        return {
            "needs_date_range": True,
            "message": (
                f"No shipment found for {identifier} in the default last-{_DEFAULT_LOOKUP_DAYS}-day "
                f"window ({start_date.isoformat()} to {end_date.isoformat()}). "
                "Ask the user for the shipment registration date range (start_date and end_date) "
                "and retry with fetch_rr_log_by_shipment."
            ),
            "default_range_tried": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days": _DEFAULT_LOOKUP_DAYS,
            },
            "awb": awb,
            "order_id": order_id,
        }
    return {
        "error": (
            f"No shipment found for {identifier} between {start_date.isoformat()} and "
            f"{end_date.isoformat()}."
        ),
        "awb": awb,
        "order_id": order_id,
        "date_range_used": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    }


async def _resolve_reference_number(
    context: ToolContext,
    *,
    awb: str | None,
    order_id: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    try:
        start_ist, end_ist, start_day, end_day, used_default = _resolve_lookup_date_range(
            start_date,
            end_date,
        )
    except ValueError as e:
        return {"error": str(e)}

    try:
        raw = await context.clickpost_client.search_shipment_details(
            context.token,
            awb_list=[awb] if awb else None,
            order_id_list=[order_id] if order_id else None,
            shipment_registration_start_date_ist=start_ist,
            shipment_registration_end_date_ist=end_ist,
        )
    except Exception as e:
        return {"error": str(e), "awb": awb, "order_id": order_id}

    shipments = _extract_shipments_from_track_order_response(raw)
    if not shipments:
        return _shipment_not_found_response(
            awb=awb,
            order_id=order_id,
            used_default_date_range=used_default,
            start_date=start_day,
            end_date=end_day,
        )

    shaped: list[dict[str, Any]] = []
    for shipment in shipments:
        reference_number = (shipment.get("reference_number") or "").strip()
        if not reference_number:
            continue
        shaped.append(_shape_shipment(shipment, reference_number))

    if not shaped:
        return {
            "error": "Shipment(s) found but reference_number is missing in track-order response.",
            "awb": awb,
            "order_id": order_id,
        }

    date_range_used = {
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "used_default": used_default,
    }

    return {
        "count": len(shaped),
        "shipments": shaped,
        "reference_numbers": [s["reference_number"] for s in shaped],
        "date_range_used": date_range_used,
    }


async def _load_rr_log_by_shipment(
    context: ToolContext,
    *,
    awb: str | None,
    order_id: str | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    resolved = await _resolve_reference_number(
        context,
        awb=awb,
        order_id=order_id,
        start_date=start_date,
        end_date=end_date,
    )
    if resolved.get("needs_date_range") or resolved.get("error"):
        return resolved

    shipments = resolved.get("shipments") or []
    date_range_used = resolved.get("date_range_used")

    async def _one(shipment: dict[str, Any]) -> dict[str, Any]:
        reference_number = shipment["reference_number"]
        try:
            rr_log = await _load_rr_log(context, reference_number)
        except Exception as e:
            return {
                "reference_number": reference_number,
                "shipment": shipment,
                "error": f"Resolved reference_number but RR log fetch failed: {e}",
            }
        if rr_log.get("error"):
            return {
                "reference_number": reference_number,
                "shipment": shipment,
                **rr_log,
            }
        return {
            "reference_number": reference_number,
            "shipment": shipment,
            "rr_log": rr_log,
        }

    results = await asyncio.gather(*[_one(s) for s in shipments])

    return {
        "count": len(results),
        "date_range_used": date_range_used,
        "results": list(results),
    }


def _extract_single_allocation(entry: dict) -> tuple[dict[str, Any] | None, dict | None, dict | None]:
    """Extract order, allocation body, and timestamps from one result entry."""
    order = shape_user_request(entry.get("user_request"))
    timestamps = entry.get("order_timestamp") if isinstance(entry.get("order_timestamp"), dict) else None

    clickpost_response = entry.get("clickpost_response")
    if isinstance(clickpost_response, dict):
        inner = clickpost_response.get("result")
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return order, inner[0], timestamps

    return order, None, timestamps


def _extract_allocation_fallback(raw: dict) -> dict | None:
    for wrapper in ("result", "data", "log"):
        inner = raw.get(wrapper)
        if isinstance(inner, dict) and (
            "filters_ran" in inner or "preference_array" in inner
        ):
            return inner
    if "filters_ran" in raw or "preference_array" in raw:
        return raw
    return None


def _shape_rr_log(reference_number: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"reference_number": reference_number, "error": "Unexpected response shape"}

    result = raw.get("result")
    if not isinstance(result, list) or not result:
        return {"reference_number": reference_number, "error": "No allocation data in response"}

    attempts: list[dict[str, Any]] = []
    for entry in result:
        if not isinstance(entry, dict):
            continue
        order, body, timestamps = _extract_single_allocation(entry)
        if body is None:
            continue
        shaped = shape_allocation_result(body, order=order, reference_number=reference_number)
        if timestamps:
            shaped["timestamps"] = timestamps
        attempts.append(shaped)

    if not attempts:
        return {"reference_number": reference_number, "error": "No allocation data in response"}

    latest = attempts[-1]
    return {
        **latest,
        "attempt_count": len(attempts),
        "attempts": attempts,
    }


async def _load_rr_log(context: ToolContext, reference_number: str) -> dict[str, Any]:
    reference_number = reference_number.strip()
    if not reference_number:
        return {"error": "reference_number is required"}

    raw = await context.clickpost_client.get_rr_log(
        token=context.token,
        reference_number=reference_number,
    )
    return _shape_rr_log(reference_number, raw)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def fetch_rr_log(reference_number: str) -> dict[str, Any]:
        return await _load_rr_log(context, reference_number)

    async def fetch_rr_log_by_shipment(
        awb: str | None = None,
        order_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_shipment_lookup_input(awb, order_id, start_date, end_date)
        if isinstance(normalized, dict):
            return normalized
        awb_value, order_id_value, start, end = normalized
        return await _load_rr_log_by_shipment(
            context,
            awb=awb_value,
            order_id=order_id_value,
            start_date=start,
            end_date=end,
        )

    async def resolve_reference_number(
        awb: str | None = None,
        order_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        normalized = _normalize_shipment_lookup_input(awb, order_id, start_date, end_date)
        if isinstance(normalized, dict):
            return normalized
        awb_value, order_id_value, start, end = normalized
        return await _resolve_reference_number(
            context,
            awb=awb_value,
            order_id=order_id_value,
            start_date=start,
            end_date=end,
        )

    async def fetch_rr_logs_batch(reference_numbers: list[str]) -> dict[str, Any]:
        refs = [str(r).strip() for r in reference_numbers if str(r).strip()]
        if not refs:
            return {"error": "reference_numbers must contain at least one value"}

        async def _one(ref: str) -> dict:
            try:
                return await _load_rr_log(context, ref)
            except Exception as e:
                return {"reference_number": ref, "error": str(e)}
        async def _one(ref: str) -> dict:
            try:
                return await _load_rr_log(context, ref)
            except Exception as e:
                return {"reference_number": ref, "error": str(e)}

        logs = await asyncio.gather(*[_one(r) for r in refs])
        return {"count": len(logs), "logs": list(logs)}
        logs = await asyncio.gather(*[_one(r) for r in refs])
        return {"count": len(logs), "logs": list(logs)}

    async def summarize_rr_log(reference_number: str) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return compact_allocation_summary(log)

    async def get_rr_log_order(reference_number: str) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return extract_order(log)

    async def get_rr_log_pipeline(
        reference_number: str,
        stage: PipelineStage,
        account_id: int | None = None,
        cp_name: str = "",
    ) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return extract_pipeline(log, stage, account_id=account_id, cp_name=cp_name)

    async def get_rr_log_ranking(
        reference_number: str,
        account_id: int | None = None,
        cp_name: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return extract_ranking(log, account_id=account_id, cp_name=cp_name, limit=limit)

    async def search_rr_log_courier(
        reference_number: str,
        account_id: int | None = None,
        cp_name: str = "",
    ) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return search_courier_in_allocation(log, account_id=account_id, cp_name=cp_name)

    async def summarize_rr_logs_batch(reference_numbers: list[str]) -> dict[str, Any]:
        refs = [str(r).strip() for r in reference_numbers if str(r).strip()]
        if not refs:
            return {"error": "reference_numbers must contain at least one value"}

        async def _one(ref: str) -> dict:
            try:
                return compact_allocation_summary(await _load_rr_log(context, ref))
            except Exception as e:
                return {"reference_number": ref, "error": str(e)}

        summaries = await asyncio.gather(*[_one(r) for r in refs])
        return {"count": len(summaries), "summaries": list(summaries)}

    _stages_hint = ", ".join(FILTERS_RAN_KEYS)
    async def summarize_rr_log(reference_number: str) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return compact_allocation_summary(log)

    async def get_rr_log_order(reference_number: str) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return extract_order(log)

    async def get_rr_log_pipeline(
        reference_number: str,
        stage: PipelineStage,
        account_id: int | None = None,
        cp_name: str = "",
    ) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return extract_pipeline(log, stage, account_id=account_id, cp_name=cp_name)

    async def get_rr_log_ranking(
        reference_number: str,
        account_id: int | None = None,
        cp_name: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return extract_ranking(log, account_id=account_id, cp_name=cp_name, limit=limit)

    async def search_rr_log_courier(
        reference_number: str,
        account_id: int | None = None,
        cp_name: str = "",
    ) -> dict[str, Any]:
        log = await _load_rr_log(context, reference_number)
        return search_courier_in_allocation(log, account_id=account_id, cp_name=cp_name)

    async def summarize_rr_logs_batch(reference_numbers: list[str]) -> dict[str, Any]:
        refs = [str(r).strip() for r in reference_numbers if str(r).strip()]
        if not refs:
            return {"error": "reference_numbers must contain at least one value"}

        async def _one(ref: str) -> dict:
            try:
                return compact_allocation_summary(await _load_rr_log(context, ref))
            except Exception as e:
                return {"reference_number": ref, "error": str(e)}

        summaries = await asyncio.gather(*[_one(r) for r in refs])
        return {"count": len(summaries), "summaries": list(summaries)}

    _stages_hint = ", ".join(FILTERS_RAN_KEYS)

    return [
        StructuredTool.from_function(
            coroutine=fetch_rr_log_by_shipment,
            name="fetch_rr_log_by_shipment",
            description=(
                "PRIMARY — When the user gives an AWB or order_id (not reference_number): resolves "
                f"reference_number via track-order, then fetches the RR log. awb/order_id are not "
                "mandatory when both start_date and end_date are supplied (date-range-only lookup). "
                f"If start_date/end_date are omitted, searches the last {_DEFAULT_LOOKUP_DAYS} days "
                "first. If nothing is found, returns needs_date_range=true — ask the user for dates "
                "and retry with the same awb or order_id plus start_date and end_date. "
                "Returns {count, date_range_used, results:[{reference_number, shipment, rr_log}]} — "
                "one entry per matched shipment (date-range lookups can match many)."
            ),
            args_schema=FetchRRLogByShipmentInput,
        ),
        StructuredTool.from_function(
            coroutine=resolve_reference_number,
            name="resolve_reference_number",
            description=(
                "SUB-TOOL — Resolve AWB or order_id to reference_number only (no RR log). "
                "awb/order_id are not mandatory when both start_date and end_date are supplied "
                f"(date-range-only lookup). Uses the last {_DEFAULT_LOOKUP_DAYS} days when dates "
                "are omitted; returns needs_date_range=true if not found so you can ask the user "
                "for a wider window. Returns {count, shipments:[...], reference_numbers:[...], "
                "date_range_used} — all matched shipments."
            ),
            args_schema=FetchRRLogByShipmentInput,
        ),
        StructuredTool.from_function(
            coroutine=fetch_rr_log,
            name="fetch_rr_log",
            description=(
                "PRIMARY — Full RR log when reference_number is already known. For AWB or order_id, "
                "use fetch_rr_log_by_shipment first. Returns order, filters_ran pipeline trace, and "
                "complete preference_array. If too large, use summarize_rr_log or get_rr_log_* sub-tools."
            ),
            args_schema=ReferenceInput,
        ),
        StructuredTool.from_function(
            coroutine=fetch_rr_logs_batch,
            name="fetch_rr_logs_batch",
            description=(
                "PRIMARY — Full RR logs for multiple reference numbers (max 20). Use to compare "
                "allocations. If too large, use summarize_rr_logs_batch."
            ),
            args_schema=ReferenceBatchInput,
        ),
        StructuredTool.from_function(
            coroutine=summarize_rr_log,
            name="summarize_rr_log",
            description=(
                "SUB-TOOL — Compact RR log overview: matched filter, top courier, total ranked. "
                "Use when fetch_rr_log result is too large."
            ),
            args_schema=ReferenceInput,
        ),
        StructuredTool.from_function(
            coroutine=get_rr_log_order,
            name="get_rr_log_order",
            description="SUB-TOOL — Order/request fields only from an RR log.",
            args_schema=ReferenceInput,
        ),
        StructuredTool.from_function(
            coroutine=get_rr_log_pipeline,
            name="get_rr_log_pipeline",
            description=(
                f"SUB-TOOL — One pipeline stage from filters_ran. Stages: {_stages_hint}. "
                "Pass account_id or cp_name to check carrier survival at that stage."
            ),
            args_schema=PipelineStageInput,
        ),
        StructuredTool.from_function(
            coroutine=get_rr_log_ranking,
            name="get_rr_log_ranking",
            description="SUB-TOOL — Final ranking only; filter by account_id/cp_name or top N.",
            args_schema=RankingInput,
        ),
        StructuredTool.from_function(
            coroutine=search_rr_log_courier,
            name="search_rr_log_courier",
            description=(
                "SUB-TOOL — Trace one courier through pipeline stages. Requires account_id or cp_name."
            ),
            args_schema=SearchCourierInLogInput,
        ),
        StructuredTool.from_function(
            coroutine=summarize_rr_logs_batch,
            name="summarize_rr_logs_batch",
            description="SUB-TOOL — Compact summaries for multiple orders when fetch_rr_logs_batch is too large.",
            args_schema=ReferenceBatchInput,
        ),
    ]
