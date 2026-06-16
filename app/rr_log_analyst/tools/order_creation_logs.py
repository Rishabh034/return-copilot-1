from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import shape_preference_entry
from app.rr_log_analyst.tools.context import ToolContext


class OrderCreationLogInput(BaseModel):
    reference_number: str = Field(description="The shipment/order reference number to look up.")


def _shape_order_creation_entry(entry: dict) -> dict[str, Any]:
    """Shape one result entry from the order-creation request_logs response."""
    shaped: dict[str, Any] = {}

    user_request = entry.get("user_request")
    if isinstance(user_request, list) and user_request:
        shaped["user_request"] = user_request[0] if len(user_request) == 1 else user_request
    elif isinstance(user_request, dict):
        shaped["user_request"] = user_request

    cp_request = entry.get("courier_partner_request")
    if cp_request and cp_request != "{}":
        shaped["courier_partner_request"] = cp_request

    cp_response = entry.get("courier_partner_response")
    if cp_response and cp_response != "{}":
        shaped["courier_partner_response"] = cp_response

    clickpost_response = entry.get("clickpost_response")
    if isinstance(clickpost_response, dict):
        meta = clickpost_response.get("meta")
        result = clickpost_response.get("result")
        shaped["clickpost_response_meta"] = meta
        if isinstance(result, list) and result:
            shaped["clickpost_response_result"] = result
        elif isinstance(result, dict):
            shaped["clickpost_response_result"] = result

    timestamps = entry.get("order_timestamp")
    if isinstance(timestamps, dict):
        non_null = {k: v for k, v in timestamps.items() if v is not None}
        if non_null:
            shaped["timestamps"] = non_null

    return shaped


def _shape_order_creation_log(reference_number: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"reference_number": reference_number, "error": "Unexpected response shape"}

    result = raw.get("result")
    if not isinstance(result, list) or not result:
        return {"reference_number": reference_number, "error": "No order creation log data found"}

    entries = []
    for entry in result:
        if not isinstance(entry, dict):
            continue
        entries.append(_shape_order_creation_entry(entry))

    if not entries:
        return {"reference_number": reference_number, "error": "No order creation log data found"}

    return {
        "reference_number": reference_number,
        "entry_count": len(entries),
        "entries": entries,
    }


async def _load_order_creation_log(context: ToolContext, reference_number: str) -> dict[str, Any]:
    reference_number = reference_number.strip()
    if not reference_number:
        return {"error": "reference_number is required"}

    raw = await context.clickpost_client.get_order_creation_log(
        token=context.token,
        reference_number=reference_number,
    )
    return _shape_order_creation_log(reference_number, raw)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def fetch_order_creation_log(reference_number: str) -> dict[str, Any]:
        return await _load_order_creation_log(context, reference_number)

    return [
        StructuredTool.from_function(
            coroutine=fetch_order_creation_log,
            name="fetch_order_creation_log",
            description=(
                "Fetch the order creation request/response log for a shipment by reference_number. "
                "Returns the Clickpost request sent to the courier partner, the courier partner's "
                "response, and timestamps. Use this to debug order creation failures, see what "
                "payload was sent to the CP, or check the CP's response. "
                "Note: logs are retained for roughly 7 days."
            ),
            args_schema=OrderCreationLogInput,
        ),
    ]
