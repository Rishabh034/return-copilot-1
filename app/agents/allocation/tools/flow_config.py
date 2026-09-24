from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.tool_context import ToolContext


class EmptyInput(BaseModel):
    pass

_CONFIG_FIELDS = (
    "id",
    "enabled",
    "old_flow",
    "filters",
    "ordered_list",
    "recs",
    "oda_filter",
    "reverse_wnf",
    "load_distribution",
    "load_distribution_reverse",
    "load_distribution_filter",
    "recency_bias",
    "intelligent_feedback",
    "iterate_through_rules_filters",
    "iterate_through_rules_ordered_list",
)

FlowConfigFlag = Literal[
    "enabled",
    "old_flow",
    "filters",
    "ordered_list",
    "recs",
    "oda_filter",
    "reverse_wnf",
    "load_distribution",
    "load_distribution_reverse",
    "load_distribution_filter",
    "recency_bias",
    "intelligent_feedback",
    "iterate_through_rules_filters",
    "iterate_through_rules_ordered_list",
]

_FLAG_HINTS: dict[str, str] = {
    "enabled": "Master switch — recommendation flow active for this enterprise.",
    "old_flow": "Legacy allocation flow instead of RecsV2.",
    "filters": "Filter rules eligibility stage.",
    "ordered_list": "Ordered List rules stage.",
    "recs": "PBA / performance-based allocation terminal layer.",
    "oda_filter": "ODA pinched-accounts pre-filter.",
    "reverse_wnf": "Reverse WNF handling.",
    "load_distribution": "Forward terminal load distribution.",
    "load_distribution_reverse": "Reverse (RVP) terminal load distribution.",
    "load_distribution_filter": "Per-filter lane load distribution.",
    "recency_bias": "Recency bias ranking.",
    "intelligent_feedback": "Intelligent feedback / performance allocation.",
    "iterate_through_rules_filters": "Iterate to next filter rule on empty result.",
    "iterate_through_rules_ordered_list": "Iterate to next OL rule on empty result.",
}


class GetFlowConfigFlagInput(BaseModel):
    flag: FlowConfigFlag = Field(
        description=(
            "Single flow-config toggle to look up, e.g. 'load_distribution', 'ordered_list', 'recs'. "
            "Use when the user asks about one specific setting."
        ),
    )


def _unwrap_result(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    result = raw.get("result")
    if isinstance(result, dict):
        return result
    return raw if raw else None


def _derive_flow_summary(config: dict) -> str:
    if not config.get("enabled", True):
        return "Recommendation flow is disabled for this enterprise."

    parts: list[str] = []
    if config.get("old_flow"):
        parts.append("Uses the legacy (old) allocation flow, not RecsV2.")
    else:
        parts.append("Uses RecsV2 (modern recommendation flow).")

    if config.get("filters"):
        parts.append("Filter rules stage is enabled.")
    else:
        parts.append("Filter rules stage is disabled.")

    if config.get("ordered_list"):
        parts.append("Ordered List rules stage is enabled.")
    else:
        parts.append("Ordered List rules stage is disabled.")

    if config.get("recs"):
        parts.append("PBA / performance-based allocation (recs) is enabled as the terminal layer when no OL matches.")
    else:
        parts.append(
            "PBA / recs terminal layer is disabled — when no OL matches, allocation comes from "
            "the matched filter's account_list order (or upstream pool)."
        )

    if config.get("iterate_through_rules_filters"):
        parts.append("Rule iteration is ON for Filter rules (empty result moves to next filter rule).")
    if config.get("iterate_through_rules_ordered_list"):
        parts.append("Rule iteration is ON for Ordered List rules.")

    if config.get("oda_filter"):
        parts.append("ODA pinched-accounts pre-filter is enabled.")
    if config.get("load_distribution_filter"):
        parts.append("Load distribution at the Filter stage is enabled.")
    if config.get("load_distribution"):
        parts.append("Load distribution (forward) is enabled.")
    if config.get("load_distribution_reverse"):
        parts.append("Load distribution (reverse) is enabled.")
    if config.get("recency_bias"):
        parts.append("Recency bias ranking is enabled.")
    if config.get("intelligent_feedback"):
        parts.append("Intelligent feedback is enabled.")

    return " ".join(parts)


async def _fetch_flow_config(context: ToolContext) -> dict[str, Any]:
    raw = await context.clickpost_client.get_rr_config(auth=context.auth)
    config = _unwrap_result(raw)
    if not config:
        return {"error": "Unexpected flow-config response shape", "raw": raw}
    return config


def _shape_flow_config(config: dict) -> dict[str, Any]:
    shaped = {field: config.get(field) for field in _CONFIG_FIELDS}
    shaped["flow_summary"] = _derive_flow_summary(config)
    return shaped


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def get_enterprise_flow_config() -> dict[str, Any]:
        config = await _fetch_flow_config(context)
        if "error" in config:
            return config
        return _shape_flow_config(config)

    async def summarize_flow_config() -> dict[str, Any]:
        config = await _fetch_flow_config(context)
        if "error" in config:
            return config
        flags = {field: config.get(field) for field in _CONFIG_FIELDS if field != "id"}
        return {
            "flow_summary": _derive_flow_summary(config),
            "flags": flags,
            "hint": "Use get_flow_config_flag(flag) to look up one specific toggle.",
        }

    async def get_flow_config_flag(flag: FlowConfigFlag) -> dict[str, Any]:
        config = await _fetch_flow_config(context)
        if "error" in config:
            return config
        return {
            "flag": flag,
            "value": config.get(flag),
            "description": _FLAG_HINTS.get(flag, ""),
            "flow_summary": _derive_flow_summary(config),
        }

    return [
        StructuredTool.from_function(
            coroutine=get_enterprise_flow_config,
            name="get_enterprise_flow_config",
            description=(
                "PRIMARY — Full enterprise Recommendation Flow Config: all boolean toggles (filters, "
                "ordered_list, recs, load_distribution*, rule iteration, etc.) plus flow_summary. "
                "Call early when explaining pipeline behaviour."
            ),
            args_schema=EmptyInput,
        )
    ]
