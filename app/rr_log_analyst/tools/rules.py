from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import as_list, compact_rule_summary, shape_rule
from app.rr_log_analyst.tools.context import ToolContext
from app.rr_log_analyst.tools.flow_config import EmptyInput

_PINCODE_KEYS = {"pickup_pincode", "drop_pincode"}


class SearchRulesInput(BaseModel):
    priority: int | None = Field(
        default=None,
        description="Exact priority number to match (e.g. 12 returns only priority-12 rules).",
    )
    name: str = Field(
        default="",
        description="Substring to match against rule name (case-insensitive).",
    )
    rule_id: str = Field(
        default="",
        description="Exact rule id to match.",
    )
    pickup_pincode: str = Field(
        default="",
        description="Return rules whose conditions reference this pickup pincode.",
    )
    drop_pincode: str = Field(
        default="",
        description="Return rules whose conditions reference this drop pincode.",
    )
    enabled: bool | None = Field(
        default=None,
        description="Filter by enabled status (true/false). Omit to ignore.",
    )


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


def _pincode_matches(condition: dict, pincode: str) -> bool:
    pincodes = _condition_pincodes(condition)
    op = (condition.get("op") or "").upper()
    return pincode in pincodes or (op == "CONTAINS" and any(pincode in p for p in pincodes))


def _search_criteria_provided(
    *,
    priority: int | None,
    name: str,
    rule_id: str,
    pickup_pincode: str,
    drop_pincode: str,
    enabled: bool | None,
) -> bool:
    return any(
        (
            priority is not None,
            bool(name.strip()),
            bool(rule_id.strip()),
            bool(pickup_pincode.strip()),
            bool(drop_pincode.strip()),
            enabled is not None,
        )
    )


def _applied_filters(
    *,
    priority: int | None,
    name: str,
    rule_id: str,
    pickup_pincode: str,
    drop_pincode: str,
    enabled: bool | None,
) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    if priority is not None:
        applied["priority"] = priority
    if name.strip():
        applied["name"] = name.strip()
    if rule_id.strip():
        applied["rule_id"] = rule_id.strip()
    if pickup_pincode.strip():
        applied["pickup_pincode"] = pickup_pincode.strip()
    if drop_pincode.strip():
        applied["drop_pincode"] = drop_pincode.strip()
    if enabled is not None:
        applied["enabled"] = enabled
    return applied


def _rule_matches_search(
    raw_rule: dict,
    *,
    priority: int | None,
    name: str,
    rule_id: str,
    pickup_pincode: str,
    drop_pincode: str,
    enabled: bool | None,
) -> bool:
    rule = shape_rule(raw_rule)

    if priority is not None and rule.get("priority") != priority:
        return False

    name_q = name.strip().lower()
    if name_q:
        rule_name = (rule.get("name") or "").lower()
        if name_q not in rule_name:
            return False

    rule_id_q = rule_id.strip()
    if rule_id_q and str(rule.get("id")) != rule_id_q:
        return False

    if enabled is not None and rule.get("enabled") is not enabled:
        return False

    pickup = pickup_pincode.strip()
    drop = drop_pincode.strip()
    if pickup or drop:
        wanted: dict[str, str] = {}
        if pickup:
            wanted["pickup_pincode"] = pickup
        if drop:
            wanted["drop_pincode"] = drop

        matched_pincode = False
        for cond in rule["rule"]:
            key = cond.get("key")
            if key not in _PINCODE_KEYS or key not in wanted:
                continue
            if _pincode_matches(cond, wanted[key]):
                matched_pincode = True
                break
        if not matched_pincode:
            return False

    return True


async def _fetch_rules_by_type(context: ToolContext, rule_type: str) -> list[dict]:
    raw = await context.clickpost_client.list_rules(token=context.token, rule_type=rule_type)
    return as_list(raw)


async def _search_rules(
    context: ToolContext,
    rule_type: str,
    *,
    priority: int | None = None,
    name: str = "",
    rule_id: str = "",
    pickup_pincode: str = "",
    drop_pincode: str = "",
    enabled: bool | None = None,
) -> dict[str, Any]:
    if not _search_criteria_provided(
        priority=priority,
        name=name,
        rule_id=rule_id,
        pickup_pincode=pickup_pincode,
        drop_pincode=drop_pincode,
        enabled=enabled,
    ):
        return {
            "error": (
                "Provide at least one search filter: priority, name, rule_id, pickup_pincode, "
                "drop_pincode, or enabled. For a compact overview of all rules, use "
                "summarize_filter_rules or summarize_ordered_list_rules instead."
            )
        }

    raw_rules = await _fetch_rules_by_type(context, rule_type)
    matching = [
        shape_rule(raw_rule)
        for raw_rule in raw_rules
        if _rule_matches_search(
            raw_rule,
            priority=priority,
            name=name,
            rule_id=rule_id,
            pickup_pincode=pickup_pincode,
            drop_pincode=drop_pincode,
            enabled=enabled,
        )
    ]
    matching.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))

    return {
        "rule_type": rule_type,
        "filters_applied": _applied_filters(
            priority=priority,
            name=name,
            rule_id=rule_id,
            pickup_pincode=pickup_pincode,
            drop_pincode=drop_pincode,
            enabled=enabled,
        ),
        "total_matching": len(matching),
        "rules": matching,
    }


async def _list_rules(context: ToolContext, rule_type: str) -> dict[str, Any]:
    raw_rules = await _fetch_rules_by_type(context, rule_type)
    rules = [shape_rule(raw_rule) for raw_rule in raw_rules]
    rules.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
    return {"total_rules": len(rules), "rules": rules}


async def _summarize_rules(context: ToolContext, rule_type: str) -> dict[str, Any]:
    raw_rules = await _fetch_rules_by_type(context, rule_type)
    summaries = [compact_rule_summary(shape_rule(raw_rule)) for raw_rule in raw_rules]
    summaries.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
    return {
        "rule_type": rule_type,
        "total_rules": len(summaries),
        "rules": summaries,
        "hint": "Compact index only (id, name, priority, enabled, condition_keys). "
        "Use search_filter_rules / search_ordered_list_rules with filters for full rule details.",
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

    filters = await _fetch_rules_by_type(context, "FILTER")
    ols = await _fetch_rules_by_type(context, "ORDERED_LIST")

    matching: list[dict] = []
    for raw_rule in [*filters, *ols]:
        rule = shape_rule(raw_rule)
        matched_on: list[dict] = []
        for cond in rule["rule"]:
            key = cond.get("key")
            if key not in _PINCODE_KEYS or key not in wanted:
                continue
            target = wanted[key]
            if _pincode_matches(cond, target):
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
    filters = await _fetch_rules_by_type(context, "FILTER")
    ols = await _fetch_rules_by_type(context, "ORDERED_LIST")

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
        return await _list_rules(context, "FILTER")

    async def list_ordered_list_rules() -> dict[str, Any]:
        return await _list_rules(context, "ORDERED_LIST")

    async def search_filter_rules(
        priority: int | None = None,
        name: str = "",
        rule_id: str = "",
        pickup_pincode: str = "",
        drop_pincode: str = "",
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        return await _search_rules(
            context,
            "FILTER",
            priority=priority,
            name=name,
            rule_id=rule_id,
            pickup_pincode=pickup_pincode,
            drop_pincode=drop_pincode,
            enabled=enabled,
        )

    async def search_ordered_list_rules(
        priority: int | None = None,
        name: str = "",
        rule_id: str = "",
        pickup_pincode: str = "",
        drop_pincode: str = "",
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        return await _search_rules(
            context,
            "ORDERED_LIST",
            priority=priority,
            name=name,
            rule_id=rule_id,
            pickup_pincode=pickup_pincode,
            drop_pincode=drop_pincode,
            enabled=enabled,
        )

    async def summarize_filter_rules() -> dict[str, Any]:
        return await _summarize_rules(context, "FILTER")

    async def summarize_ordered_list_rules() -> dict[str, Any]:
        return await _summarize_rules(context, "ORDERED_LIST")

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

    _sub_search_hint = (
        "Sub-tool: use when list_* is too large or you need a subset. Requires at least one filter: "
        "priority, name, rule_id, pickup_pincode, drop_pincode, or enabled."
    )

    return [
        StructuredTool.from_function(
            coroutine=list_filter_rules,
            name="list_filter_rules",
            description=(
                "PRIMARY — List all Filter rules in priority order with full details (conditions, "
                "priority, account_list). Use first when you need the complete rule set. If the result "
                "is too large, use search_filter_rules or summarize_filter_rules instead."
            ),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            coroutine=list_ordered_list_rules,
            name="list_ordered_list_rules",
            description=(
                "PRIMARY — List all Ordered List rules in priority order with full details. Use first "
                "for a complete OL overview. If too large, use search_ordered_list_rules or "
                "summarize_ordered_list_rules."
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
        StructuredTool.from_function(
            coroutine=search_filter_rules,
            name="search_filter_rules",
            description=(
                "SUB-TOOL — Filter rules by priority, name, rule_id, pincode, or enabled. "
                "Example: priority=12 returns only priority-12 rules with full details. " + _sub_search_hint
            ),
            args_schema=SearchRulesInput,
        ),
        StructuredTool.from_function(
            coroutine=search_ordered_list_rules,
            name="search_ordered_list_rules",
            description="SUB-TOOL — Same filters as search_filter_rules but for Ordered List rules. " + _sub_search_hint,
            args_schema=SearchRulesInput,
        ),
        StructuredTool.from_function(
            coroutine=summarize_filter_rules,
            name="summarize_filter_rules",
            description=(
                "SUB-TOOL — Compact index of all Filter rules (id, name, priority, enabled, "
                "condition_keys only). Use when list_filter_rules is too large."
            ),
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            coroutine=summarize_ordered_list_rules,
            name="summarize_ordered_list_rules",
            description=(
                "SUB-TOOL — Compact index of all OL rules. Use when list_ordered_list_rules is too large."
            ),
            args_schema=EmptyInput,
        ),
    ]
