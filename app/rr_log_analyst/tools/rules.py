from __future__ import annotations

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import (
    as_list,
    cached_fetch,
    shape_rule,
    shape_rule_digest,
)
from app.rr_log_analyst.tools.context import ToolContext

RuleListType = Literal["FILTER", "ORDERED_LIST", "BOTH"]


class ListRulesInput(BaseModel):
    rule_type: RuleListType = Field(
        default="BOTH",
        description=(
            "Which rules to list: 'FILTER', 'ORDERED_LIST', or 'BOTH' (default). "
            "Returns full rule details (conditions, priority, account_list)."
        ),
    )


class FindRuleInput(BaseModel):
    query: str = Field(
        description=(
            "Rule id, exact rule name, or free-text from the user's question that mentions a rule name. "
            "Searches all Filter and Ordered List rules."
        ),
    )


async def _fetch_rules_by_type(context: ToolContext, rule_type: str) -> list[dict]:
    return as_list(
        await cached_fetch(
            context,
            ("rules_by_type", rule_type),
            lambda: context.clickpost_client.list_rules(auth=context.auth, rule_type=rule_type),
        )
    )


async def _list_rules(context: ToolContext, rule_type: str) -> dict[str, Any]:
    # Conditions and accounts included, value lists bounded. Raw rules ran ~20k chars
    # each (a single pincode IN can hold 1000+ values), so a full listing cost dozens
    # of Haiku calls; a bare index was cheap but forced a find_rule per rule to answer
    # "explain my rules". The digest answers that in one call — see shape_rule_digest.
    raw_rules = await _fetch_rules_by_type(context, rule_type)
    rules = [shape_rule_digest(shape_rule(raw_rule)) for raw_rule in raw_rules]
    rules.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
    return {"rule_type": rule_type, "total_rules": len(rules), "rules": rules}


async def _list_rules_unified(context: ToolContext, rule_type: RuleListType) -> dict[str, Any]:
    if rule_type != "BOTH":
        return await _list_rules(context, rule_type)

    filters = await _list_rules(context, "FILTER")
    ols = await _list_rules(context, "ORDERED_LIST")
    return {
        "rule_type": "BOTH",
        "total_rules": filters["total_rules"] + ols["total_rules"],
        "filter_rules": filters["rules"],
        "ordered_list_rules": ols["rules"],
        "filter_total": filters["total_rules"],
        "ordered_list_total": ols["total_rules"],
    }


def _rule_matches_id(rule: dict, rule_id: str) -> bool:
    for key in ("id", "_id"):
        val = rule.get(key)
        if val is not None and str(val) == rule_id:
            return True
    return False


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


def _resolve_rules_by_query(raw_rules: list[dict], query: str) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"error": "Provide a rule id or name"}

    candidates = raw_rules
    id_matches = [r for r in candidates if _rule_matches_id(r, query)]
    if len(id_matches) == 1:
        return {"rule": shape_rule(id_matches[0])}
    if len(id_matches) > 1:
        shaped = [shape_rule(r) for r in id_matches]
        return {"total_matching": len(shaped), "rules": shaped}

    scored: list[tuple[int, dict]] = []
    for raw_rule in candidates:
        name = raw_rule.get("name")
        if not isinstance(name, str):
            continue
        score = _name_match_score(name, query)
        if score:
            scored.append((score, raw_rule))

    if not scored:
        return {"error": f"No rule found matching '{query}'"}

    scored.sort(key=lambda item: (-item[0], item[1].get("priority") is None, item[1].get("priority")))
    top_score = scored[0][0]
    top_matches = [shape_rule(raw_rule) for score, raw_rule in scored if score == top_score]

    if len(top_matches) == 1:
        return {"rule": top_matches[0]}

    return {
        "total_matching": len(top_matches),
        "rules": top_matches,
        "hint": f"Multiple rules matched '{query}'; compare names and priorities.",
    }


async def _fetch_all_raw_rules(context: ToolContext) -> list[dict]:
    return as_list(
        await cached_fetch(
            context,
            ("rules_all",),
            lambda: context.clickpost_client.get_rule(auth=context.auth, rule_id=""),
        )
    )


async def _find_rule(context: ToolContext, query: str) -> dict[str, Any]:
    return _resolve_rules_by_query(await _fetch_all_raw_rules(context), query)


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def list_rules(rule_type: RuleListType = "BOTH") -> dict[str, Any]:
        return await _list_rules_unified(context, rule_type)

    async def find_rule(query: str) -> dict[str, Any]:
        return await _find_rule(context, query)

    return [
        StructuredTool.from_function(
            coroutine=list_rules,
            name="list_rules",
            description=(
                "PRIMARY — List Filter and/or Ordered List rules in priority order with their "
                "conditions: each condition's key and operator, plus account_list, priority and "
                "enabled state. List-operator values are summarized (value_count, value_samples, "
                "value_prefixes) rather than enumerated, which is enough to explain what a rule "
                "covers. Pass rule_type='FILTER', 'ORDERED_LIST', or 'BOTH' (default). Use "
                "find_rule only when you need one rule's complete raw value list."
            ),
            args_schema=ListRulesInput,
        ),
        StructuredTool.from_function(
            coroutine=find_rule,
            name="find_rule",
            description=(
                "Find a Filter or Ordered List rule by id or by matching its name against the user's "
                "query text. Loads all rules, matches by id first, then by exact name, substring, or "
                "rule name appearing inside the query. Returns the full rule definition (conditions, "
                "priority, account_list) for a unique match, or all matching rules with full details "
                "when ambiguous. Prefer this when the user mentions a rule by name in their question."
            ),
            args_schema=FindRuleInput,
        ),
    ]
