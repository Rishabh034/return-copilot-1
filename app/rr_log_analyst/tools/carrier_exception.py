from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel,Field

from app.rr_log_analyst.tools._common import as_list,shape_carrier_exception_rule
from app.rr_log_analyst.tools.context import ToolContext
from app.rr_log_analyst.tools.flow_config import EmptyInput


class FindCarrierExceptionRuleInput(BaseModel):
    query: str = Field(
        description="The exact rule id, courier_partner_id (CP_ID), or exact/partial rule name.",
    )
    match_category: Literal["RULE_ID", "COURIER_PARTNER_ID", "RULE_NAME"] = Field(
        description=(
            "How to interpret query. If the user provides an unlabeled numeric ID, do not guess: "
            "ask whether it is a rule ID or courier partner ID before calling this tool."
        ),
    )


def _rule_matches_id(rule: dict, rule_id: str) -> bool:
    return any(
        rule.get(key) is not None and str(rule[key]) == rule_id
        for key in ("id", "_id")
    )


def _rule_matches_courier_partner_id(rule: dict, courier_partner_id: str) -> bool:
    val = rule.get("courier_partner_id")
    if val is None:
        return False
    return str(val) == courier_partner_id


def _name_match_score(rule_name: str, query: str) -> int:
    """Score how well a rule name matches a query (higher = better). 0 = no match."""
    rule_name = rule_name.lower().strip()
    query = query.lower().strip()
    if not rule_name or not query:
        return 0
    if rule_name == query:
        return 100
    if query in rule_name:
        return 80
    if rule_name in query:
        return 70
    return 0


def _resolve_rules_by_query(
    rules: list[dict],
    query: str,
    match_category: Literal["RULE_ID", "COURIER_PARTNER_ID", "RULE_NAME"],
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"error": "Provide a non-empty query"}

    if match_category == "RULE_ID":
        matches = [rule for rule in rules if _rule_matches_id(rule, query)]
        if len(matches) == 1:
            return {"match_category": match_category, "rule": matches[0]}
        if len(matches) > 1:
            return {
                "match_category": match_category,
                "total_matching": len(matches),
                "rules": matches,
            }
        return {
            "match_category": match_category,
            "error": f"No Attribute Layer rule found with rule id '{query}'",
        }

    if match_category == "COURIER_PARTNER_ID":
        matches = [
            rule
            for rule in rules
            if _rule_matches_courier_partner_id(rule, query)
        ]
        matches.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
        if not matches:
            return {
                "match_category": match_category,
                "error": f"No Attribute Layer rules found for courier partner id '{query}'",
            }
        return {
            "match_category": match_category,
            "courier_partner_id": query,
            "total_matching": len(matches),
            "rules": matches,
            "hint": (
                "All Attribute Layer rules for this CP_ID, ordered by priority. "
                "Disabled rules are included — check `enabled`."
            ),
        }

    scored: list[tuple[int, dict]] = []
    for rule in rules:
        name = rule.get("rule_name")
        if isinstance(name, str):
            score = _name_match_score(name, query)
            if score:
                scored.append((score, rule))

    if not scored:
        return {
            "match_category": match_category,
            "error": f"No Attribute Layer rule name found matching '{query}'",
        }

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].get("priority") is None,
            item[1].get("priority"),
        )
    )
    top_score = scored[0][0]
    top_matches = [rule for score, rule in scored if score == top_score]
    if len(top_matches) == 1:
        return {"match_category": match_category, "rule": top_matches[0]}
    return {
        "match_category": match_category,
        "total_matching": len(top_matches),
        "rules": top_matches,
        "hint": f"Multiple Attribute Layer rules matched '{query}'; compare names and CP_IDs.",
    }


async def _fetch_carrier_exception_rules(context: ToolContext) -> list[dict]:
    raw = await context.clickpost_client.get_carrier_exception_rules(token=context.token)
    return as_list(raw)


async def _list_rules(context:ToolContext) -> dict[str,Any]:
    raw_rules = await _fetch_carrier_exception_rules(context)
    rules = [shape_carrier_exception_rule(raw_rule) for raw_rule in raw_rules]
    return {"total_rules": len(rules), "rules" : rules}



async def _find_rule(
    context: ToolContext,
    query: str,
    match_category: Literal["RULE_ID", "COURIER_PARTNER_ID", "RULE_NAME"],
) -> dict[str, Any]:
    listed = await _list_rules(context)
    return _resolve_rules_by_query(listed["rules"], query, match_category)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def list_carrier_exception_rules() -> dict[str, Any]:
        return await _list_rules(context)
    
    async def find_carrier_exception_rule(
        query: str,
        match_category: Literal["RULE_ID", "COURIER_PARTNER_ID", "RULE_NAME"],
    ) -> dict[str, Any]:
        return await _find_rule(context, query, match_category)
    
    return [
        StructuredTool.from_function(
            coroutine=list_carrier_exception_rules,
            name="list_carrier_exception_rules",
            description=(
                "PRIMARY — List all Attribute Layer (carrier exception / carrier attribute) rules "
                "for the enterprise, ordered by priority. Each rule is scoped to a courier_partner_id "
                "(CP_ID) and lists conditions plus exclusion_list (accounts eliminated when the rule "
                "matches). Use when investigating why accounts under a carrier were dropped before "
                "Filter rules, or when the user asks about Attribute Layer / carrier exception config."
            ),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            coroutine=find_carrier_exception_rule,
            name="find_carrier_exception_rule",
            description=(
                "Find Attribute Layer rules using an explicit match category: RULE_ID, "
                "COURIER_PARTNER_ID, or RULE_NAME. IMPORTANT: if the user provides an unlabeled "
                "numeric ID, ask whether it is a rule ID or courier partner ID before calling "
                "this tool. Never infer the category or try both automatically."
            ),
            args_schema=FindCarrierExceptionRuleInput,
        ),
    ]
