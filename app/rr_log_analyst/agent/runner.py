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
from pydantic import ValidationError

from app.clients.clickpost import ClickpostAPIError, ClickpostPermissionError
from app.clients.llm import create_chat_model, create_secondary_chat_model
from app.config import settings
from app.rr_log_analyst.agent.context import merge_usage, prepare_messages, serialize_usage
from app.rr_log_analyst.agent.result_query import (
    build_auto_prefetch_message,
    extract_query_keywords,
)
from app.rr_log_analyst.agent.scratchpad import (
    ScratchpadBuildResult,
    build_compact_envelope,
    fallback_scratchpad,
    prepare_raw_data_parts,
    summarize_raw_data_parts,
)
from app.rr_log_analyst.memory.tool_results import StoredToolResult, tool_result_store
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
    if "top_courier" in result:
        top = result.get("top_courier") or {}
        name = top.get("cp_name") or top.get("account_code") or "unknown"
        return f"Top: {name}, {result.get('total_ranked', 0)} ranked"
    if "stage" in result and "value" in result:
        return f"Pipeline stage {result['stage']}"
    if "ranking" in result and "total_ranked" in result:
        return f"{result.get('total_matching', len(result['ranking']))} of {result['total_ranked']} ranked"
    if "in_final_ranking" in result:
        return f"Courier in final ranking: {result['in_final_ranking']}"
    if "flow_summary" in result and "flags" in result:
        return result["flow_summary"][:200]
    if "flag" in result and "value" in result:
        return f"{result['flag']}={result['value']}"
    if "logs_sample" in result:
        return f"{result.get('count', 0)} audit logs ({result.get('sample_size', 0)} sampled)"
    if "summaries" in result and "count" in result:
        return f"{result['count']} order summaries"
    if "total_rules" in result:
        return f"Found {result['total_rules']} rules (compact summary)"
    if "total_accounts" in result:
        return f"Found {result['total_accounts']} accounts across {len(result.get('carrier_brands') or [])} carriers"
    if "total_entries" in result and "enabled_entries" in result:
        return f"{result['total_entries']} LD entries ({result['enabled_entries']} enabled)"
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
    if "resolved" in result:
        resolved = result["resolved"]
        return (
            f"Resolved enterprise: {resolved.get('name')} "
            f"(id={resolved.get('enterprise_user_profile_id')})"
        )
    if "matches" in result:
        count = result.get("match_count", len(result["matches"]))
        return f"{count} enterprise match(es) — ask user to pick or refine name"
    return "Completed"


def _summarize_scratchpad_envelope(tool_name: str, envelope: dict[str, Any]) -> str:
    text = envelope.get("scratchpad") or ""
    if not text and envelope.get("scratchpad_stored"):
        total = envelope.get("scratchpad_total_chars", 0)
        batches = envelope.get("scratchpad_batches", 0)
        return f"{tool_name}: full scratchpad stored ({total} chars, {batches} batches)"
    preview = text[:200].replace("\n", " ")
    if len(text) > 200:
        preview += "..."
    if preview:
        return f"{tool_name}: {preview}"
    return _summarize_result(tool_name, envelope)


def _build_tool_message_content(
    *,
    tool_call_id: str,
    tool_name: str,
    result: dict[str, Any],
    scratchpad: str,
    scratchpad_batches: list[str],
    raw_data_parts: list[str],
    use_scratchpad: bool,
) -> str:
    if use_scratchpad:
        return json.dumps(
            build_compact_envelope(
                result_id=tool_call_id,
                tool_name=tool_name,
                scratchpad=scratchpad,
                batches=scratchpad_batches,
                raw_data_parts=raw_data_parts,
            )
        )
    return json.dumps(result)


def _latest_user_text(messages: list[BaseMessage]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str) and content.startswith("[Auto-fetched tool result slices"):
                continue
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                text = _extract_text_delta(content)
                return text
    return None


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
    except ValidationError as e:
        logger.warning("Tool %s invalid arguments: %s", tool.name, e)
        return {"error": f"Invalid tool arguments: {e.errors()[0]['msg']}"}
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
        conversation_id: str,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Agentic loop using ChatAnthropic.bind_tools(). Yields SSE event tuples."""

        tool_context = ToolContext(
            token=token,
            clickpost_client=self.clickpost_client,
            conversation_id=conversation_id,
        )
        tools = create_tools(tool_context)
        tools_by_name = {tool.name: tool for tool in tools}
        llm = create_chat_model(model)
        secondary_llm = (
            create_secondary_chat_model() if settings.rr_analyst_scratchpad_enabled else None
        )
        use_scratchpad = settings.rr_analyst_scratchpad_enabled
        total_usage: dict[str, int] | None = None
        secondary_usage: dict[str, int] | None = None
        usage_by_iteration: list[dict[str, int]] = []

        for iteration in range(settings.rr_analyst_max_iterations):
            trimmed = prepare_messages(messages, system_prompt, conversation_id=conversation_id)
            is_last = iteration == settings.rr_analyst_max_iterations - 1

            if is_last:
                trimmed = [
                    *trimmed,
                    HumanMessage(
                        content="You've used all available iterations. Provide your best final answer now."
                    ),
                ]

            llm_input: list[BaseMessage] = [SystemMessage(content=system_prompt), *trimmed]

            if use_scratchpad and not is_last:
                user_text = _latest_user_text(trimmed)
                if user_text:
                    keywords = extract_query_keywords(user_text)
                    prefetch = build_auto_prefetch_message(
                        tool_result_store.list(conversation_id),
                        keywords,
                    )
                    if prefetch:
                        llm_input.append(HumanMessage(content=prefetch))

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
                        "secondary_usage": secondary_usage,
                    },
                )
                return

            messages.append(ai_message)

            # Phase 1: invoke all tools (sequential — tools may have side effects)
            tool_invocations: list[tuple[str, str, dict, dict, list[str]]] = []
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

                raw_data_parts = prepare_raw_data_parts(result) if use_scratchpad else []

                if use_scratchpad:
                    tool_result_store.put(
                        conversation_id,
                        StoredToolResult(
                            tool_call_id=tool_id,
                            tool_name=tool_name,
                            args=tool_args,
                            result=result,
                            scratchpad="",
                            scratchpad_batches=[],
                            raw_data_parts=raw_data_parts,
                        ),
                    )
                    logger.info(
                        "Stored full tool result tool=%s result_id=%s raw_data_parts=%d",
                        tool_name,
                        tool_id,
                        len(raw_data_parts),
                    )

                tool_invocations.append((tool_id, tool_name, tool_args, result, raw_data_parts))

            # Phase 2: build scratchpads in parallel across all tool results
            scratchpad_tasks = []
            for tool_id, tool_name, tool_args, result, raw_data_parts in tool_invocations:
                if use_scratchpad and secondary_llm is not None and raw_data_parts:
                    scratchpad_tasks.append(
                        summarize_raw_data_parts(secondary_llm, tool_name, tool_args, raw_data_parts)
                    )
                else:
                    async def _immediate(tn=tool_name, r=result):
                        if use_scratchpad:
                            return fallback_scratchpad(tn, r)
                        return ScratchpadBuildResult(scratchpad="", batches=[], usage=None)
                    scratchpad_tasks.append(_immediate())

            scratchpad_results_raw = await asyncio.gather(
                *scratchpad_tasks, return_exceptions=True,
            )

            # Phase 3: assemble tool messages
            for idx, (tool_id, tool_name, tool_args, result, raw_data_parts) in enumerate(
                tool_invocations
            ):
                built_or_exc = scratchpad_results_raw[idx]
                if isinstance(built_or_exc, Exception):
                    logger.exception(
                        "Scratchpad summarization failed for %s; using fallback",
                        tool_name,
                        exc_info=built_or_exc,
                    )
                    built = fallback_scratchpad(tool_name, result)
                else:
                    built = built_or_exc

                scratchpad = built.scratchpad
                scratchpad_batches = built.batches
                if built.usage:
                    secondary_usage = merge_usage(secondary_usage, built.usage)

                if use_scratchpad:
                    tool_result_store.put(
                        conversation_id,
                        StoredToolResult(
                            tool_call_id=tool_id,
                            tool_name=tool_name,
                            args=tool_args,
                            result=result,
                            scratchpad=scratchpad,
                            scratchpad_batches=scratchpad_batches,
                            raw_data_parts=raw_data_parts,
                        ),
                    )

                tool_message_content = _build_tool_message_content(
                    tool_call_id=tool_id,
                    tool_name=tool_name,
                    result=result,
                    scratchpad=scratchpad,
                    scratchpad_batches=scratchpad_batches,
                    raw_data_parts=raw_data_parts,
                    use_scratchpad=use_scratchpad,
                )
                messages.append(
                    ToolMessage(
                        content=tool_message_content,
                        tool_call_id=tool_id,
                        name=tool_name,
                    )
                )

                if use_scratchpad:
                    try:
                        envelope = json.loads(tool_message_content)
                        summary = _summarize_scratchpad_envelope(tool_name, envelope)
                    except json.JSONDecodeError:
                        summary = _summarize_result(tool_name, result)
                else:
                    summary = _summarize_result(tool_name, result)
                yield ("tool_result", {"tool": tool_name, "summary": summary})

        yield ("error", {"code": "max_iterations", "message": "Maximum iterations reached"})
