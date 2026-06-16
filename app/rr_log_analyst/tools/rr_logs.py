from __future__ import annotations

import asyncio
from typing import Any, Literal

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
from app.rr_log_analyst.tools.context import ToolContext

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


def _extract_allocation_from_envelope(raw: dict) -> tuple[dict[str, Any] | None, dict | None]:
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

    async def fetch_rr_logs_batch(reference_numbers: list[str]) -> dict[str, Any]:
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
            coroutine=fetch_rr_log,
            name="fetch_rr_log",
            description=(
                "PRIMARY — Full RR log for one shipment: order, filters_ran pipeline trace, and "
                "complete preference_array. Use first for allocation investigations. If too large, "
                "use summarize_rr_log or the get_rr_log_* sub-tools."
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
