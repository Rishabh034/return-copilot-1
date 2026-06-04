from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.clients.clickpost import ClickpostAPIError, ClickpostPermissionError
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
    except ClickpostPermissionError as e:
        # The token is valid but lacks the dashboard permission this endpoint
        # requires. Tell the agent clearly so it can relay it instead of retrying.
        logger.warning("Tool %s permission denied: %s", tool_name, e)
        return {"error": str(e), "code": "permission_denied"}
    except ClickpostAPIError as e:
        logger.warning("Tool %s Clickpost error: %s", tool_name, e)
        return {"error": str(e), "code": "clickpost_error"}
    except Exception as e:
        logger.exception("Tool %s failed", tool_name)
        return {"error": f"Tool {tool_name} failed: {str(e)}"}
