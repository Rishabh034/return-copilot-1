from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.rr_log_analyst.tools import ToolContext, get_tool

logger = logging.getLogger(__name__)


async def execute_tool(
    tool_name: str,
    args: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    tool = get_tool(tool_name)
    if not tool:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        result = await asyncio.wait_for(
            tool.execute(args, context),
            timeout=settings.rr_analyst_tool_timeout,
        )
        return result
    except asyncio.TimeoutError:
        logger.error("Tool %s timed out after %ds", tool_name, settings.rr_analyst_tool_timeout)
        return {"error": f"Tool {tool_name} timed out after {settings.rr_analyst_tool_timeout}s"}
    except Exception as e:
        logger.exception("Tool %s failed", tool_name)
        return {"error": f"Tool {tool_name} failed: {str(e)}"}
