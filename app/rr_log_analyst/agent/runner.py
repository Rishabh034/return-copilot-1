from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool

from app.clients.clickpost import ClickpostAPIError, ClickpostPermissionError
from app.clients.llm import create_chat_model
from app.config import settings
from app.rr_log_analyst.agent.context import merge_usage, prepare_messages, serialize_usage
from app.rr_log_analyst.tools import ToolContext, create_tools

logger = logging.getLogger(__name__)


def _extract_text_delta(chunk_content: str | list) -> str | None:
    if isinstance(chunk_content, str):
        return chunk_content or None
    if isinstance(chunk_content, list):
        parts = [
            block.get("text", "")
            for block in chunk_content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = "".join(parts)
        return joined or None
    return None


def _summarize_result(tool_name: str, result: dict) -> str:
    if "error" in result:
        return f"Error: {result['error']}"
    if "count" in result:
        return f"Returned {result['count']} results"
    if "total_ranked" in result and "filters_ran" in result:
        rule = result.get("matched_filter_rule")
        rule_hint = f", filter={rule}" if rule else ""
        return f"{result['total_ranked']} ranked couriers{rule_hint}"
    if "total_rules" in result:
        return f"Found {result['total_rules']} rules"
    if "total_matching" in result:
        rules = result.get("rules") or []
        if rules:
            names = ", ".join(r.get("name", "unknown") for r in rules[:3])
            suffix = "..." if len(rules) > 3 else ""
            return f"Found {result['total_matching']} matching rules: {names}{suffix}"
        return f"Found {result['total_matching']} matching rules"
    if "rule" in result:
        rule = result["rule"]
        return f"Found rule: {rule.get('name', 'unknown')}"
    if "flow_summary" in result:
        return result["flow_summary"][:200]
    if "results" in result and "count" in result:
        first = result["results"][0] if result["results"] else {}
        ranked = first.get("total_ranked", 0)
        return f"{result['count']} order(s), top result has {ranked} ranked couriers"
    return "Completed"


async def _invoke_tool(tool: BaseTool, args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await asyncio.wait_for(
            tool.ainvoke(args),
            timeout=settings.rr_analyst_tool_timeout,
        )
        if isinstance(result, dict):
            return result
        return {"result": result}
    except asyncio.TimeoutError:
        logger.error("Tool %s timed out after %ds", tool.name, settings.rr_analyst_tool_timeout)
        return {"error": f"Tool {tool.name} timed out after {settings.rr_analyst_tool_timeout}s"}
    except ClickpostPermissionError as e:
        logger.warning("Tool %s permission denied: %s", tool.name, e)
        return {"error": str(e), "code": "permission_denied"}
    except ClickpostAPIError as e:
        logger.warning("Tool %s Clickpost error: %s", tool.name, e)
        return {"error": str(e), "code": "clickpost_error"}
    except Exception as e:
        logger.exception("Tool %s failed", tool.name)
        return {"error": f"Tool {tool.name} failed: {str(e)}"}


class AgentRunner:
    def __init__(self, clickpost_client: Any):
        self.clickpost_client = clickpost_client

    async def handle_message_stream(
        self,
        messages: list[BaseMessage],
        system_prompt: str,
        model: str,
        token: str,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Agentic loop using ChatAnthropic.bind_tools(). Yields SSE event tuples."""

        tool_context = ToolContext(token=token, clickpost_client=self.clickpost_client)
        tools = create_tools(tool_context)
        tools_by_name = {tool.name: tool for tool in tools}
        llm = create_chat_model(model)
        total_usage: dict[str, int] | None = None
        usage_by_iteration: list[dict[str, int]] = []

        for iteration in range(settings.rr_analyst_max_iterations):
            trimmed = prepare_messages(messages, system_prompt)
            is_last = iteration == settings.rr_analyst_max_iterations - 1

            if is_last:
                trimmed = [
                    *trimmed,
                    HumanMessage(
                        content="You've used all available iterations. Provide your best final answer now."
                    ),
                ]

            llm_input: list[BaseMessage] = [SystemMessage(content=system_prompt), *trimmed]
            runnable = llm if is_last else llm.bind_tools(tools)
            accumulated: AIMessageChunk | None = None

            try:
                async for chunk in runnable.astream(llm_input):
                    text_delta = _extract_text_delta(chunk.content)
                    if text_delta:
                        yield ("text_delta", {"delta": text_delta})
                    accumulated = chunk if accumulated is None else accumulated + chunk
            except Exception as e:
                logger.exception("Claude API error on iteration %d", iteration)
                yield ("error", {"code": "llm_error", "message": str(e)})
                return

            if accumulated is None:
                yield ("error", {"code": "llm_error", "message": "Empty model response"})
                return

            iteration_usage = serialize_usage(accumulated.usage_metadata)
            if iteration_usage:
                usage_by_iteration.append(iteration_usage)
                total_usage = merge_usage(total_usage, iteration_usage)
                logger.info(
                    "Claude usage iteration %d: input=%d output=%d total=%d",
                    iteration + 1,
                    iteration_usage["input_tokens"],
                    iteration_usage["output_tokens"],
                    iteration_usage["total_tokens"],
                )

            ai_message = AIMessage(
                content=accumulated.content,
                tool_calls=accumulated.tool_calls,
                usage_metadata=accumulated.usage_metadata,
            )

            if not accumulated.tool_calls:
                messages.append(ai_message)
                yield (
                    "done",
                    {
                        "messages": messages,
                        "usage": total_usage,
                        "usage_by_iteration": usage_by_iteration,
                    },
                )
                return

            messages.append(ai_message)

            for tool_call in accumulated.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args") or {}
                tool_id = tool_call["id"]

                yield ("tool_start", {"tool": tool_name, "args": tool_args})

                tool = tools_by_name.get(tool_name)
                if tool is None:
                    result = {"error": f"Unknown tool: {tool_name}"}
                else:
                    result = await _invoke_tool(tool, tool_args)

                messages.append(
                    ToolMessage(
                        content=json.dumps(result),
                        tool_call_id=tool_id,
                        name=tool_name,
                    )
                )

                summary = _summarize_result(tool_name, result)
                yield ("tool_result", {"tool": tool_name, "summary": summary})

        yield ("error", {"code": "max_iterations", "message": "Maximum iterations reached"})
