from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import as_list
from app.rr_log_analyst.tools.context import ToolContext
from app.rr_log_analyst.tools.flow_config import EmptyInput


class SearchLoadDistributionInput(BaseModel):
    account_id: int | None = Field(
        default=None,
        description="SUB-TOOL filter — return only this carrier's LD entry. Omit for full config.",
    )


class GetLoadDistributionFilterConfigInput(BaseModel):
    filter_id: int = Field(
        description=(
            "The Filter rule id whose per-lane load distribution config to fetch. "
            "Get this from get_filter_rule, find_rule, or matched_filter_rule in an RR log."
        ),
    )
    account_id: int | None = Field(
        default=None,
        description="Return only the entry for this account_id within the filter's LD config.",
    )


def _account_lookup(accounts: list[dict]) -> dict[int, dict]:
    lookup: dict[int, dict] = {}
    for acc in accounts:
        account_id = acc.get("account_id") or acc.get("id")
        if account_id is not None:
            lookup[int(account_id)] = acc
    return lookup


def _enrich_account(
    account_id: int | None,
    lookup: dict[int, dict],
) -> dict[str, Any]:
    if account_id is None:
        return {}
    acc = lookup.get(int(account_id), {})
    return {
        "account_code": acc.get("account_code") or acc.get("code"),
        "cp_name": acc.get("cp_name") or acc.get("carrier_name") or acc.get("courier_partenr_name"),
    }


def _shape_forward_entry(entry: dict, lookup: dict[int, dict]) -> dict[str, Any]:
    account_id = entry.get("param_key_id")
    shaped: dict[str, Any] = {
        "id": entry.get("id"),
        "account_id": account_id,
        "distribution_param": entry.get("distribution_param"),
        "percent_value": entry.get("percent_value"),
        "max_cap_per_day": entry.get("max_cap_per_day"),
        "enabled": entry.get("enabled"),
    }
    shaped.update(_enrich_account(account_id, lookup))
    return {k: v for k, v in shaped.items() if v is not None}


def _shape_filter_entry(entry: dict, lookup: dict[int, dict]) -> dict[str, Any]:
    account_id = entry.get("account_id")
    shaped: dict[str, Any] = {
        "id": entry.get("id"),
        "account_id": account_id,
        "distribution_param": entry.get("distribution_param"),
        "allocation_value": entry.get("allocation_value"),
    }
    shaped.update(_enrich_account(account_id, lookup))
    return {k: v for k, v in shaped.items() if v is not None}


def _compact_forward_entry(entry: dict) -> dict[str, Any]:
    return {
        "account_id": entry.get("account_id"),
        "cp_name": entry.get("cp_name"),
        "percent_value": entry.get("percent_value"),
        "max_cap_per_day": entry.get("max_cap_per_day"),
        "enabled": entry.get("enabled"),
    }


def _compact_filter_entry(entry: dict) -> dict[str, Any]:
    return {
        "account_id": entry.get("account_id"),
        "cp_name": entry.get("cp_name"),
        "allocation_value": entry.get("allocation_value"),
    }


async def _fetch_accounts(context: ToolContext) -> dict[int, dict]:
    raw = await context.clickpost_client.list_accounts(token=context.token)
    return _account_lookup(as_list(raw))


async def _get_load_distribution_config(
    context: ToolContext,
    account_id: int | None = None,
) -> dict[str, Any]:
    raw = await context.clickpost_client.get_load_distribution_config(token=context.token)
    lookup = await _fetch_accounts(context)
    entries = [_shape_forward_entry(e, lookup) for e in as_list(raw)]

    if account_id is not None:
        matches = [e for e in entries if e.get("account_id") == account_id]
        return {
            "delivery_type": "FORWARD",
            "config_type": "account_level",
            "filters_applied": {"account_id": account_id},
            "total_matching": len(matches),
            "entries": matches,
        }

    enabled = [e for e in entries if e.get("enabled")]
    return {
        "delivery_type": "FORWARD",
        "config_type": "account_level",
        "total_entries": len(entries),
        "enabled_entries": len(enabled),
        "entries": entries,
    }


async def _get_load_distribution_reverse_config(
    context: ToolContext,
    account_id: int | None = None,
) -> dict[str, Any]:
    raw = await context.clickpost_client.get_load_distribution_reverse_config(token=context.token)
    lookup = await _fetch_accounts(context)
    entries = [_shape_forward_entry(e, lookup) for e in as_list(raw)]

    if account_id is not None:
        matches = [e for e in entries if e.get("account_id") == account_id]
        return {
            "delivery_type": "RVP",
            "config_type": "account_level",
            "filters_applied": {"account_id": account_id},
            "total_matching": len(matches),
            "entries": matches,
        }

    enabled = [e for e in entries if e.get("enabled")]
    return {
        "delivery_type": "RVP",
        "config_type": "account_level",
        "total_entries": len(entries),
        "enabled_entries": len(enabled),
        "entries": entries,
    }


async def _get_load_distribution_filter_config(
    context: ToolContext,
    filter_id: int,
    account_id: int | None = None,
) -> dict[str, Any]:
    raw = await context.clickpost_client.get_load_distribution_filter_config(
        token=context.token,
        filter_id=filter_id,
    )
    lookup = await _fetch_accounts(context)
    entries = [_shape_filter_entry(e, lookup) for e in as_list(raw)]

    if account_id is not None:
        matches = [e for e in entries if e.get("account_id") == account_id]
        return {
            "filter_id": filter_id,
            "config_type": "filter_level",
            "filters_applied": {"account_id": account_id},
            "total_matching": len(matches),
            "entries": matches,
        }

    return {
        "filter_id": filter_id,
        "config_type": "filter_level",
        "total_entries": len(entries),
        "entries": entries,
        "hint": (
            "allocation_value meaning depends on the filter's allocation_type: PERCENTAGE or "
            "ABSOLUTE_CAPPING. Pass account_id to filter to one carrier."
        ),
    }


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def get_load_distribution_config(
        account_id: int | None = None,
    ) -> dict[str, Any]:
        return await _get_load_distribution_config(context, account_id)

    async def get_load_distribution_reverse_config(
        account_id: int | None = None,
    ) -> dict[str, Any]:
        return await _get_load_distribution_reverse_config(context, account_id)

    async def get_load_distribution_filter_config(
        filter_id: int,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        return await _get_load_distribution_filter_config(context, filter_id, account_id)

    _ld_filter_hint = "Pass account_id to filter to one carrier (sub-tool behaviour)."

    return [
        StructuredTool.from_function(
            coroutine=get_load_distribution_config,
            name="get_load_distribution_config",
            description=(
                "PRIMARY — Full forward load distribution config (percent_value, max_cap_per_day per "
                "account). Call get_enterprise_flow_config first. " + _ld_filter_hint
            ),
            args_schema=SearchLoadDistributionInput,
        ),
        StructuredTool.from_function(
            coroutine=get_load_distribution_reverse_config,
            name="get_load_distribution_reverse_config",
            description=(
                "PRIMARY — Full reverse (RVP) load distribution config. " + _ld_filter_hint
            ),
            args_schema=SearchLoadDistributionInput,
        ),
        StructuredTool.from_function(
            coroutine=get_load_distribution_filter_config,
            name="get_load_distribution_filter_config",
            description=(
                "PRIMARY — Full per-filter load distribution for a Filter rule (requires filter_id). "
                + _ld_filter_hint
            ),
            args_schema=GetLoadDistributionFilterConfigInput,
        ),
    ]
