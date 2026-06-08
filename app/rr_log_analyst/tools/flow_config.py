from __future__ import annotations

from typing import Any

from app.rr_log_analyst.tools import ToolContext, register

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
        parts.append("PBA / recs terminal layer is disabled — when no OL matches, allocation comes from the matched filter's account_list order (or upstream pool).")

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


def _shape_flow_config(raw: Any) -> dict[str, Any]:
    config = _unwrap_result(raw)
    if not config:
        return {"error": "Unexpected flow-config response shape", "raw": raw}

    shaped = {field: config.get(field) for field in _CONFIG_FIELDS}
    shaped["flow_summary"] = _derive_flow_summary(config)
    return shaped


@register
class GetEnterpriseFlowConfigTool:
    name = "get_enterprise_flow_config"
    description = (
        "Fetch the enterprise's Recommendation Flow Config (EnterpriseRecommendationFlowConfig). "
        "Returns boolean toggles that define which pipeline stages this enterprise uses: Filter rules, "
        "Ordered Lists, PBA/recs, ODA pre-filter, load distribution, rule iteration, recency bias, "
        "intelligent feedback, and whether the legacy or RecsV2 flow is active. "
        "Call this early when explaining allocation behaviour — it tells you which layers can run "
        "and how to interpret an RR log (e.g. no OL stage if ordered_list=false, no PBA if recs=false, "
        "whether empty filter results trigger iteration). Does not require a reference number."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        raw = await context.clickpost_client.get_rr_config(token=context.token)
        return _shape_flow_config(raw)
