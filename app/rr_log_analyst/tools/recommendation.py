from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import shape_allocation_result
from app.rr_log_analyst.tools.context import ToolContext


class OrderInput(BaseModel):
    pickup_pincode: str
    drop_pincode: str
    delivery_type: Literal["FORWARD", "RVP"]
    order_type: str | None = Field(default=None, description="e.g. PREPAID, COD")
    reference_number: str | None = None
    item: str | None = None
    invoice_value: float | None = None
    weight: float | None = Field(default=None, description="Weight in grams.")
    length: float | None = None
    height: float | None = None
    breadth: float | None = None
    item_count: int | None = None
    additional: dict[str, Any] | None = Field(
        default=None,
        description="Extra fields such as custom_fields.",
    )
    higher_of_dry_weight_and_volumetric_weight: float | None = None
    shipment_created_datetime_utc: str | None = Field(
        default=None,
        description="UTC datetime string, e.g. 2026-06-05 07:19:15",
    )


class RunRecommendationInput(BaseModel):
    order: OrderInput = Field(
        description=(
            "Order details to run the recommendation for. Include all fields "
            "relevant to allocation; at minimum pickup_pincode, drop_pincode, "
            "and delivery_type."
        )
    )


def _shape_recommendation_response(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"error": "Unexpected response shape", "raw": raw}

    results = raw.get("result")
    if not isinstance(results, list) or not results:
        return {"error": "Empty or unexpected result", "raw": raw}

    shaped_results: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        shaped_results.append(shape_allocation_result(item))

    return {"count": len(shaped_results), "results": shaped_results}


async def _run_recommendation(context: ToolContext, order: OrderInput) -> dict[str, Any]:
    order_dict = order.model_dump(exclude_none=True)
    raw = await context.clickpost_client.run_recommendation(
        token=context.token,
        orders=[order_dict],
    )
    return _shape_recommendation_response(raw)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def run_recommendation(order: OrderInput) -> dict[str, Any]:
        return await _run_recommendation(context, order)

    return [
        StructuredTool.from_function(
            coroutine=run_recommendation,
            name="run_recommendation",
            description=(
                "Re-run the recommendation engine for an order to see which courier would be "
                "allocated under the current rules. Use this after a rule change to verify the "
                "new allocation result. Requires order details "
                "(pickup/drop pincodes, weight, dimensions, delivery type, etc.). "
                "Returns the full pipeline trace (filters_ran) and ranked couriers (preference_array), "
                "identical in structure to an RR log."
            ),
            args_schema=RunRecommendationInput,
        ),
    ]
