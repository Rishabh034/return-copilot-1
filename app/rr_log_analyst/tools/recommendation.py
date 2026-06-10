from __future__ import annotations

from typing import Any

from app.rr_log_analyst.tools import ToolContext, register


def _shape_recommendation_response(raw: Any) -> dict[str, Any]:
    """Normalize the recommendation API response into the fields the analyst
    reasons over — same structure as an RR log so analysis is consistent."""
    if not isinstance(raw, dict):
        return {"error": "Unexpected response shape", "raw": raw}

    results = raw.get("result")
    if not isinstance(results, list) or not results:
        return {"error": "Empty or unexpected result", "raw": raw}

    shaped_results: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        filters_ran = item.get("filters_ran") or {}
        preference_array = item.get("preference_array") or []

        shaped: dict[str, Any] = {
            "reference_number": item.get("reference_number"),
            "username": item.get("username"),
            "pincode_serviceable": item.get("pincode_serviceable"),
            "filters_ran": filters_ran,
            "preference_array": preference_array,
            "total_ranked": len(preference_array),
        }
        shaped_results.append(shaped)

    return {"count": len(shaped_results), "results": shaped_results}


@register
class RunRecommendationTool:
    name = "run_recommendation"
    description = (
        "Re-run the recommendation engine for an order to see which courier would be "
        "allocated under the current rules. Use this after a rule change to verify the "
        "new allocation result. Requires order details "
        "(pickup/drop pincodes, weight, dimensions, delivery type, etc.). "
        "Returns the full pipeline trace (filters_ran) and ranked couriers (preference_array), "
        "identical in structure to an RR log."
    )
    parameters = {
        "type": "object",
        "properties": {
            "order": {
                "type": "object",
                "description": (
                    "Order details to run the recommendation for. Include all fields "
                    "relevant to allocation; at minimum pickup_pincode, drop_pincode, "
                    "and delivery_type."
                ),
                "properties": {
                    "pickup_pincode": {"type": "string"},
                    "drop_pincode": {"type": "string"},
                    "delivery_type": {
                        "type": "string",
                        "enum": ["FORWARD", "RVP"],
                    },
                    "order_type": {
                        "type": "string",
                        "description": "e.g. PREPAID, COD",
                    },
                    "reference_number": {"type": "string"},
                    "item": {"type": "string"},
                    "invoice_value": {"type": "number"},
                    "weight": {
                        "type": "number",
                        "description": "Weight in grams.",
                    },
                    "length": {"type": "number"},
                    "height": {"type": "number"},
                    "breadth": {"type": "number"},
                    "item_count": {"type": "integer"},
                    "additional": {
                        "type": "object",
                        "description": "Extra fields such as custom_fields.",
                    },
                    "higher_of_dry_weight_and_volumetric_weight": {
                        "type": "number",
                    },
                    "shipment_created_datetime_utc": {
                        "type": "string",
                        "description": "UTC datetime string, e.g. 2026-06-05 07:19:15",
                    },
                },
                "required": ["pickup_pincode", "drop_pincode", "delivery_type"],
            },
        },
        "required": ["order"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        order = args.get("order")
        if not isinstance(order, dict):
            return {"error": "order must be an object with at least pickup_pincode, drop_pincode, delivery_type"}

        for field in ("pickup_pincode", "drop_pincode", "delivery_type"):
            if not order.get(field):
                return {"error": f"order.{field} is required"}

        raw = await context.clickpost_client.run_recommendation(
            token=context.token,
            orders=[order],
        )
        return _shape_recommendation_response(raw)
