from __future__ import annotations

from typing import Any

from app.rr_log_analyst.tools._common import FILTERS_RAN_KEYS

_PIPELINE_STAGE_DESCRIPTIONS: dict[str, str] = {
    "1_active_accounts_filter": "Active carrier accounts after activation filter",
    "1_blacklisted_accounts": "Blacklisted accounts removed",
    "2_serviceable_accounts_filter": "Serviceable accounts for pincode pair",
    "2_1_serviceable_oda_accounts_filter": "ODA pinched serviceable accounts",
    "clickpost_defined_and_custom_rule_matched": "Matched Filter rule name",
    "3_clickpost_filters_rules_list": "Filter rules evaluated",
    "3_clickpost_define_and_custom_filters_accounts_applied": "Whether filter stage applied",
    "3_clickpost_define_and_custom_filters_accounts": "Accounts after matched filter",
    "accounts_after_load_distribution_filter": "Accounts after per-filter load distribution",
    "accounts_after_load_distribution": "Accounts after terminal load distribution",
    "priority_list_account_names": "Upstream pool passed to OL (filter pool ∩ serviceable, or all serviceable if no filter)",
    "4_ordered_list_rules_list": "Ordered List rules evaluated (sequence, has_serviceable_accounts)",
    "order_list_matched_rule": "Matched Ordered List rule name",
    "4_ordered_list_account_names_filters_applied": "Whether an Ordered List matched",
    "ordered_list_filter_accounts": "Final accounts after OL intersection",
    "5_accounts_after_intelligent_performance_allocation": "Accounts after PBA / intelligent feedback",
}

_MAX_LIST_SAMPLE = 8


def _as_name_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if value is not None:
        return [str(value)]
    return []


def _compact_stage_value(value: Any, *, account_id: int | None, cp_name: str) -> Any:
    if not isinstance(value, list):
        return value

    names = _as_name_list(value)
    if not names:
        return value

    compact: dict[str, Any] = {"account_count": len(names)}
    sample = names[:_MAX_LIST_SAMPLE]
    if len(names) > _MAX_LIST_SAMPLE:
        compact["accounts_sample"] = sample
        compact["truncated"] = True
    else:
        compact["accounts"] = sample

    account_q = str(account_id) if account_id is not None else ""
    cp_q = cp_name.strip().lower()
    if account_q or cp_q:
        hits = [
            name
            for name in names
            if (account_q and account_q in name)
            or (cp_q and cp_q in name.lower())
        ]
        compact["matching_entries"] = hits
        compact["present"] = bool(hits)

    return compact


def compact_allocation_summary(log: dict[str, Any]) -> dict[str, Any]:
    preference = log.get("preference_array") or []
    top = preference[0] if preference else None
    order = log.get("order") or {}
    summary: dict[str, Any] = {
        "reference_number": log.get("reference_number"),
        "pincode_serviceable": log.get("pincode_serviceable"),
        "matched_filter_rule": log.get("matched_filter_rule"),
        "ordered_list_applied": log.get("ordered_list_applied"),
        "total_ranked": log.get("total_ranked", len(preference)),
        "top_courier": (
            {
                "cp_name": top.get("cp_name"),
                "account_id": top.get("account_id"),
                "account_code": top.get("account_code"),
                "priority": top.get("priority"),
            }
            if top
            else None
        ),
    }
    if order:
        summary["order"] = {
            k: order.get(k)
            for k in (
                "pickup_pincode",
                "drop_pincode",
                "delivery_type",
                "order_type",
                "item",
                "invoice_value",
            )
            if order.get(k) is not None
        }
    if "error" in log:
        summary["error"] = log["error"]
    filters_ran = log.get("filters_ran") or {}
    if filters_ran:
        summary["pipeline_counts"] = {
            k: len(v)
            for k, v in {
                "serviceable_accounts": filters_ran.get("2_serviceable_accounts_filter"),
                "upstream_pool": filters_ran.get("priority_list_account_names"),
                "ol_intersection": filters_ran.get("ordered_list_filter_accounts"),
            }.items()
            if isinstance(v, list)
        }
    return {k: v for k, v in summary.items() if v is not None}


def extract_order(log: dict[str, Any]) -> dict[str, Any]:
    if "error" in log:
        return log
    order = log.get("order")
    if not order:
        return {"reference_number": log.get("reference_number"), "error": "No order data in log"}
    return {"reference_number": log.get("reference_number"), "order": order}


def extract_pipeline(
    log: dict[str, Any],
    stage: str,
    *,
    account_id: int | None = None,
    cp_name: str = "",
) -> dict[str, Any]:
    if "error" in log:
        return log

    stage = stage.strip()
    if not stage:
        return {
            "error": (
                "stage is required. Choose one of: "
                + ", ".join(FILTERS_RAN_KEYS)
            ),
            "available_stages": {
                key: _PIPELINE_STAGE_DESCRIPTIONS.get(key, key)
                for key in FILTERS_RAN_KEYS
            },
        }

    filters_ran = log.get("filters_ran") or {}
    if stage not in filters_ran:
        return {
            "reference_number": log.get("reference_number"),
            "error": f"Stage '{stage}' not present in this log",
            "available_stages": list(filters_ran.keys()),
        }

    value = filters_ran[stage]
    if account_id is not None or cp_name.strip():
        value = _compact_stage_value(value, account_id=account_id, cp_name=cp_name)
    elif isinstance(value, list) and len(value) > _MAX_LIST_SAMPLE:
        value = _compact_stage_value(value, account_id=None, cp_name="")

    return {
        "reference_number": log.get("reference_number"),
        "stage": stage,
        "description": _PIPELINE_STAGE_DESCRIPTIONS.get(stage, stage),
        "value": value,
    }


def extract_ranking(
    log: dict[str, Any],
    *,
    account_id: int | None = None,
    cp_name: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    if "error" in log:
        return log

    preference = log.get("preference_array") or []
    if account_id is not None:
        matches = [e for e in preference if e.get("account_id") == account_id]
    elif cp_name.strip():
        q = cp_name.strip().lower()
        matches = [
            e
            for e in preference
            if q in str(e.get("cp_name") or "").lower()
            or q in str(e.get("account_code") or "").lower()
        ]
    else:
        matches = preference[: max(1, min(limit, 20))]

    return {
        "reference_number": log.get("reference_number"),
        "filters_applied": {
            k: v
            for k, v in {
                "account_id": account_id,
                "cp_name": cp_name.strip() or None,
                "limit": limit if account_id is None and not cp_name.strip() else None,
            }.items()
            if v is not None
        },
        "total_ranked": len(preference),
        "total_matching": len(matches),
        "ranking": matches,
    }


def search_courier_in_allocation(
    log: dict[str, Any],
    *,
    account_id: int | None = None,
    cp_name: str = "",
) -> dict[str, Any]:
    if "error" in log:
        return log
    if account_id is None and not cp_name.strip():
        return {"error": "Provide account_id or cp_name to search for a courier in the log"}

    ranking = extract_ranking(log, account_id=account_id, cp_name=cp_name, limit=1)
    in_final = ranking.get("total_matching", 0) > 0
    final_entry = (ranking.get("ranking") or [None])[0]

    stage_presence: dict[str, Any] = {}
    for stage in FILTERS_RAN_KEYS:
        filters_ran = log.get("filters_ran") or {}
        if stage not in filters_ran:
            continue
        compact = _compact_stage_value(
            filters_ran[stage],
            account_id=account_id,
            cp_name=cp_name,
        )
        if isinstance(compact, dict) and "present" in compact:
            stage_presence[stage] = compact["present"]
        elif isinstance(compact, dict) and compact.get("matching_entries"):
            stage_presence[stage] = True
        elif isinstance(filters_ran[stage], str):
            stage_presence[stage] = True

    return {
        "reference_number": log.get("reference_number"),
        "search": {"account_id": account_id, "cp_name": cp_name.strip() or None},
        "in_final_ranking": in_final,
        "final_ranking_entry": final_entry,
        "pipeline_stage_presence": stage_presence,
    }
