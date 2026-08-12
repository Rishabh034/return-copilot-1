from __future__ import annotations

from typing import Any

from app.rr_log_analyst.tools.context import ToolContext


async def cached_fetch(context: ToolContext, key: tuple, fetch) -> Any:
    """Memoize a raw Clickpost payload for the current request cycle.

    Several tools pull the same endpoints: the accounts list backs both
    `get_account_info` and every load-distribution call, and `find_rule` re-pulled
    the whole rule set on every invocation. Cached on the ToolContext, which the
    runner rebuilds per user message, so repeated reads within one agent loop are
    free while the next message still sees fresh data.

    Caches the *raw* payload, not a shaped one — callers shape the same response
    differently (accounts become a list for one tool and an id lookup for another).
    """
    cache = context.raw_fetch_cache
    if key in cache:
        return cache[key]
    value = await fetch()
    cache[key] = value
    return value


def as_list(raw: Any) -> list:
    """Coerce a Clickpost list response into a plain list.

    Different endpoints wrap collections differently — a bare list, or under
    `results` / `data` / `rules` / `accounts`. This normalizes all of those.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("results", "data", "dataList", "rules", "accounts", "result"):
            val = raw.get(key)
            if isinstance(val, list):
                return val
        # A single object — wrap it.
        if raw:
            return [raw]
    return []


_VALUE_SAMPLES = 5
_VALUE_PREFIXES = 12


def _split_condition_value(value: Any) -> list:
    """Normalize a condition value into a list of individual values."""
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return [part for part in parts if part]
    return [value] if value not in (None, "") else []


def summarize_condition_value(value: Any) -> dict:
    """Describe a condition's value without reproducing an unbounded list.

    A single pincode `IN` condition can hold 1000+ values (tens of thousands of
    chars), which is what makes raw rules too large to hand to the model. The
    count plus a few samples and the distinct prefixes answer "which lanes does
    this cover" — the actual question — at a fraction of the size.
    """
    items = _split_condition_value(value)
    if not items:
        return {}
    if len(items) == 1:
        return {"value": items[0]}

    summary: dict = {"value_count": len(items), "value_samples": items[:_VALUE_SAMPLES]}
    prefixes = sorted(
        {
            f"{str(item)[:3]}xxx"
            for item in items
            if str(item).isdigit() and len(str(item)) >= 4
        }
    )
    if prefixes:
        summary["value_prefixes"] = prefixes[:_VALUE_PREFIXES]
        if len(prefixes) > _VALUE_PREFIXES:
            summary["value_prefixes_truncated"] = len(prefixes)
    return summary


def shape_rule_digest(shaped: dict) -> dict:
    """Shape a rule with its conditions intact but its value lists bounded.

    Carries what an analyst needs to explain a rule without a follow-up call —
    operators, what each condition tests, how much it covers, and the eligible
    accounts. Only the complete raw value list is omitted; `find_rule` has it.
    """
    digest: dict = {}
    for key in ("id", "name", "rule_type", "priority", "enabled", "updated_at"):
        val = shaped.get(key)
        if val is not None:
            digest[key] = val

    accounts = shaped.get("account_list")
    if accounts:
        digest["account_list"] = accounts

    conditions = shaped.get("rule")
    if isinstance(conditions, list):
        digest["conditions"] = [
            {
                **{k: cond[k] for k in ("key", "op") if cond.get(k) is not None},
                **summarize_condition_value(cond.get("value")),
            }
            for cond in conditions
            if isinstance(cond, dict)
        ]
    return digest


def shape_rule(rule: dict) -> dict:
    """Pick the fields of a filter / ordered-list rule the analyst cares about."""
    shaped = {
        "id": rule.get("id"),
        "name": rule.get("name"),
        "rule_type": rule.get("rule_type"),
        "priority": rule.get("priority"),
        "enabled": rule.get("enabled"),
        "updated_at": rule.get("updated_at"),
        "account_list": rule.get("account_list") or [],
        "rule": rule.get("rule") or [],
    }
    return {k: v for k, v in shaped.items() if v is not None and v != ""}

def shape_carrier_exception_rule(rule: dict) -> dict:
    """Pick the fields of an Attribute Layer (carrier exception) rule the analyst cares about."""
    shaped = {
        "id": rule.get("id"),
        "courier_partner_id": rule.get("courier_partner_id"),
        "rule_name": rule.get("rule_name"),
        "priority": rule.get("priority"),
        "rule": rule.get("rule") or [],
        "exclusion_list": rule.get("exclusion_list") or [],
        "enabled": rule.get("enabled"),
        "updated_at": rule.get("updated_at"),
    }

    return {k: v for k, v in shaped.items() if v is not None and v != ""}

FILTERS_RAN_KEYS = (
    "1_active_accounts_filter",
    "1_blacklisted_accounts",
    "2_serviceable_accounts_filter",
    "2_1_serviceable_oda_accounts_filter",
    "2_3_1_attribute_rules_matched",
    "clickpost_defined_and_custom_rule_matched",
    "3_clickpost_filters_rules_list",
    "3_clickpost_define_and_custom_filters_accounts_applied",
    "3_clickpost_define_and_custom_filters_accounts",
    "accounts_after_load_distribution_filter",
    "accounts_after_load_distribution",
    "priority_list_account_names",
    "4_ordered_list_rules_list",
    "order_list_matched_rule",
    "4_ordered_list_account_names_filters_applied",
    "ordered_list_filter_accounts",
    "5_accounts_after_intelligent_performance_allocation",
)


_USER_REQUEST_FIELDS = (
    "reference_number",
    "item",
    "order_type",
    "delivery_type",
    "invoice_value",
    "pickup_pincode",
    "drop_pincode",
    "weight",
    "volumetric_weight",
    "higher_of_dry_weight_and_volumetric_weight",
    "pickup_city",
    "drop_city",
    "pickup_postal_code",
    "drop_postal_code",
    "pickup_country_code",
    "drop_country_code",
    "length",
    "height",
    "breadth",
    "pickup_city_postal_code",
    "drop_city_postal_code",
    "is_restricted",
    "fareye_store_id",
    "enterprise_store_id",
    "pickup_address",
    "drop_address",
    "zone",
    "pickup_name",
    "item_count",
    "pickup_location_code",
    "drop_location_code",
    "pickup_state",
    "drop_state",
    "box_code",
    "shipment_created_datetime_utc",
)


def shape_user_request(user_requests: Any) -> dict[str, Any] | None:
    if not isinstance(user_requests, list) or not user_requests:
        return None
    req = user_requests[0]
    if not isinstance(req, dict):
        return None

    shaped: dict[str, Any] = {
        field: req.get(field) for field in _USER_REQUEST_FIELDS if req.get(field) is not None
    }
    additional = req.get("additional")
    if isinstance(additional, dict):
        custom_fields = additional.get("custom_fields")
        if custom_fields:
            shaped["custom_fields"] = custom_fields
    return shaped or None


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
    return {key: filters_ran[key] for key in FILTERS_RAN_KEYS if key in filters_ran}


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
    matched_ol = filters_ran.get("order_list_matched_rule")
    if matched_ol:
        shaped["matched_ordered_list_rule"] = matched_ol
    return {k: v for k, v in shaped.items() if v is not None}
