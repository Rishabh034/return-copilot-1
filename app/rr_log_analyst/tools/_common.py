from __future__ import annotations

from typing import Any


def as_list(raw: Any) -> list:
    """Coerce a Clickpost list response into a plain list.

    Different endpoints wrap collections differently — a bare list, or under
    `results` / `data` / `rules` / `accounts`. This normalizes all of those.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("results", "data", "rules", "accounts", "result"):
            val = raw.get(key)
            if isinstance(val, list):
                return val
        # A single object — wrap it.
        if raw:
            return [raw]
    return []


def shape_rule(rule: dict) -> dict:
    """Pick the fields of a filter / ordered-list rule the analyst cares about."""
    return {
        "id": rule.get("id"),
        "name": rule.get("name"),
        "rule_type": rule.get("rule_type"),
        "priority": rule.get("priority"),
        "enabled": rule.get("enabled"),
        "account_list": rule.get("account_list") or [],
        "rule": rule.get("rule") or [],
    }


_FILTERS_RAN_KEYS = (
    "1_active_accounts_filter",
    "1_blacklisted_accounts",
    "2_serviceable_accounts_filter",
    "2_1_serviceable_oda_accounts_filter",
    "clickpost_defined_and_custom_rule_matched",
    "3_clickpost_filters_rules_list",
    "3_clickpost_define_and_custom_filters_accounts_applied",
    "3_clickpost_define_and_custom_filters_accounts",
    "accounts_after_load_distribution_filter",
    "priority_list_account_names",
    "4_ordered_list_account_names_filters_applied",
    "ordered_list_filter_accounts",
    "5_accounts_after_intelligent_performance_allocation",
)


def shape_user_request(user_requests: Any) -> dict[str, Any] | None:
    if not isinstance(user_requests, list) or not user_requests:
        return None
    req = user_requests[0]
    if not isinstance(req, dict):
        return None

    shaped: dict[str, Any] = {
        "reference_number": req.get("reference_number"),
        "pickup_pincode": req.get("pickup_pincode"),
        "drop_pincode": req.get("drop_pincode"),
        "delivery_type": req.get("delivery_type"),
        "order_type": req.get("order_type"),
        "item": req.get("item"),
        "invoice_value": req.get("invoice_value"),
        "pickup_city": req.get("pickup_city"),
        "pickup_state": req.get("pickup_state"),
        "drop_city": req.get("drop_city"),
        "drop_state": req.get("drop_state"),
        "shipment_created_datetime_utc": req.get("shipment_created_datetime_utc"),
    }
    additional = req.get("additional")
    if isinstance(additional, dict):
        custom_fields = additional.get("custom_fields")
        if custom_fields:
            shaped["custom_fields"] = custom_fields
    return {k: v for k, v in shaped.items() if v is not None}


def shape_preference_entry(entry: dict) -> dict[str, Any]:
    shaped: dict[str, Any] = {
        "cp_name": entry.get("cp_name"),
        "account_id": entry.get("account_id"),
        "account_code": entry.get("account_code"),
        "priority": entry.get("priority"),
        "shipping_charge": entry.get("shipping_charge"),
        "mode_of_shipment": entry.get("mode_of_shipment"),
    }
    scores = entry.get("scores_computation")
    if isinstance(scores, dict):
        shaped["total_score"] = scores.get("total_score")
        actual = scores.get("scoring_params_actual")
        if isinstance(actual, dict):
            shaped["scores"] = {k: v for k, v in actual.items() if v is not None}
    return {k: v for k, v in shaped.items() if v is not None}


def shape_filters_ran(filters_ran: Any) -> dict[str, Any]:
    if not isinstance(filters_ran, dict):
        return {}
    return {key: filters_ran[key] for key in _FILTERS_RAN_KEYS if key in filters_ran}


def shape_allocation_result(
    body: dict,
    *,
    order: dict[str, Any] | None = None,
    reference_number: str | None = None,
) -> dict[str, Any]:
    filters_ran = shape_filters_ran(body.get("filters_ran"))
    preference_array = [
        shape_preference_entry(entry)
        for entry in (body.get("preference_array") or [])
        if isinstance(entry, dict)
    ]

    shaped: dict[str, Any] = {
        "reference_number": body.get("reference_number") or reference_number,
        "username": body.get("username"),
        "pincode_serviceable": body.get("pincode_serviceable"),
        "order": order,
        "filters_ran": filters_ran,
        "preference_array": preference_array,
        "total_ranked": len(preference_array),
    }
    matched_rule = filters_ran.get("clickpost_defined_and_custom_rule_matched")
    if matched_rule:
        shaped["matched_filter_rule"] = matched_rule
    ordered_list_applied = filters_ran.get("4_ordered_list_account_names_filters_applied")
    if ordered_list_applied is not None:
        shaped["ordered_list_applied"] = ordered_list_applied
    return {k: v for k, v in shaped.items() if v is not None}
