from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.return_exchange.tools.common import CONFIG_GROUP, rne_client, token
from app.core.runtime.policy import ToolPolicy, with_policy
from app.core.tool_context import ToolContext

CONFIG = ToolPolicy(group=CONFIG_GROUP)
ALONE = "Do not combine with get_enterprise_return_exchange_configs."

# (tool name, client path, when to use it)
SIMPLE = (
    ("get_return_window_config", "return_window", f"Return window only. {ALONE}"),
    ("get_exchange_window_config", "exchange_window", f"Exchange window only. {ALONE}"),
    ("get_return_refund_config", "refund_config", f"Refund mode, store credit, or gift card only. {ALONE}"),
    ("get_rejection_config", "rejection_config", f"Rejection or non-returnable rules only. {ALONE}"),
    ("get_warehouse_config", "warehouse_config", f"Warehouse settings only. {ALONE}"),
    ("get_qc_door_step_config", "qc_config", f"QC or doorstep settings only. {ALONE}"),
)


class EmptyInput(BaseModel):
    pass


class EnterpriseConfigsInput(BaseModel):
    domain: str | None = Field(
        default=None,
        description=(
            "Optional returns-portal domain. Omit this — the caller's token already "
            "selects the enterprise. Pass a domain only if the user typed one."
        ),
    )


class CatalogueInput(BaseModel):
    domain: str | None = Field(
        default=None,
        description="Optional. Omit unless the user typed a returns-portal domain.",
    )
    category_id: str | None = Field(default=None, description="Optional category filter.")
    search: str | None = Field(default=None, description="Optional product search.")
    page: int | None = Field(default=None, description="Optional page number.")


def tool(coroutine, name: str, description: str, schema, *, grouped: bool = False):
    tool = StructuredTool.from_function(
        coroutine=coroutine, name=name, description=description, args_schema=schema
    )
    return with_policy(tool, CONFIG) if grouped else tool


def build_tools(context: ToolContext) -> list[StructuredTool]:
    client = rne_client(context)
    auth_token = token(context)

    async def get_enterprise_return_exchange_configs(domain: str | None = None) -> dict[str, Any]:
        domain_clean = (domain or "").strip() or None
        names = (
            "return_window",
            "exchange_window",
            "refund_config",
            "rejection_config",
            "warehouse_config",
            "qc_config",
        )
        fetched = await asyncio.gather(
            *(client.get(name, auth_token) for name in names),
            return_exceptions=True,
        )
        result: dict[str, Any] = {}
        errors = 0
        for name, value in zip(names, fetched):
            if isinstance(value, Exception):
                result[name] = {"error": str(value)}
                errors += 1
            else:
                result[name] = value
        if errors < len(names):
            return {
                "query": {"source": "token_enterprise"},
                "result": result,
                "note": "Loaded via the caller's token. Do not ask for a domain.",
            }
        if not domain_clean:
            return {
                "query": {"source": "token_enterprise"},
                "result": result,
                "error": "Token-scoped config APIs failed. Do not ask the user for a domain.",
            }
        raw = await client.get("enterprise_configs", auth_token, domain=domain_clean)
        return {"query": {"domain": domain_clean, "source": "domain_lookup"}, "result": raw}

    def simple(path_key: str):
        async def call() -> dict[str, Any]:
            return {"result": await client.get(path_key, auth_token)}

        return call

    async def get_exchange_catalogue(
        domain: str | None = None,
        category_id: str | None = None,
        search: str | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        params = {
            key: value
            for key, value in {
                "domain": (domain or "").strip() or None,
                "category_id": category_id,
                "search": search,
                "page": page,
            }.items()
            if value is not None
        }
        raw = await client.get("catalogue", auth_token, **params)
        return {"query": params, "result": raw}

    tools = [
        tool(
            get_enterprise_return_exchange_configs,
            "get_enterprise_return_exchange_configs",
            "PRIMARY for a broad 'what settings are on?' question. The caller's token "
            "selects the enterprise — do not invent or ask for a domain. "
            "Do not also call a specific window, refund, rejection, warehouse, or QC tool.",
            EnterpriseConfigsInput,
            grouped=True,
        )
    ]
    for name, path_key, description in SIMPLE:
        tools.append(tool(simple(path_key), name, description, EmptyInput, grouped=True))
    tools.append(
        tool(
            get_exchange_catalogue,
            "get_exchange_catalogue",
            "Browse exchange products. The token selects the enterprise. "
            "Pass domain only if the user typed one. Narrow with category_id, search, or page.",
            CatalogueInput,
        )
    )
    return tools
