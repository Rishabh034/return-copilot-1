from __future__ import annotations

from typing import Any

from app.rr_log_analyst.tools import ToolContext, register
from app.rr_log_analyst.tools._common import as_list, shape_rule

_PINCODE_KEYS = {"pickup_pincode", "drop_pincode"}


def _condition_pincodes(condition: dict) -> list[str]:
    """Extract the individual pincodes a condition matches against.

    Pincode conditions store a comma-separated string, e.g.
    `"value": "560082, 560083, 560016"`.
    """
    value = condition.get("value")
    if not isinstance(value, str):
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


@register
class ListFilterRulesTool:
    name = "list_filter_rules"
    description = (
        "List all Filter rules configured for the enterprise, in priority order. Filter rules "
        "are the eligibility gate (AND conditions; first match wins) — each yields a pool of "
        "eligible carrier accounts. Use to get an overview or to locate a rule by name/priority."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        raw = await context.clickpost_client.list_rules(token=context.token, rule_type="FILTER")
        rules = [shape_rule(r) for r in as_list(raw)]
        rules.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
        return {"total_rules": len(rules), "rules": rules}


@register
class ListOrderedListRulesTool:
    name = "list_ordered_list_rules"
    description = (
        "List all Ordered List (OL) rules configured for the enterprise, in priority order. OL "
        "rules define carrier priority order for a specific route; final allocation is the "
        "intersection of the OL's account_list with the matched filter's eligible pool, in OL order."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        raw = await context.clickpost_client.list_rules(token=context.token, rule_type="ORDERED_LIST")
        rules = [shape_rule(r) for r in as_list(raw)]
        rules.sort(key=lambda r: (r.get("priority") is None, r.get("priority")))
        return {"total_rules": len(rules), "rules": rules}


@register
class GetFilterRuleTool:
    name = "get_filter_rule"
    description = (
        "Fetch the full definition of a single rule (Filter or Ordered List) by its id — all "
        "conditions, operators, priority and the account_list. Use after an RR log shows a "
        "specific rule fired or dropped a courier, to explain exactly why."
    )
    parameters = {
        "type": "object",
        "properties": {
            "rule_id": {
                "type": "integer",
                "description": "The id of the filter / ordered-list rule to fetch.",
            }
        },
        "required": ["rule_id"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        rule_id = args.get("rule_id")
        if rule_id is None:
            return {"error": "rule_id is required"}

        rule_id = int(rule_id)
        # The underlying endpoint can't filter by id, so it returns every rule;
        # pick out the one we want here.
        raw = await context.clickpost_client.get_rule(token=context.token, rule_id=rule_id)
        match = next((r for r in as_list(raw) if r.get("id") == rule_id), None)
        if match is None:
            return {"error": f"No rule found for id {rule_id}"}
        return {"rule": shape_rule(match)}


@register
class FindRulesForPincodeTool:
    name = "find_rules_for_pincode"
    description = (
        "Find all rules (Filter and Ordered List) whose conditions reference a given pickup "
        "and/or drop pincode. Use to answer 'what rules affect this route/pincode'. Matches "
        "pincode conditions (pickup_pincode / drop_pincode with IN, EQ or CONTAINS operators)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pickup_pincode": {
                "type": "string",
                "description": "Pickup pincode to match against rule conditions (optional).",
            },
            "drop_pincode": {
                "type": "string",
                "description": "Drop pincode to match against rule conditions (optional).",
            },
        },
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        pickup = (args.get("pickup_pincode") or "").strip()
        drop = (args.get("drop_pincode") or "").strip()
        if not pickup and not drop:
            return {"error": "Provide at least one of pickup_pincode or drop_pincode"}

        # wanted: which condition key must contain which pincode
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


@register
class FindRulesForCarrierTool:
    name = "find_rules_for_carrier"
    description = (
        "Find all rules (Filter and Ordered List) whose account_list includes a given carrier "
        "account_id. Use to see every rule a carrier participates in. If you only have a carrier "
        "name or account code, call get_account_info first to resolve the account_id."
    )
    parameters = {
        "type": "object",
        "properties": {
            "account_id": {
                "type": "integer",
                "description": "The carrier account id to search for in rule account_lists.",
            }
        },
        "required": ["account_id"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        account_id = args.get("account_id")
        if account_id is None:
            return {"error": "account_id is required"}
        account_id = int(account_id)

        filters = as_list(await context.clickpost_client.list_rules(token=context.token, rule_type="FILTER"))
        ols = as_list(await context.clickpost_client.list_rules(token=context.token, rule_type="ORDERED_LIST"))

        matching: list[dict] = []
        for raw_rule in [*filters, *ols]:
            rule = shape_rule(raw_rule)
            account_list = rule["account_list"]
            if account_id in account_list:
                # For OL rules position is the carrier's priority (0-indexed).
                position = account_list.index(account_id)
                matching.append({**rule, "carrier_position": position})

        return {"total_matching": len(matching), "rules": matching}
