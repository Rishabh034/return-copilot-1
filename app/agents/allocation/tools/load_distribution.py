from __future__ import annotations

from typing import Any, Literal, Self

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator

from app.agents.allocation.tools._common import as_list, cached_fetch
from app.core.tool_context import ToolContext

LoadDistributionMode = Literal["forward", "reverse", "filter"]


class GetLoadDistributionConfigInput(BaseModel):
    mode: LoadDistributionMode = Field(
        description=(
            "Which LD config to fetch: "
            "'forward' = terminal forward account-level, "
            "'reverse' = terminal RVP account-level, "
            "'filter' = per-filter lane LD (requires filter_id)."
        ),
    )
    filter_id: int | None = Field(
        default=None,
        description=(
            "Required when mode='filter'. Filter rule id from find_rule / list_rules / "
            "matched filter in an RR log."
        ),
    )
    account_id: int | None = Field(
        default=None,
        description="Optional — return only this carrier's LD entry.",
    )

    @model_validator(mode="after")
    def _require_filter_id_for_filter_mode(self) -> Self:
        if self.mode == "filter" and self.filter_id is None:
            raise ValueError("filter_id is required when mode='filter'")
        return self


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


async def _fetch_accounts(context: ToolContext) -> dict[int, dict]:
    # Every load-distribution call needs this purely to enrich entries with account
    # codes, and get_account_info fetches the same payload — share one download.
    raw = await cached_fetch(
        context,
        ("accounts",),
        lambda: context.clickpost_client.list_accounts(auth=context.auth),
    )
    return _account_lookup(as_list(raw))


async def _get_load_distribution_config(
    context: ToolContext,
    account_id: int | None = None,
) -> dict[str, Any]:
    raw = await cached_fetch(
        context,
        ("ld_config", "forward"),
        lambda: context.clickpost_client.get_load_distribution_config(auth=context.auth),
    )
    lookup = await _fetch_accounts(context)
    entries = [_shape_forward_entry(e, lookup) for e in as_list(raw)]

    if account_id is not None:
        matches = [e for e in entries if e.get("account_id") == account_id]
        return {
            "mode": "forward",
            "delivery_type": "FORWARD",
            "config_type": "account_level",
            "filters_applied": {"account_id": account_id},
            "total_matching": len(matches),
            "entries": matches,
        }

    enabled = [e for e in entries if e.get("enabled")]
    return {
        "mode": "forward",
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
    raw = await cached_fetch(
        context,
        ("ld_config", "reverse"),
        lambda: context.clickpost_client.get_load_distribution_reverse_config(auth=context.auth),
    )
    lookup = await _fetch_accounts(context)
    entries = [_shape_forward_entry(e, lookup) for e in as_list(raw)]

    if account_id is not None:
        matches = [e for e in entries if e.get("account_id") == account_id]
        return {
            "mode": "reverse",
            "delivery_type": "RVP",
            "config_type": "account_level",
            "filters_applied": {"account_id": account_id},
            "total_matching": len(matches),
            "entries": matches,
        }

    enabled = [e for e in entries if e.get("enabled")]
    return {
        "mode": "reverse",
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
    raw = await cached_fetch(
        context,
        ("ld_config", "filter", filter_id),
        lambda: context.clickpost_client.get_load_distribution_filter_config(
            auth=context.auth,
            filter_id=filter_id,
        ),
    )
    lookup = await _fetch_accounts(context)
    entries = [_shape_filter_entry(e, lookup) for e in as_list(raw)]

    if account_id is not None:
        matches = [e for e in entries if e.get("account_id") == account_id]
        return {
            "mode": "filter",
            "filter_id": filter_id,
            "config_type": "filter_level",
            "filters_applied": {"account_id": account_id},
            "total_matching": len(matches),
            "entries": matches,
        }

    return {
        "mode": "filter",
        "filter_id": filter_id,
        "config_type": "filter_level",
        "total_entries": len(entries),
        "entries": entries,
        "hint": (
            "allocation_value meaning depends on the filter's allocation_type: PERCENTAGE or "
            "ABSOLUTE_CAPPING. Pass account_id to filter to one carrier."
        ),
    }


async def _get_load_distribution_unified(
    context: ToolContext,
    input: GetLoadDistributionConfigInput,
) -> dict[str, Any]:
    if input.mode == "forward":
        return await _get_load_distribution_config(context, input.account_id)
    if input.mode == "reverse":
        return await _get_load_distribution_reverse_config(context, input.account_id)
    assert input.filter_id is not None
    return await _get_load_distribution_filter_config(
        context, input.filter_id, input.account_id
    )


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def get_load_distribution_config(**kwargs: Any) -> dict[str, Any]:
        return await _get_load_distribution_unified(
            context, GetLoadDistributionConfigInput(**kwargs)
        )

    return [
        StructuredTool.from_function(
            coroutine=get_load_distribution_config,
            name="get_load_distribution_config",
            description=(
                "PRIMARY — Load distribution config. Set mode='forward' (terminal forward), "
                "'reverse' (terminal RVP), or 'filter' (per-filter lane LD; requires filter_id). "
                "Optional account_id filters to one carrier. Prefer RR-log "
                "load_distribution_* fields for a specific order when present; use this for "
                "dashboard targets or older logs missing those fields. "
                "Call get_enterprise_flow_config first for enterprise-wide LD toggles."
            ),
            args_schema=GetLoadDistributionConfigInput,
        ),
    ]
