from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import as_list
from app.rr_log_analyst.tools.context import ToolContext
from app.rr_log_analyst.tools.flow_config import EmptyInput


class GetAccountInfoInput(BaseModel):
    query: str = Field(
        default="",
        description=(
            "Optional substring to match against account_id, account_code, cp_name or cpid. "
            "Omit to return all accounts with full metadata."
        ),
    )


class SearchAccountsInput(BaseModel):
    query: str = Field(
        description="Substring to match against account_id, account_code, cp_name or cpid.",
    )


def _shape_account(acc: dict) -> dict:
    return {
        "account_id": acc.get("account_id") or acc.get("id"),
        "account_code": acc.get("account_code") or acc.get("code"),
        "cp_name": acc.get("cp_name") or acc.get("carrier_name") or acc.get("courier_partenr_name"),
        "cpid": acc.get("cpid") or acc.get("courier_partner_pk"),
        "active": acc.get("active") if "active" in acc else acc.get("is_active"),
    }


def _matches(acc: dict, query: str) -> bool:
    q = query.lower()
    for field in ("account_id", "account_code", "cp_name", "cpid"):
        val = acc.get(field)
        if val is not None and q in str(val).lower():
            return True
    return False


async def _fetch_shaped_accounts(context: ToolContext) -> list[dict]:
    raw = await context.clickpost_client.list_accounts(token=context.token)
    return [_shape_account(a) for a in as_list(raw)]


async def _get_account_info(context: ToolContext, query: str = "") -> dict[str, Any]:
    accounts = await _fetch_shaped_accounts(context)
    query = query.strip()
    if query:
        accounts = [a for a in accounts if _matches(a, query)]
    return {"count": len(accounts), "accounts": accounts}


async def _summarize_accounts(context: ToolContext) -> dict[str, Any]:
    accounts = await _fetch_shaped_accounts(context)
    carriers = sorted(
        {a.get("cp_name") for a in accounts if a.get("cp_name")},
        key=str.lower,
    )
    return {
        "total_accounts": len(accounts),
        "carrier_brands": carriers,
        "hint": "Compact overview. Use get_account_info for full account metadata.",
    }


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def get_account_info(query: str = "") -> dict[str, Any]:
        return await _get_account_info(context, query)

    async def search_accounts(query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            return {"error": "query is required. Use get_account_info() to list all accounts."}
        return await _get_account_info(context, query)

    async def summarize_accounts() -> dict[str, Any]:
        return await _summarize_accounts(context)

    return [
        StructuredTool.from_function(
            coroutine=get_account_info,
            name="get_account_info",
            description=(
                "PRIMARY — Carrier account metadata for the enterprise. Omit query to list all accounts; "
                "pass query to filter by account_id, account_code, cp_name, or cpid. If too large, use "
                "summarize_accounts or search_accounts."
            ),
            args_schema=GetAccountInfoInput,
        ),
        StructuredTool.from_function(
            coroutine=search_accounts,
            name="search_accounts",
            description="SUB-TOOL — Filter accounts by query substring (same as get_account_info with query).",
            args_schema=SearchAccountsInput,
        ),
        StructuredTool.from_function(
            coroutine=summarize_accounts,
            name="summarize_accounts",
            description=(
                "SUB-TOOL — Compact account overview: total count and carrier brand names only."
            ),
            args_schema=EmptyInput,
        ),
    ]
