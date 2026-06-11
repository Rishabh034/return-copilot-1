from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import shape_allocation_result, shape_user_request
from app.rr_log_analyst.tools.context import ToolContext


class FetchRRLogInput(BaseModel):
    reference_number: str = Field(description="The shipment/order reference number to look up.")


class FetchRRLogsBatchInput(BaseModel):
    reference_numbers: list[str] = Field(
        description="List of shipment reference numbers to fetch.",
        min_length=1,
        max_length=20,
    )


def _extract_allocation_from_envelope(raw: dict) -> tuple[dict[str, Any] | None, dict | None]:
    """Parse the request_logs envelope into (order, allocation body)."""
    result = raw.get("result")
    if not isinstance(result, dict):
        return None, None

    order = shape_user_request(result.get("user_request"))

    clickpost_response = result.get("clickpost_response")
    if isinstance(clickpost_response, dict):
        inner = clickpost_response.get("result")
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            return order, inner[0]

    return order, None


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

    order, body = _extract_allocation_from_envelope(raw)
    if body is None:
        body = _extract_allocation_fallback(raw)
    if body is None:
        return {"reference_number": reference_number, "error": "No allocation data in response"}

    return shape_allocation_result(body, order=order, reference_number=reference_number)


async def _fetch_rr_log(context: ToolContext, reference_number: str) -> dict[str, Any]:
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
            raw = await context.clickpost_client.get_rr_log(
                token=context.token,
                reference_number=ref,
            )
            return _shape_rr_log(ref, raw)
        except Exception as e:
            return {"reference_number": ref, "error": str(e)}

    logs = await asyncio.gather(*[_one(r) for r in refs])
    return {"count": len(logs), "logs": list(logs)}


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def fetch_rr_log(reference_number: str) -> dict[str, Any]:
        return await _fetch_rr_log(context, reference_number)

    async def fetch_rr_logs_batch(reference_numbers: list[str]) -> dict[str, Any]:
        return await _fetch_rr_logs_batch(context, reference_numbers)

    return [
        StructuredTool.from_function(
            coroutine=fetch_rr_log,
            name="fetch_rr_log",
            description=(
                "Fetch the recommendation/allocation log (RR log) for a single shipment by its "
                "reference number. Returns the order details, pipeline trace (`filters_ran` — "
                "pre-filter stages, matched filter rule name, OL/PBA stages, account lists at each "
                "step), and final ranked couriers (`preference_array` with cp_name, account_id, "
                "account_code, priority, shipping_charge, scores). Also exposes "
                "`matched_filter_rule` and `ordered_list_applied` for quick rule follow-up via "
                "`find_rule`. Primary tool for 'why was X allocated / not selected'."
            ),
            args_schema=FetchRRLogInput,
        ),
        StructuredTool.from_function(
            coroutine=fetch_rr_logs_batch,
            name="fetch_rr_logs_batch",
            description=(
                "Fetch RR logs for multiple shipment reference numbers at once. Use this to compare "
                "allocations across orders (e.g. 'why did order A get Delhivery but order B got "
                "BlueDart'). Returns one shaped log per reference number."
            ),
            args_schema=FetchRRLogsBatchInput,
        ),
    ]
