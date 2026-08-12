from __future__ import annotations

from typing import Any, Literal, Self

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator

from app.rr_log_analyst.tools._common import as_list, shape_carrier_exception_rule
from app.rr_log_analyst.tools.context import ToolContext

MatchCategory = Literal["RULE_ID", "COURIER_PARTNER_ID", "RULE_NAME"]


class ListCarrierExceptionRulesInput(BaseModel):
    query: str | None = Field(
        default=None,
        description=(
            "Optional. Omit to list all Attribute Layer rules. When set, also pass "
            "match_category to find by rule id, courier_partner_id (CP_ID), or rule name."
        ),
    )
    match_category: MatchCategory | None = Field(
        default=None,
        description=(
            "Required when query is set. How to interpret query: RULE_ID, COURIER_PARTNER_ID, "
            "or RULE_NAME. If the user provides an unlabeled numeric ID, do not guess — ask "
            "whether it is a rule ID or courier partner ID before calling."
        ),
    )

    @model_validator(mode="after")
    def _query_and_category_together(self) -> Self:
        has_query = bool(self.query and self.query.strip())
        if has_query and self.match_category is None:
            raise ValueError("match_category is required when query is provided")
        if self.match_category is not None and not has_query:
            raise ValueError("query is required when match_category is provided")
        return self


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
    match_category: MatchCategory,
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


async def _list_rules(context: ToolContext) -> dict[str, Any]:
    raw_rules = await _fetch_carrier_exception_rules(context)
    rules = [shape_carrier_exception_rule(raw_rule) for raw_rule in raw_rules]
    rules.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
    return {"total_rules": len(rules), "rules": rules}


async def _list_or_find(
    context: ToolContext,
    query: str | None,
    match_category: MatchCategory | None,
) -> dict[str, Any]:
    listed = await _list_rules(context)
    if not query or not match_category:
        return listed
    return _resolve_rules_by_query(listed["rules"], query, match_category)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def list_carrier_exception_rules(
        query: str | None = None,
        match_category: MatchCategory | None = None,
    ) -> dict[str, Any]:
        return await _list_or_find(context, query, match_category)

    return [
        StructuredTool.from_function(
            coroutine=list_carrier_exception_rules,
            name="list_carrier_exception_rules",
            description=(
                "PRIMARY — Attribute Layer (carrier exception / carrier attribute) rules. "
                "Omit query to list all rules for the enterprise (priority order). Pass query + "
                "match_category (RULE_ID, COURIER_PARTNER_ID, or RULE_NAME) to find specific "
                "rules. Each rule is scoped to a courier_partner_id (CP_ID) with conditions and "
                "exclusion_list. If the user gives an unlabeled numeric ID, ask whether it is a "
                "rule ID or CP_ID before calling — never infer the category."
            ),
            args_schema=ListCarrierExceptionRulesInput,
        ),
    ]
