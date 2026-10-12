from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.return_exchange.tools.common import rne_client, token
from app.core.tool_context import ToolContext


class StatsInput(BaseModel):
    start_date: str = Field(description="Start date, DD-MM-YYYY.")
    end_date: str = Field(description="End date, DD-MM-YYYY.")


class EmptyInput(BaseModel):
    pass


def build_tools(context: ToolContext) -> list[StructuredTool]:
    client = rne_client(context)

    async def get_refund_summary() -> dict[str, Any]:
        return {"result": await client.get("refund_summary", token(context))}

    async def get_rne_overview_return_stats(start_date: str, end_date: str) -> dict[str, Any]:
        raw = await client.get("return_stats", token(context), start_date=start_date, end_date=end_date)
        return {"query": {"start_date": start_date, "end_date": end_date}, "result": raw}

    return [
        StructuredTool.from_function(
            coroutine=get_refund_summary,
            name="get_refund_summary",
            description="Enterprise refund counts. Use for volume, not one case.",
            args_schema=EmptyInput,
        ),
        StructuredTool.from_function(
            coroutine=get_rne_overview_return_stats,
            name="get_rne_overview_return_stats",
            description="Return volume for a date range. Dates are DD-MM-YYYY. Not for one case.",
            args_schema=StatsInput,
        ),
    ]
