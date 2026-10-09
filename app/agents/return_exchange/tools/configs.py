from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agents.return_exchange.tools._common import CONFIG_GROUP, rne_client, token
from app.core.runtime.policy import ToolPolicy, with_policy
from app.core.tool_context import ToolContext

_CONFIG = ToolPolicy(group=CONFIG_GROUP)
_ALONE = "Do not combine with get_enterprise_return_exchange_configs."

# (tool name, client path, when to use it)
_SIMPLE = (
    ("get_return_window_config", "return_window", f"Return window only. {_ALONE}"),
    ("get_exchange_window_config", "exchange_window", f"Exchange window only. {_ALONE}"),
    ("get_return_refund_config", "refund_config", f"Refund mode, store credit, or gift card only. {_ALONE}"),
    ("get_rejection_config", "rejection_config", f"Rejection or non-returnable rules only. {_ALONE}"),
    ("get_warehouse_config", "warehouse_config", f"Warehouse settings only. {_ALONE}"),
    ("get_qc_door_step_config", "qc_config", f"QC or doorstep settings only. {_ALONE}"),
)


class EmptyInput(BaseModel):
    pass


class EnterpriseConfigsInput(BaseModel):
    domain: str = Field(description="Enterprise domain.")


class CatalogueInput(BaseModel):
    domain: str = Field(description="Enterprise domain. Required.")
    category_id: str | None = Field(default=None, description="Optional category filter.")
    search: str | None = Field(default=None, description="Optional product search.")
    page: int | None = Field(default=None, description="Optional page number.")


def _tool(coroutine, name: str, description: str, schema, *, grouped: bool = False):
    tool = StructuredTool.from_function(
        coroutine=coroutine, name=name, description=description, args_schema=schema
    )
    return with_policy(tool, _CONFIG) if grouped else tool


def build_tools(context: ToolContext) -> list[StructuredTool]:
    client = rne_client(context)
    auth_token = token(context)

    async def get_enterprise_return_exchange_configs(domain: str) -> dict[str, Any]:
        raw = await client.get("enterprise_configs", auth_token, domain=domain)
        return {"query": {"domain": domain}, "result": raw}

    def _simple(path_key: str):
        async def call() -> dict[str, Any]:
            return {"result": await client.get(path_key, auth_token)}

        return call

    async def get_exchange_catalogue(
        domain: str,
        category_id: str | None = None,
        search: str | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        params = {
            key: value
            for key, value in {"category_id": category_id, "search": search, "page": page}.items()
            if value is not None
        }
        raw = await client.get("catalogue", auth_token, domain=domain, **params)
        return {"query": {"domain": domain, **params}, "result": raw}

    tools = [
        _tool(
            get_enterprise_return_exchange_configs,
            "get_enterprise_return_exchange_configs",
            "PRIMARY for a broad 'what settings are on?' question. Requires domain. "
            "Do not also call a specific window, refund, rejection, warehouse, or QC tool.",
            EnterpriseConfigsInput,
            grouped=True,
        )
    ]
    for name, path_key, description in _SIMPLE:
        tools.append(_tool(_simple(path_key), name, description, EmptyInput, grouped=True))
    tools.append(
        _tool(
            get_exchange_catalogue,
            "get_exchange_catalogue",
            "Browse exchange products for a domain. Narrow with category_id, search, or page.",
            CatalogueInput,
        )
    )
    return tools
