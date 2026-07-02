from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.tools._common import as_list
from app.rr_log_analyst.tools.context import ToolContext


class LookupCourierPartnerInput(BaseModel):
    cpid: int = Field(description="Courier partner ID (cpId) to look up.")


class SearchCourierPartnersInput(BaseModel):
    query: str = Field(
        description="Substring to match against cpid or carrier partner name.",
    )


def _shape_partner(partner: dict) -> dict:
    return {
        "name": partner.get("name"),
        "cpid": partner.get("cpId") or partner.get("cpid"),
        "country": partner.get("country"),
        "type": partner.get("type"),
    }


async def _fetch_shaped_partners(context: ToolContext) -> list[dict]:
    raw = await context.clickpost_client.list_courier_partners(token=context.token)
    return [_shape_partner(p) for p in as_list(raw)]


def _matches(partner: dict, query: str) -> bool:
    q = query.lower()
    for field in ("cpid", "name"):
        val = partner.get(field)
        if val is not None and q in str(val).lower():
            return True
    return False


async def _lookup_courier_partner(context: ToolContext, cpid: int) -> dict[str, Any]:
    partners = await _fetch_shaped_partners(context)
    for partner in partners:
        if partner.get("cpid") == cpid:
            return {"found": True, "partner": partner}
    return {
        "found": False,
        "cpid": cpid,
        "hint": "No courier partner with this cpId. Try search_courier_partners with a name substring.",
    }


async def _search_courier_partners(context: ToolContext, query: str) -> dict[str, Any]:
    partners = await _fetch_shaped_partners(context)
    query = query.strip()
    if not query:
        return {"error": "query is required. Use lookup_courier_partner when you have the cpId."}
    matched = [p for p in partners if _matches(p, query)]
    return {"count": len(matched), "partners": matched}


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def lookup_courier_partner(cpid: int) -> dict[str, Any]:
        return await _lookup_courier_partner(context, cpid)

    async def search_courier_partners(query: str) -> dict[str, Any]:
        return await _search_courier_partners(context, query)

    return [
        StructuredTool.from_function(
            coroutine=lookup_courier_partner,
            name="lookup_courier_partner",
            description=(
                "PRIMARY — Resolve a courier partner (carrier) by cpId. Returns name, cpid, country, "
                "and type (Forward/Reverse/B2B). Use whenever you have a cpId and need the carrier "
                "name or metadata."
            ),
            args_schema=LookupCourierPartnerInput,
        ),
        StructuredTool.from_function(
            coroutine=search_courier_partners,
            name="search_courier_partners",
            description=(
                "SUB-TOOL — Find courier partners by name or cpid substring when you don't have "
                "the exact cpId."
            ),
            args_schema=SearchCourierPartnersInput,
        ),
    ]
