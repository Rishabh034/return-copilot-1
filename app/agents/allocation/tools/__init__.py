from __future__ import annotations

from langchain_core.tools import BaseTool

from app.agents.allocation.tools import (
    accounts,
    audit_logs,
    carrier_exception,
    check_serviceability,
    courier_partners,
    flow_config,
    load_distribution,
    order_creation_logs,
    recommendation,
    rr_logs,
    rules,
)
from app.core.runtime.tools import scratchpad as scratchpad_tools
from app.core.tool_context import ToolContext

# Order is part of the contract, not a detail. The tools array sits inside the
# cached prompt prefix, so reordering it silently invalidates the prompt cache
# for every caller — a cost change no functional test would notice.
#
# `scratchpad_tools` is owned by the platform (every agent that turns the
# scratchpad on gets the same inspect_evidence / inspect_source_segment), but
# the agent still chooses *where* it sits, which is what keeps this list stable
# across the move out of app/core/.
_TOOL_MODULES = (
    accounts,
    audit_logs,
    carrier_exception,
    check_serviceability,
    courier_partners,
    flow_config,
    load_distribution,
    order_creation_logs,
    recommendation,
    scratchpad_tools,
    rr_logs,
    rules,
)


def create_tools(context: ToolContext) -> list[BaseTool]:
    """Build all LangChain StructuredTools bound to the per-request Clickpost context."""
    tools: list[BaseTool] = []
    for module in _TOOL_MODULES:
        tools.extend(module.build_tools(context))
    return tools


__all__ = ["ToolContext", "create_tools"]
