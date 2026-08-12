from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Self
from zoneinfo import ZoneInfo

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator

from app.rr_log_analyst.tools._common import shape_allocation_result, shape_user_request
from app.rr_log_analyst.tools.context import ToolContext

_IST = ZoneInfo("Asia/Kolkata")
_DEFAULT_LOOKUP_DAYS = 90
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S")

class FetchRRLogInput(BaseModel):
    """Unified RR log fetch: reference_number(s) and/or AWB/order_id (+ optional dates)."""

    reference_number: str | None = Field(
        default=None,
        description="Known shipment reference_number. Use this when the ref is already known.",
    )
    reference_numbers: list[str] | None = Field(
        default=None,
        description="Batch of reference numbers to fetch and compare (max 20).",
        min_length=1,
        max_length=20,
    )
    awb: str | None = Field(
        default=None,
        description=(
            "AWB / waybill. Provide this OR order_id (not both) when the reference_number is "
            "unknown. Optional when both start_date and end_date are supplied."
        ),
    )
    order_id: str | None = Field(
        default=None,
        description=(
            "Dashboard order ID. Provide this OR awb (not both) when the reference_number is "
            "unknown. Optional when both start_date and end_date are supplied."
        ),
    )
    start_date: str | None = Field(
        default=None,
        description=(
            "Shipment registration start (inclusive), e.g. 2026-06-01. With end_date makes "
            f"awb/order_id optional. Omit both for the default last-{_DEFAULT_LOOKUP_DAYS}-day window."
        ),
    )
    end_date: str | None = Field(
        default=None,
        description=(
            "Shipment registration end (inclusive), e.g. 2026-06-13. With start_date makes "
            f"awb/order_id optional. Omit both for the default last-{_DEFAULT_LOOKUP_DAYS}-day window."
        ),
    )
    page: int = Field(
        default=1,
        ge=1,
        description=(
            "1-indexed page for AWB/order_id/date-range lookups. When pagination.has_more is true, "
            "call again with pagination.next_page_number."
        ),
    )
    page_size: int = Field(
        default=100,
        ge=1,
        le=200,
        description="Shipments per page for AWB/order_id/date-range lookups (default 100).",
    )

    @model_validator(mode="after")
    def _validate_lookup_mode(self) -> Self:
        ref = (self.reference_number or "").strip() or None
        self.reference_number = ref
        refs = [str(r).strip() for r in (self.reference_numbers or []) if str(r).strip()]
        self.reference_numbers = refs or None

        awb = (self.awb or "").strip() or None
        order_id = (self.order_id or "").strip() or None
        start = (self.start_date or "").strip() or None
        end = (self.end_date or "").strip() or None
        self.awb, self.order_id, self.start_date, self.end_date = awb, order_id, start, end

        has_ref = bool(ref)
        has_refs = bool(refs)
        has_shipment = bool(awb or order_id or (start and end))

        modes = sum(bool(x) for x in (has_ref, has_refs, has_shipment))
        if modes == 0:
            raise ValueError(
                "Provide reference_number, reference_numbers, or awb/order_id "
                "(optionally with start_date + end_date)"
            )
        if modes > 1:
            raise ValueError(
                "Provide only one lookup mode: reference_number, reference_numbers, "
                "or awb/order_id/date-range — not a mix"
            )
        if awb and order_id:
            raise ValueError("Provide exactly one of awb or order_id, not both")
        if bool(start) != bool(end):
            raise ValueError(
                "Provide both start_date and end_date, or omit both for the default "
                f"{_DEFAULT_LOOKUP_DAYS}-day window"
            )
        if has_shipment and not awb and not order_id and not (start and end):
            raise ValueError(
                "Provide awb or order_id, or a start_date + end_date range to look up by date alone"
            )
        return self


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



def _has_next_page(raw: Any) -> bool | None:
    """Read results.has_next_page from a track-order shipment-details response."""
    if not isinstance(raw, dict):
        return None
    results = raw.get("results")
    if not isinstance(results, dict):
        return None
    has_next = results.get("has_next_page")
    if isinstance(has_next, bool):
        return has_next
    if has_next in (1, 0):
        return bool(has_next)
    return None


def _build_pagination(
    *,
    page: int,
    page_size: int,
    returned: int,
    has_more: bool,
) -> dict[str, Any]:
    pagination: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "returned": returned,
        "has_more": has_more,
    }
    if has_more:
        pagination["next_page_number"] = page + 1
    return pagination


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
                "and retry with fetch_rr_log."
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
    page: int = 1,
    page_size: int = 100,
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
            page_number=page,
            page_size=page_size,
        )
    except Exception as e:
        return {"error": str(e), "awb": awb, "order_id": order_id}

    shipments = _extract_shipments_from_track_order_response(raw)
    has_more = _has_next_page(raw)
    if has_more is None:
        has_more = len(shipments) >= page_size
    pagination = _build_pagination(
        page=page,
        page_size=page_size,
        returned=len(shipments),
        has_more=has_more,
    )
    if not shipments:
        # An empty page beyond the first just means we ran past the last page —
        # don't treat it as "shipment not found / widen the date range".
        if page > 1:
            return {
                "count": 0,
                "shipments": [],
                "reference_numbers": [],
                "message": f"No shipments on page {page}; the previous page was the last.",
                "pagination": pagination,
            }
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
            "pagination": pagination,
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
        "pagination": pagination,
    }


async def _load_rr_log_by_shipment(
    context: ToolContext,
    *,
    awb: str | None,
    order_id: str | None,
    start_date: str | None,
    end_date: str | None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    resolved = await _resolve_reference_number(
        context,
        awb=awb,
        order_id=order_id,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )
    if resolved.get("needs_date_range") or resolved.get("error"):
        return resolved

    shipments = resolved.get("shipments") or []
    date_range_used = resolved.get("date_range_used")
    pagination = resolved.get("pagination")

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
        "pagination": pagination,
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


async def _fetch_rr_logs_batch(context: ToolContext, reference_numbers: list[str]) -> dict[str, Any]:
    refs = [str(r).strip() for r in reference_numbers if str(r).strip()]
    if not refs:
        return {"error": "reference_numbers must contain at least one value"}

    async def _one(ref: str) -> dict:
        try:
            return await _load_rr_log(context, ref)
        except Exception as e:
            return {"reference_number": ref, "error": str(e)}

    logs = await asyncio.gather(*[_one(r) for r in refs])
    return {"count": len(logs), "logs": list(logs)}


async def _fetch_rr_log_unified(
    context: ToolContext,
    input: FetchRRLogInput,
) -> dict[str, Any]:
    if input.reference_number:
        return await _load_rr_log(context, input.reference_number)

    if input.reference_numbers:
        return await _fetch_rr_logs_batch(context, input.reference_numbers)

    return await _load_rr_log_by_shipment(
        context,
        awb=input.awb,
        order_id=input.order_id,
        start_date=input.start_date,
        end_date=input.end_date,
        page=input.page,
        page_size=input.page_size,
    )


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def fetch_rr_log(**kwargs: Any) -> dict[str, Any]:
        return await _fetch_rr_log_unified(context, FetchRRLogInput(**kwargs))

    return [
        StructuredTool.from_function(
            coroutine=fetch_rr_log,
            name="fetch_rr_log",
            description=(
                "PRIMARY — Fetch RR / allocation log(s). Each call takes exactly one lookup mode: "
                "(1) reference_number for a known ref, "
                "(2) reference_numbers for a batch compare (max 20), or "
                f"(3) awb OR order_id (optionally with start_date/end_date; defaults to last "
                f"{_DEFAULT_LOOKUP_DAYS} days). Mode 3 resolves the shipment then fetches the RR log. "
                "One mode per call is a per-call limit, NOT a limit on how many interpretations you "
                "may test: when an identifier is unlabeled, issue separate parallel calls in the same "
                "turn — one with reference_number, one with awb, one with order_id — and treat the "
                "identifier as unresolved only if all of them come back empty. "
                "If nothing is found in the default window, returns needs_date_range=true — ask for "
                "dates and retry. Returns the shaped log (order, filters_ran, preference_array) for "
                "a single ref; for AWB/order_id lookups returns "
                "{count, date_range_used, pagination, results:[{reference_number, shipment, rr_log}]}."
            ),
            args_schema=FetchRRLogInput,
        ),
    ]
