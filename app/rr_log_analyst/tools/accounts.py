from __future__ import annotations

from typing import Any

from app.rr_log_analyst.tools import ToolContext, register
from app.rr_log_analyst.tools._common import as_list


def _shape_account(acc: dict) -> dict:
    """Pick the identifying fields of a carrier account.

    See knowledge/02_account_structure.md: account_id is Clickpost's internal id
    for a specific account, account_code is the brand's contract name, cp_name is
    the carrier brand, and cpid is the carrier grouping.
    """
    # The courier_accounts endpoint names these `courier_partenr_name` (sic) and
    # `courier_partner_pk`; accept those alongside our internal names.
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


@register
class GetAccountInfoTool:
    name = "get_account_info"
    description = (
        "Look up carrier account metadata for the enterprise. Resolves between the different "
        "identifiers: account_id (Clickpost internal id), account_code (brand contract name, e.g. "
        "'Delhivery_Maratahalli'), cp_name (carrier brand, e.g. 'Delhivery') and cpid. Pass a "
        "`query` to filter by any of these (e.g. a carrier name to find its account_ids), or omit "
        "it to list all accounts. Use this to get an account_id before calling find_rules_for_carrier."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Substring to match against account_id, account_code, cp_name or cpid. "
                    "Omit to return all accounts."
                ),
            }
        },
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        raw = await context.clickpost_client.list_accounts(token=context.token)
        accounts = [_shape_account(a) for a in as_list(raw)]

        query = (args.get("query") or "").strip()
        if query:
            accounts = [a for a in accounts if _matches(a, query)]

        return {"count": len(accounts), "accounts": accounts}
