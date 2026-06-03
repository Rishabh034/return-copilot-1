from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import anthropic

from app.config import settings
from app.rr_log_analyst.services.context_assembler import build_context
from app.rr_log_analyst.services.tool_executor import execute_tool
from app.rr_log_analyst.tools import ToolContext, get_claude_tool_schemas

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        clickpost_client: Any,
        anthropic_client: anthropic.AsyncAnthropic,
    ):
        self.clickpost_client = clickpost_client
        self.anthropic = anthropic_client

    async def handle_message_stream(
        self,
        user_text: str,
        system_prompt: str,
        model: str,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Main agentic loop. Yields (event_type, data) tuples for SSE."""

        messages: list[dict] = [
            {"role": "user", "content": user_text},
        ]

        tool_schemas = get_claude_tool_schemas()
        tool_context = ToolContext(
            enterprise_id=1,
            clickpost_client=self.clickpost_client,
        )

        for iteration in range(settings.rr_analyst_max_iterations):
            api_messages = build_context(messages, system_prompt)

            is_last = iteration == settings.rr_analyst_max_iterations - 1
            current_tools = None if is_last else (tool_schemas or None)

            if is_last:
                api_messages.append({
                    "role": "user",
                    "content": "You've used all available iterations. Provide your best final answer now.",
                })

            try:
                create_kwargs: dict[str, Any] = dict(
                    model=model,
                    system=system_prompt,
                    messages=api_messages,
                    max_tokens=settings.rr_analyst_max_output_tokens,
                )
                if current_tools:
                    create_kwargs["tools"] = current_tools

                text_parts: list[str] = []
                tool_uses: list[dict] = []
                current_tool_input_json = ""

                async with self.anthropic.messages.stream(**create_kwargs) as stream:
                    async for event in stream:
                        if event.type == "content_block_start":
                            block = event.content_block
                            if block.type == "tool_use":
                                tool_uses.append({
                                    "id": block.id,
                                    "name": block.name,
                                    "input": {},
                                })
                                current_tool_input_json = ""
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                text_parts.append(delta.text)
                                yield ("text_delta", {"delta": delta.text})
                            elif delta.type == "input_json_delta":
                                current_tool_input_json += delta.partial_json
                        elif event.type == "content_block_stop":
                            if tool_uses and current_tool_input_json:
                                try:
                                    tool_uses[-1]["input"] = json.loads(current_tool_input_json)
                                except json.JSONDecodeError:
                                    tool_uses[-1]["input"] = {}
                                current_tool_input_json = ""

            except anthropic.APIError as e:
                logger.exception("Claude API error on iteration %d", iteration)
                yield ("error", {"code": "llm_error", "message": str(e)})
                return

            combined_text = "".join(text_parts) if text_parts else None

            if not tool_uses:
                yield ("done", {})
                return

            assistant_content: list[dict] = []
            if combined_text:
                assistant_content.append({"type": "text", "text": combined_text})
            for tu in tool_uses:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tu["id"],
                    "name": tu["name"],
                    "input": tu["input"],
                })
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results: list[dict] = []
            for tu in tool_uses:
                yield ("tool_start", {"tool": tu["name"], "args": tu["input"]})

                result = await execute_tool(tu["name"], tu["input"], tool_context)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": json.dumps(result),
                })

                summary = _summarize_result(tu["name"], result)
                yield ("tool_result", {"tool": tu["name"], "summary": summary})

            messages.append({"role": "user", "content": tool_results})

        yield ("error", {"code": "max_iterations", "message": "Maximum iterations reached"})


def _summarize_result(tool_name: str, result: dict) -> str:
    if "error" in result:
        return f"Error: {result['error']}"
    if "count" in result:
        return f"Returned {result['count']} results"
    if "total_filters" in result:
        return f"{result['total_filters']} filters, {result['total_ranked']} ranked couriers"
    if "total_rules" in result:
        return f"Found {result['total_rules']} rules"
    if "total_matching" in result:
        return f"Found {result['total_matching']} matching rules"
    return "Completed"
