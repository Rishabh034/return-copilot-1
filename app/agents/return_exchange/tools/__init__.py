from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agents.return_exchange.tools import analytics, configs, request_lookup
from app.core.runtime.tools import scratchpad as scratchpad_tools
from app.core.tool_context import ToolContext

# Order is part of the cached prompt prefix. Append; do not insert.
TOOL_MODULES = (
    request_lookup,
    configs,
    analytics,
    scratchpad_tools,
)


def create_tools(context: ToolContext) -> list[BaseTool]:
    tools: list[BaseTool] = []
    for module in TOOL_MODULES:
        tools.extend(module.build_tools(context))
    return tools


__all__ = ["create_tools"]
