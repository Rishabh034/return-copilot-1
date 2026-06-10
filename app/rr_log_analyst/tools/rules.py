from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import as_list, shape_rule
from app.rr_log_analyst.tools.context import ToolContext
from app.rr_log_analyst.tools.flow_config import EmptyInput

_PINCODE_KEYS = {"pickup_pincode", "drop_pincode"}


class GetFilterRuleInput(BaseModel):
    rule_id: str = Field(
        description="The rule id, exact rule name, or text from the user question containing a rule name."
    )


class FindRuleInput(BaseModel):
    query: str = Field(
        description=(
            "Rule id, exact rule name, or free-text from the user's question that mentions a rule name. "
            "Searches all Filter and Ordered List rules."
        ),
    )


class FindRulesForPincodeInput(BaseModel):
    pickup_pincode: str = Field(
        default="",
        description="Pickup pincode to match against rule conditions (optional).",
    )
    drop_pincode: str = Field(
        default="",
        description="Drop pincode to match against rule conditions (optional).",
    )


class FindRulesForCarrierInput(BaseModel):
    account_id: int = Field(description="The carrier account id to search for in rule account_lists.")


class GetOrderedListRuleInput(BaseModel):
    name: str = Field(
        description="The OL rule id, exact name, or user query text containing the rule name."
    )


def _condition_pincodes(condition: dict) -> list[str]:
    value = condition.get("value")
    if not isinstance(value, str):
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


async def _list_filter_rules(context: ToolContext) -> dict[str, Any]:
    raw = await context.clickpost_client.list_rules(token=context.token, rule_type="FILTER")
    rules = [shape_rule(r) for r in as_list(raw)]
    rules.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
    return {"total_rules": len(rules), "rules": rules}


async def _list_ordered_list_rules(context: ToolContext) -> dict[str, Any]:
    raw = await context.clickpost_client.list_rules(token=context.token, rule_type="ORDERED_LIST")
    rules = [shape_rule(r) for r in as_list(raw)]
    rules.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
    return {"total_rules": len(rules), "rules": rules}


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


def _resolve_rules_by_query(
    raw_rules: list[dict],
    query: str,
    *,
    rule_type: str | None = None,
    rule_type_label: str = "rule",
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {"error": "Provide a rule id or name"}

    candidates = raw_rules
    if rule_type:
        candidates = [r for r in raw_rules if r.get("rule_type") == rule_type]

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
        return {"error": f"No {rule_type_label} found matching '{query}'"}

    scored.sort(key=lambda item: (-item[0], item[1].get("priority") is None, item[1].get("priority")))
    top_score = scored[0][0]
    top_matches = [shape_rule(raw_rule) for score, raw_rule in scored if score == top_score]

    if len(top_matches) == 1:
        return {"rule": top_matches[0]}

    return {
        "total_matching": len(top_matches),
        "rules": top_matches,
        "hint": f"Multiple {rule_type_label}s matched '{query}'; compare names and priorities.",
    }


async def _fetch_all_raw_rules(context: ToolContext) -> list[dict]:
    raw = await context.clickpost_client.get_rule(token=context.token, rule_id="")
    return as_list(raw)


async def _find_rule(context: ToolContext, query: str) -> dict[str, Any]:
    return _resolve_rules_by_query(await _fetch_all_raw_rules(context), query)


async def _get_filter_rule(context: ToolContext, rule_id: str) -> dict[str, Any]:
    return _resolve_rules_by_query(await _fetch_all_raw_rules(context), rule_id)


async def _find_rules_for_pincode(
    context: ToolContext,
    pickup_pincode: str = "",
    drop_pincode: str = "",
) -> dict[str, Any]:
    pickup = pickup_pincode.strip()
    drop = drop_pincode.strip()
    if not pickup and not drop:
        return {"error": "Provide at least one of pickup_pincode or drop_pincode"}

    wanted: dict[str, str] = {}
    if pickup:
        wanted["pickup_pincode"] = pickup
    if drop:
        wanted["drop_pincode"] = drop

    filters = as_list(await context.clickpost_client.list_rules(token=context.token, rule_type="FILTER"))
    ols = as_list(await context.clickpost_client.list_rules(token=context.token, rule_type="ORDERED_LIST"))

    matching: list[dict] = []
    for raw_rule in [*filters, *ols]:
        rule = shape_rule(raw_rule)
        matched_on: list[dict] = []
        for cond in rule["rule"]:
            key = cond.get("key")
            if key not in _PINCODE_KEYS or key not in wanted:
                continue
            pincodes = _condition_pincodes(cond)
            target = wanted[key]
            op = (cond.get("op") or "").upper()
            hit = target in pincodes or (op == "CONTAINS" and any(target in p for p in pincodes))
            if hit:
                matched_on.append({"key": key, "op": cond.get("op"), "pincode": target})
        if matched_on:
            matching.append({**rule, "matched_on": matched_on})

    return {"total_matching": len(matching), "rules": matching}


async def _get_ordered_list_rule_by_name(context: ToolContext, name: str) -> dict[str, Any]:
    raw = await context.clickpost_client.list_rules(token=context.token, rule_type="ORDERED_LIST")
    return _resolve_rules_by_query(
        as_list(raw),
        name,
        rule_type="ORDERED_LIST",
        rule_type_label="Ordered List rule",
    )


async def _find_rules_for_carrier(context: ToolContext, account_id: int) -> dict[str, Any]:
    filters = as_list(await context.clickpost_client.list_rules(token=context.token, rule_type="FILTER"))
    ols = as_list(await context.clickpost_client.list_rules(token=context.token, rule_type="ORDERED_LIST"))

    matching: list[dict] = []
    for raw_rule in [*filters, *ols]:
        rule = shape_rule(raw_rule)
        account_list = rule["account_list"]
        if account_id in account_list:
            position = account_list.index(account_id)
            matching.append({**rule, "carrier_position": position})

    return {"total_matching": len(matching), "rules": matching}


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def list_filter_rules() -> dict[str, Any]:
        return await _list_filter_rules(context)

    async def list_ordered_list_rules() -> dict[str, Any]:
        return await _list_ordered_list_rules(context)

    async def get_filter_rule(rule_id: str) -> dict[str, Any]:
        return await _get_filter_rule(context, rule_id)

    async def find_rules_for_pincode(
        pickup_pincode: str = "",
        drop_pincode: str = "",
    ) -> dict[str, Any]:
        return await _find_rules_for_pincode(context, pickup_pincode, drop_pincode)

    async def find_rules_for_carrier(account_id: int) -> dict[str, Any]:
        return await _find_rules_for_carrier(context, account_id)

    async def get_ordered_list_rule(name: str) -> dict[str, Any]:
        return await _get_ordered_list_rule_by_name(context, name)

    async def find_rule(query: str) -> dict[str, Any]:
        return await _find_rule(context, query)

    return [
        StructuredTool.from_function(
            coroutine=list_filter_rules,
            name="list_filter_rules",
            description=(
                "List all Filter rules configured for the enterprise, in priority order. Filter rules "
                "are the eligibility gate (AND conditions; first match wins) — each yields a pool of "
                "eligible carrier accounts. Use to get an overview or to locate a rule by name/priority."
            ),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            coroutine=list_ordered_list_rules,
            name="list_ordered_list_rules",
            description=(
                "List all Ordered List (OL) rules configured for the enterprise, in priority order. OL "
                "rules define carrier priority order for a specific route; final allocation is the "
                "intersection of the OL's account_list with the matched filter's eligible pool, in OL order."
            ),
            args_schema=EmptyInput,
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
        StructuredTool.from_function(
            coroutine=get_ordered_list_rule,
            name="get_ordered_list_rule",
            description=(
                "Fetch an Ordered List (OL) rule by id or name. Loads all OL rules and matches by id, "
                "exact name, substring, or rule name found in the user's query text. Returns full rule "
                "details — conditions, priority, and account_list."
            ),
            args_schema=GetOrderedListRuleInput,
        ),
        StructuredTool.from_function(
            coroutine=get_filter_rule,
            name="get_filter_rule",
            description=(
                "Fetch a Filter or Ordered List rule by id or name. Same matching as find_rule but kept "
                "for RR-log follow-ups. Returns full definition — conditions, operators, priority and "
                "account_list — for a unique match, or all matching rules with details when ambiguous."
            ),
            args_schema=GetFilterRuleInput,
        ),
        StructuredTool.from_function(
            coroutine=find_rules_for_pincode,
            name="find_rules_for_pincode",
            description=(
                "Find all rules (Filter and Ordered List) whose conditions reference a given pickup "
                "and/or drop pincode. Use to answer 'what rules affect this route/pincode'. Matches "
                "pincode conditions (pickup_pincode / drop_pincode with IN, EQ or CONTAINS operators)."
            ),
            args_schema=FindRulesForPincodeInput,
        ),
        StructuredTool.from_function(
            coroutine=find_rules_for_carrier,
            name="find_rules_for_carrier",
            description=(
                "Find all rules (Filter and Ordered List) whose account_list includes a given carrier "
                "account_id. Use to see every rule a carrier participates in. If you only have a carrier "
                "name or account code, call get_account_info first to resolve the account_id."
            ),
            args_schema=FindRulesForCarrierInput,
        ),
    ]
