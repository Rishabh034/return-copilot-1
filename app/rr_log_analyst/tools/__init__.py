from __future__ import annotations

from langchain_core.tools import BaseTool

from app.rr_log_analyst.tools import (
    accounts,
    audit_logs,
    carrier_exception,
    check_serviceability,
    courier_partners,
    flow_config,
    load_distribution,
    order_creation_logs,
    recommendation,
    result_query,
    rr_logs,
    rules,
)
from app.rr_log_analyst.tools.context import ToolContext

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
    result_query,
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