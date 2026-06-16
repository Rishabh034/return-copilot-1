from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import FILTERS_RAN_KEYS, shape_allocation_result
from app.rr_log_analyst.tools.allocation_views import (
    compact_allocation_summary,
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
            "relevant to allocation; at minimum pickup_pincode, drop_pincode, and delivery_type."
        )
    )


class SummarizeRecommendationInput(BaseModel):
    order: OrderInput = Field(description="Order details to simulate allocation for.")


class RecommendationPipelineInput(BaseModel):
    order: OrderInput
    stage: PipelineStage = Field(description="Pipeline stage key to inspect.")
    account_id: int | None = Field(default=None)
    cp_name: str = Field(default="")


class RecommendationRankingInput(BaseModel):
    order: OrderInput
    account_id: int | None = Field(default=None)
    cp_name: str = Field(default="")
    limit: int = Field(default=5, ge=1, le=20)


class RecommendationCourierSearchInput(BaseModel):
    order: OrderInput
    account_id: int | None = Field(default=None)
    cp_name: str = Field(default="")


async def _run_recommendation_raw(context: ToolContext, order: OrderInput) -> dict[str, Any]:
    order_dict = order.model_dump(exclude_none=True)
    raw = await context.clickpost_client.run_recommendation(
        token=context.token,
        orders=[order_dict],
    )
    if not isinstance(raw, dict):
        return {"error": "Unexpected response shape", "raw": raw}

    results = raw.get("result")
    if not isinstance(results, list) or not results:
        return {"error": "Empty or unexpected result", "raw": raw}

    first = results[0]
    if not isinstance(first, dict):
        return {"error": "Unexpected result item", "raw": raw}

    return shape_allocation_result(first)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def run_recommendation(order: OrderInput) -> dict[str, Any]:
        return await _run_recommendation_raw(context, order)

    async def summarize_recommendation(order: OrderInput) -> dict[str, Any]:
        result = await _run_recommendation_raw(context, order)
        return compact_allocation_summary(result)

    async def get_recommendation_pipeline(
        order: OrderInput,
        stage: PipelineStage,
        account_id: int | None = None,
        cp_name: str = "",
    ) -> dict[str, Any]:
        result = await _run_recommendation_raw(context, order)
        return extract_pipeline(result, stage, account_id=account_id, cp_name=cp_name)

    async def get_recommendation_ranking(
        order: OrderInput,
        account_id: int | None = None,
        cp_name: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        result = await _run_recommendation_raw(context, order)
        return extract_ranking(result, account_id=account_id, cp_name=cp_name, limit=limit)

    async def search_recommendation_courier(
        order: OrderInput,
        account_id: int | None = None,
        cp_name: str = "",
    ) -> dict[str, Any]:
        result = await _run_recommendation_raw(context, order)
        return search_courier_in_allocation(result, account_id=account_id, cp_name=cp_name)

    _stages_hint = ", ".join(FILTERS_RAN_KEYS)

    return [
        StructuredTool.from_function(
            coroutine=run_recommendation,
            name="run_recommendation",
            description=(
                "PRIMARY — Re-run the recommendation engine for an order. Returns full pipeline trace "
                "(filters_ran) and complete preference_array, same shape as fetch_rr_log. If too large, "
                "use summarize_recommendation or get_recommendation_* sub-tools."
            ),
            args_schema=RunRecommendationInput,
        ),
        StructuredTool.from_function(
            coroutine=summarize_recommendation,
            name="summarize_recommendation",
            description="SUB-TOOL — Compact simulation result when run_recommendation is too large.",
            args_schema=SummarizeRecommendationInput,
        ),
        StructuredTool.from_function(
            coroutine=get_recommendation_pipeline,
            name="get_recommendation_pipeline",
            description=f"SUB-TOOL — One pipeline stage from a simulation. Stages: {_stages_hint}.",
            args_schema=RecommendationPipelineInput,
        ),
        StructuredTool.from_function(
            coroutine=get_recommendation_ranking,
            name="get_recommendation_ranking",
            description="SUB-TOOL — Final ranking from simulation; filter by account_id/cp_name or top N.",
            args_schema=RecommendationRankingInput,
        ),
        StructuredTool.from_function(
            coroutine=search_recommendation_courier,
            name="search_recommendation_courier",
            description="SUB-TOOL — Trace one courier through a simulation. Requires account_id or cp_name.",
            args_schema=RecommendationCourierSearchInput,
        ),
    ]
