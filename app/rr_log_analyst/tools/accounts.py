from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import as_list
from app.rr_log_analyst.tools.context import ToolContext


class GetAccountInfoInput(BaseModel):
    query: str = Field(
        default="",
        description=(
            "Substring to match against account_id, account_code, cp_name or cpid. "
            "Omit to return all accounts."
        ),
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


async def _get_account_info(context: ToolContext, query: str = "") -> dict[str, Any]:
    raw = await context.clickpost_client.list_accounts(token=context.token)
    accounts = [_shape_account(a) for a in as_list(raw)]

    query = query.strip()
    if query:
        accounts = [a for a in accounts if _matches(a, query)]

    return {"count": len(accounts), "accounts": accounts}


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def get_account_info(query: str = "") -> dict[str, Any]:
        return await _get_account_info(context, query)

    return [
        StructuredTool.from_function(
            coroutine=get_account_info,
            name="get_account_info",
            description=(
                "Look up carrier account metadata for the enterprise. Resolves between the different "
                "identifiers: account_id (Clickpost internal id), account_code (brand contract name, e.g. "
                "'Delhivery_Maratahalli'), cp_name (carrier brand, e.g. 'Delhivery') and cpid. Pass a "
                "`query` to filter by any of these (e.g. a carrier name to find its account_ids), or omit "
                "it to list all accounts. Use this to get an account_id before calling find_rules_for_carrier."
            ),
            args_schema=GetAccountInfoInput,
        ),
    ]
