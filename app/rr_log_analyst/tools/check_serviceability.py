from __future__ import annotations

from typing import Any, Self

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator

from app.rr_log_analyst.tools._common import as_list
from app.rr_log_analyst.tools.context import ToolContext


class ServiceabilityFilters(BaseModel):
    order_type: str | None = Field(
        default=None,
        description="Optional filter: PREPAID or COD.",
    )
    account_type_id: int | None = Field(
        default=None,
        description="Optional filter: match a specific account ID from RR logs.",
    )
    account_name: str | None = Field(
        default=None,
        description="Optional filter: match account name (case-insensitive).",
    )
    courier_partner: str | None = Field(
        default=None,
        description="Optional filter: match courier partner name (case-insensitive).",
    )


class CheckRouteServiceabilityInput(ServiceabilityFilters):
    pickup_pincode: str | None = Field(
        default=None,
        description=(
            "6-digit pickup pincode. Provide this, drop_pincode, or both. "
            "Pickup-only → pickup serviceability. Drop-only → drop serviceability. "
            "Both → route intersection."
        ),
        min_length=6,
        max_length=6,
    )
    drop_pincode: str | None = Field(
        default=None,
        description=(
            "6-digit drop pincode. Provide this, pickup_pincode, or both. "
            "Drop-only → drop serviceability. Pickup-only → pickup serviceability. "
            "Both → route intersection."
        ),
        min_length=6,
        max_length=6,
    )
    is_rvp: bool = Field(
        default=False,
        description="True for reverse (RVP) orders, False for forward orders.",
    )

    @model_validator(mode="after")
    def _require_at_least_one_pincode(self) -> Self:
        pickup = (self.pickup_pincode or "").strip() or None
        drop = (self.drop_pincode or "").strip() or None
        self.pickup_pincode = pickup
        self.drop_pincode = drop
        if not pickup and not drop:
            raise ValueError("Provide pickup_pincode, drop_pincode, or both")
        return self


_ORDER_TYPE_KEYS = (
    "pincode_serviceability__order_type",
    "pickup_pincode_serviceability__order_type",
)


def _extract_order_type(row: dict[str, Any]) -> str | None:
    for key in _ORDER_TYPE_KEYS:
        value = row.get(key)
        if value is not None and value != "":
            return str(value).strip().upper()
    return None


def _shape_serviceability_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": row.get("account_type__id"),
        "account_name": row.get("account_type__account_name"),
        "courier_partner": row.get("account_type__courier_partner__name"),
        "order_type": _extract_order_type(row),
    }


def _parse_serviceability_response(raw: Any) -> list[dict[str, Any]]:
    return [_shape_serviceability_row(row) for row in as_list(raw) if isinstance(row, dict)]


def _intersect_accounts(
    pickup_accounts: list[dict[str, Any]],
    drop_accounts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Cross-product style intersection.

    Pickup is treated as an account-id set (order_type on pickup is often only
    PREPAID). For every account present on both sides, keep all drop rows —
    including COD even when pickup only listed PREPAID.
    """
    pickup_ids = {
        row.get("account_id")
        for row in pickup_accounts
        if row.get("account_id") is not None
    }
    seen: set[tuple[Any, Any]] = set()
    intersection: list[dict[str, Any]] = []
    for row in drop_accounts:
        account_id = row.get("account_id")
        order_type = row.get("order_type")
        if account_id not in pickup_ids:
            continue
        key = (account_id, order_type)
        if key in seen:
            continue
        seen.add(key)
        intersection.append(row)
    return intersection


def _filters_applied(filters: ServiceabilityFilters) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "order_type": filters.order_type,
            "account_type_id": filters.account_type_id,
            "account_name": filters.account_name,
            "courier_partner": filters.courier_partner,
        }.items()
        if value is not None and value != ""
    }


def _apply_filters(
    accounts: list[dict[str, Any]],
    filters: ServiceabilityFilters,
) -> list[dict[str, Any]]:
    filtered = accounts
    if filters.order_type:
        wanted = filters.order_type.strip().upper()
        filtered = [
            row
            for row in filtered
            if str(row.get("order_type") or "").upper() == wanted
        ]
    if filters.account_type_id is not None:
        filtered = [row for row in filtered if row.get("account_id") == filters.account_type_id]
    if filters.account_name:
        wanted = filters.account_name.strip().casefold()
        filtered = [
            row
            for row in filtered
            if wanted in str(row.get("account_name") or "").casefold()
        ]
    if filters.courier_partner:
        wanted = filters.courier_partner.strip().casefold()
        filtered = [
            row
            for row in filtered
            if wanted in str(row.get("courier_partner") or "").casefold()
        ]
    return filtered


async def _fetch_serviceable_accounts(
    context: ToolContext,
    *,
    pincode: str,
    is_pickup_pincode: bool,
    is_rvp: bool,
) -> list[dict[str, Any]]:
    raw = await context.clickpost_client.check_serviceability(
        auth=context.auth,
        pincode=pincode,
        is_pickup_pincode=is_pickup_pincode,
        is_rvp=is_rvp,
    )
    return _parse_serviceability_response(raw)


def _with_filter_meta(
    result: dict[str, Any],
    accounts: list[dict[str, Any]],
    filters: dict[str, Any],
) -> dict[str, Any]:
    if filters:
        result["filters_applied"] = filters
        result["serviceable"] = len(accounts) > 0
    return result


async def _check_route_serviceability(
    context: ToolContext,
    input: CheckRouteServiceabilityInput,
) -> dict[str, Any]:
    filters = _filters_applied(input)
    pickup = input.pickup_pincode
    drop = input.drop_pincode

    # Single side: pickup only
    if pickup and not drop:
        accounts = _apply_filters(
            await _fetch_serviceable_accounts(
                context,
                pincode=pickup,
                is_pickup_pincode=True,
                is_rvp=input.is_rvp,
            ),
            input,
        )
        return _with_filter_meta(
            {
                "mode": "pickup_only",
                "pickup_pincode": pickup,
                "is_rvp": input.is_rvp,
                "count": len(accounts),
                "accounts": accounts,
            },
            accounts,
            filters,
        )

    # Single side: drop only
    if drop and not pickup:
        accounts = _apply_filters(
            await _fetch_serviceable_accounts(
                context,
                pincode=drop,
                is_pickup_pincode=False,
                is_rvp=input.is_rvp,
            ),
            input,
        )
        return _with_filter_meta(
            {
                "mode": "drop_only",
                "drop_pincode": drop,
                "is_rvp": input.is_rvp,
                "count": len(accounts),
                "accounts": accounts,
            },
            accounts,
            filters,
        )

    # Both sides: route intersection
    assert pickup is not None and drop is not None
    pickup_accounts = await _fetch_serviceable_accounts(
        context,
        pincode=pickup,
        is_pickup_pincode=True,
        is_rvp=input.is_rvp,
    )
    drop_accounts = await _fetch_serviceable_accounts(
        context,
        pincode=drop,
        is_pickup_pincode=False,
        is_rvp=input.is_rvp,
    )
    accounts = _apply_filters(
        _intersect_accounts(pickup_accounts, drop_accounts),
        input,
    )
    return _with_filter_meta(
        {
            "mode": "route",
            "pickup_pincode": pickup,
            "drop_pincode": drop,
            "is_rvp": input.is_rvp,
            "pickup_count": len(pickup_accounts),
            "drop_count": len(drop_accounts),
            "count": len(accounts),
            "accounts": accounts,
        },
        accounts,
        filters,
    )


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def check_route_serviceability(**kwargs: Any) -> dict[str, Any]:
        return await _check_route_serviceability(
            context, CheckRouteServiceabilityInput(**kwargs)
        )

    return [
        StructuredTool.from_function(
            coroutine=check_route_serviceability,
            name="check_route_serviceability",
            description=(
                "PRIMARY — Check which carrier accounts can service a pickup and/or drop pincode. "
                "Pass pickup_pincode only, drop_pincode only, or both. "
                "Pickup-only / drop-only → one API call for that side. "
                "Both → two calls (pickup + drop); an account is route-serviceable if it appears "
                "on pickup, and all drop order_types (PREPAID/COD) for that account are kept "
                "(pickup often only lists PREPAID). "
                "Optional filters: order_type, account_type_id, account_name, courier_partner. "
                "Never use run_recommendation for serviceability-only questions."
            ),
            args_schema=CheckRouteServiceabilityInput,
        ),
    ]
