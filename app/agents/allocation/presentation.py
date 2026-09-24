"""What the allocation agent calls its own tools, and how it describes results.

Moved verbatim out of the runner. The label map is this agent's vocabulary, and
`_summarize_result` dispatches on the shape of dicts its own tools produce —
`total_ranked`/`filters_ran` means an RR log, `pickup_count`/`drop_count` means
a serviceability check. Neither is knowledge the runner can hold once a second
agent exists.

Deliberately a move, not a redesign. Pushing each summariser down into the tool
module that produces the shape is a nicer end state and is explicitly not part
of this change: the wire snapshot cannot protect a rewrite, only a relocation.
"""

from __future__ import annotations

from app.core.runtime.presentation import humanize

_FRIENDLY_TOOL_LABELS: dict[str, str] = {
    "fetch_rr_log": "Looking up the order",
    "fetch_order_creation_log": "Checking the order creation history",
    "fetch_audit_logs": "Checking recent changes",
    "list_rules": "Reading the delivery settings",
    "find_rule": "Finding a setting",
    "list_carrier_exception_rules": "Reading carrier attribute rules",
    "get_enterprise_flow_config": "Checking the delivery setup",
    "get_load_distribution_config": "Checking how orders are split",
    "run_recommendation": "Running a test",
    "get_account_info": "Looking up delivery company accounts",
    "search_courier_partners": "Searching delivery companies",
    "check_route_serviceability": "Checking route serviceability",
    "inspect_evidence": "Reviewing the evidence",
    "inspect_source_segment": "Checking supporting details",
}


def _summarize_result(tool_name: str, result: dict) -> str:
    if "error" in result:
        return f"Hit a problem: {result['error']}"

    if "total_ranked" in result and "filters_ran" in result:
        rule = result.get("matched_filter_rule")
        top_pref = (result.get("preference_array") or [{}])[0] if result.get("preference_array") else {}
        top_name = top_pref.get("cp_name") or top_pref.get("account_code") or ""
        parts = [f"Looked up this order — {result['total_ranked']} courier partners were available"]
        if top_name:
            parts.append(f"{top_name} was chosen first")
        if rule:
            parts.append(f"because of the \"{rule}\" rule")
        return ", ".join(parts)

    if "top_courier" in result:
        top = result.get("top_courier") or {}
        name = top.get("cp_name") or top.get("account_code") or "the top one"
        return f"{name} was the first choice, out of {result.get('total_ranked', 0)} delivery companies"

    if "stage" in result and "value" in result:
        from app.agents.allocation.tools.allocation_views import _PIPELINE_STAGE_DESCRIPTIONS
        desc = _PIPELINE_STAGE_DESCRIPTIONS.get(result["stage"], result["stage"])
        return f"Checked one step of how the delivery company was picked: {desc}"

    if "ranking" in result and "total_ranked" in result:
        matching = result.get("total_matching", len(result["ranking"]))
        return f"{matching} delivery company(s) matched, out of {result['total_ranked']} that were available"

    if "in_final_ranking" in result:
        present = result["in_final_ranking"]
        if present:
            return "Yes — this delivery company was one of the final choices"
        return "No — this delivery company was ruled out before the final choices"

    if "flow_summary" in result and "flags" in result:
        return "Checked the settings that control how delivery companies are picked"

    if "flag" in result and "value" in result:
        state = "on" if result["value"] in (True, "true", "True", 1) else "off"
        return f"The \"{result['flag']}\" setting is turned {state}"

    # Audit logs must be matched before the generic count+logs branch below, which
    # would otherwise describe rule edits as "shipments".
    if "action_taken" in result and "logs" in result:
        count = result.get("count", 0)
        if count:
            return f"Found {count} recent change(s) made to the settings"
        return "No setting changes found for this time period"

    if "pickup_count" in result and "drop_count" in result:
        route_count = result.get("count", 0)
        if result.get("serviceable") is True:
            return "This account is serviceable for the pickup-to-drop route"
        if result.get("serviceable") is False:
            return "This account is not serviceable for the pickup-to-drop route"
        return (
            f"Found {route_count} account(s) serviceable on both pickup and drop "
            f"({result['pickup_count']} pickup, {result['drop_count']} drop)"
        )

    if "is_pickup_pincode" in result and "pincode" in result:
        side = "pickup" if result["is_pickup_pincode"] else "drop"
        if result.get("serviceable") is True:
            return f"This account is serviceable for the {side} pincode"
        if result.get("serviceable") is False:
            return f"This account is not serviceable for the {side} pincode"
        return f"Found {result.get('count', 0)} account(s) for {side} pincode {result['pincode']}"

    if "count" in result and "summaries" in result:
        return f"Reviewed {result['count']} shipments"

    if "count" in result and "logs" in result:
        return f"Pulled up the history for {result['count']} shipments"

    if "count" in result:
        return f"Found {result['count']} results"

    if "total_rules" in result:
        return f"Found {result['total_rules']} setting(s) that decide which delivery company to use"

    if "total_accounts" in result:
        brands = result.get("carrier_brands") or []
        return f"Found {result['total_accounts']} delivery company account(s) across {len(brands)} carriers"

    if "total_entries" in result and "enabled_entries" in result:
        return (
            f"Checked how orders are split between delivery companies — "
            f"{result['enabled_entries']} of {result['total_entries']} splits are active"
        )

    if "total_matching" in result:
        rules = result.get("rules") or []
        if rules:
            names = ", ".join(r.get("name", "unnamed") for r in rules[:3])
            suffix = ", and more" if len(rules) > 3 else ""
            return f"Found {result['total_matching']} matching setting(s): {names}{suffix}"
        return f"Found {result['total_matching']} matching setting(s)"

    if "rule" in result:
        rule = result["rule"]
        return f"Opened the \"{rule.get('name', 'unnamed')}\" setting"

    if "flow_summary" in result:
        return "Checked the settings that control how delivery companies are picked"

    if "results" in result and "count" in result:
        first = result["results"][0] if result["results"] else {}
        ranked = first.get("total_ranked", 0)
        return f"Ran a test for {result['count']} order(s) — {ranked} delivery companies came up"

    if "entry_count" in result and "entries" in result:
        n = result["entry_count"]
        return f"Found {n} time(s) this shipment was sent to a delivery company"

    if "resolved" in result:
        resolved = result["resolved"]
        return f"This is for the \"{resolved.get('name', 'unknown')}\" account"

    if "matches" in result:
        count = result.get("match_count", len(result["matches"]))
        if count == 1:
            name = result["matches"][0].get("name", "unknown") if result["matches"] else "unknown"
            return f"Found the account: {name}"
        return f"Found {count} possible accounts — please pick the right one"

    return "Done"


class AllocationPresentation:
    def tool_label(self, tool_name: str) -> str:
        return _FRIENDLY_TOOL_LABELS.get(tool_name, humanize(tool_name))

    def summarize(self, tool_name: str, result: dict) -> str:
        return _summarize_result(tool_name, result)


PRESENTATION = AllocationPresentation()
