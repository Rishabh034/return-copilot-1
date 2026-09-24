from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agents.example.tools import echo
from app.core.tool_context import ToolContext

# Order matters: the tools array sits inside the cached prompt prefix, so
# reordering it invalidates the prompt cache for every caller. Append new
# modules rather than inserting them.
#
# An agent that turns the scratchpad on should also include
# `app.core.runtime.tools.scratchpad` here, which supplies inspect_evidence and
# inspect_source_segment. The platform owns those tools; the agent chooses
# where in this list they sit.
_TOOL_MODULES = (echo,)


def create_tools(context: ToolContext) -> list[BaseTool]:
    tools: list[BaseTool] = []
    for module in _TOOL_MODULES:
        tools.extend(module.build_tools(context))
    return tools


__all__ = ["create_tools"]
