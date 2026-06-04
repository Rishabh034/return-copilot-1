from __future__ import annotations

import asyncio
from typing import Any

from app.rr_log_analyst.tools import ToolContext, register


def _shape_rr_log(reference_number: str, raw: Any) -> dict:
    """Normalize a raw RR-log API response into the fields the analyst reasons over.

    The engine returns (among other things) `filters_ran` — the pipeline trace —
    and `preference_array` — the final ranked couriers. We surface those directly
    plus counts so the orchestrator can summarize the result cheaply. Anything we
    don't recognize is preserved under `raw` so no information is lost.
    """
    if not isinstance(raw, dict):
        return {"reference_number": reference_number, "raw": raw}

    # The request_logs endpoint wraps things as
    #   {"meta": {...}, "result": {"clickpost_response": {"result": [ {...} ]}}}
    # where that innermost object holds filters_ran / preference_array. Dig down
    # to it; fall back to shallower wrappers for other response shapes.
    body = raw
    result = raw.get("result")
    if isinstance(result, dict):
        cp_response = result.get("clickpost_response")
        if isinstance(cp_response, dict):
            inner_list = cp_response.get("result")
            if isinstance(inner_list, list) and inner_list and isinstance(inner_list[0], dict):
                body = inner_list[0]

    if body is raw:
        for wrapper in ("result", "data", "log"):
            inner = raw.get(wrapper)
            if isinstance(inner, dict) and ("filters_ran" in inner or "preference_array" in inner):
                body = inner
                break

    filters_ran = body.get("filters_ran") or []
    preference_array = body.get("preference_array") or []

    shaped: dict[str, Any] = {
        "reference_number": reference_number,
        "filters_ran": filters_ran,
        "preference_array": preference_array,
        "total_filters": len(filters_ran),
        "total_ranked": len(preference_array),
    }
    # Keep any other top-level metadata the engine returned (order attributes, etc.).
    extras = {k: v for k, v in body.items() if k not in ("filters_ran", "preference_array")}
    if extras:
        shaped["metadata"] = extras
    return shaped


@register
class FetchRRLogTool:
    name = "fetch_rr_log"
    description = (
        "Fetch the recommendation/allocation log (RR log) for a single shipment by its "
        "reference number. Returns the full pipeline trace (`filters_ran` — each filter, "
        "accounts before/after, dropped accounts and reasons) and the final ranked couriers "
        "(`preference_array` with cp_name, account_id, account_code, priority, scores, SLA). "
        "This is the primary tool for answering 'why was X allocated / not selected'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "reference_number": {
                "type": "string",
                "description": "The shipment/order reference number to look up.",
            }
        },
        "required": ["reference_number"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        reference_number = (args.get("reference_number") or "").strip()
        if not reference_number:
            return {"error": "reference_number is required"}

        raw = await context.clickpost_client.get_rr_log(
            token=context.token,
            reference_number=reference_number,
        )
        return _shape_rr_log(reference_number, raw)


@register
class FetchRRLogsBatchTool:
    name = "fetch_rr_logs_batch"
    description = (
        "Fetch RR logs for multiple shipment reference numbers at once. Use this to compare "
        "allocations across orders (e.g. 'why did order A get Delhivery but order B got "
        "BlueDart'). Returns one shaped log per reference number."
    )
    parameters = {
        "type": "object",
        "properties": {
            "reference_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of shipment reference numbers to fetch.",
                "minItems": 1,
                "maxItems": 20,
            }
        },
        "required": ["reference_numbers"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        refs = [str(r).strip() for r in (args.get("reference_numbers") or []) if str(r).strip()]
        if not refs:
            return {"error": "reference_numbers must contain at least one value"}

        async def _one(ref: str) -> dict:
            try:
                raw = await context.clickpost_client.get_rr_log(
                    token=context.token,
                    reference_number=ref,
                )
                return _shape_rr_log(ref, raw)
            except Exception as e:  # one bad ref shouldn't sink the batch
                return {"reference_number": ref, "error": str(e)}

        logs = await asyncio.gather(*[_one(r) for r in refs])
        return {"count": len(logs), "logs": list(logs)}
