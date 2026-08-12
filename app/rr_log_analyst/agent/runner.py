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
    ToolMessage,
)
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.clients.clickpost import ClickpostAPIError, ClickpostPermissionError
from app.clients.llm import create_chat_model, create_secondary_chat_model
from app.clients.observability import langchain_trace_config
from app.config import settings
from app.rr_log_analyst.agent.context import (
    build_system_message,
    merge_usage,
    prepare_messages,
    serialize_usage,
)
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


_FRIENDLY_TOOL_LABELS: dict[str, str] = {
    "fetch_rr_log": "Looking up the order",
    "fetch_order_creation_log": "Checking the order creation history",
    "fetch_audit_logs": "Checking recent changes",
    "list_rules": "Reading the delivery settings",
    "find_rule": "Finding a setting",
    "list_carrier_exception_rules": "Reading carrier attribute rules",
    "get_enterprise_flow_config": "Checking the delivery setup",
    "get_load_distribution_config": "Checking how orders are split",
    "run_recommendation": "Running a test",
    "get_account_info": "Looking up delivery company accounts",
    "search_courier_partners": "Searching delivery companies",
    "check_route_serviceability": "Checking route serviceability",
    "inspect_evidence": "Reviewing the evidence",
    "inspect_source_segment": "Checking supporting details",
}


def _friendly_tool_label(tool_name: str) -> str:
    return _FRIENDLY_TOOL_LABELS.get(tool_name, tool_name.replace("_", " ").capitalize())


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


def _extract_thinking_delta(chunk_content: str | list) -> str | None:
    if isinstance(chunk_content, list):
        parts = [
            block.get("thinking", "")
            for block in chunk_content
            if isinstance(block, dict) and block.get("type") == "thinking"
        ]
        joined = "".join(parts)
        return joined or None
    return None


def _summarize_result(tool_name: str, result: dict) -> str:
    if "error" in result:
        return f"Hit a problem: {result['error']}"

    if "total_ranked" in result and "filters_ran" in result:
        rule = result.get("matched_filter_rule")
        top_pref = (result.get("preference_array") or [{}])[0] if result.get("preference_array") else {}
        top_name = top_pref.get("cp_name") or top_pref.get("account_code") or ""
        parts = [f"Looked up this order — {result['total_ranked']} courier partners were available"]
        if top_name:
            parts.append(f"{top_name} was chosen first")
        if rule:
            parts.append(f"because of the \"{rule}\" rule")
        return ", ".join(parts)

    if "top_courier" in result:
        top = result.get("top_courier") or {}
        name = top.get("cp_name") or top.get("account_code") or "the top one"
        return f"{name} was the first choice, out of {result.get('total_ranked', 0)} delivery companies"

    if "stage" in result and "value" in result:
        from app.rr_log_analyst.tools.allocation_views import _PIPELINE_STAGE_DESCRIPTIONS
        desc = _PIPELINE_STAGE_DESCRIPTIONS.get(result["stage"], result["stage"])
        return f"Checked one step of how the delivery company was picked: {desc}"

    if "ranking" in result and "total_ranked" in result:
        matching = result.get("total_matching", len(result["ranking"]))
        return f"{matching} delivery company(s) matched, out of {result['total_ranked']} that were available"

    if "in_final_ranking" in result:
        present = result["in_final_ranking"]
        if present:
            return "Yes — this delivery company was one of the final choices"
        return "No — this delivery company was ruled out before the final choices"

    if "flow_summary" in result and "flags" in result:
        return "Checked the settings that control how delivery companies are picked"

    if "flag" in result and "value" in result:
        state = "on" if result["value"] in (True, "true", "True", 1) else "off"
        return f"The \"{result['flag']}\" setting is turned {state}"

    # Audit logs must be matched before the generic count+logs branch below, which
    # would otherwise describe rule edits as "shipments".
    if "action_taken" in result and "logs" in result:
        count = result.get("count", 0)
        if count:
            return f"Found {count} recent change(s) made to the settings"
        return "No setting changes found for this time period"

    if "pickup_count" in result and "drop_count" in result:
        route_count = result.get("count", 0)
        if result.get("serviceable") is True:
            return "This account is serviceable for the pickup-to-drop route"
        if result.get("serviceable") is False:
            return "This account is not serviceable for the pickup-to-drop route"
        return (
            f"Found {route_count} account(s) serviceable on both pickup and drop "
            f"({result['pickup_count']} pickup, {result['drop_count']} drop)"
        )

    if "is_pickup_pincode" in result and "pincode" in result:
        side = "pickup" if result["is_pickup_pincode"] else "drop"
        if result.get("serviceable") is True:
            return f"This account is serviceable for the {side} pincode"
        if result.get("serviceable") is False:
            return f"This account is not serviceable for the {side} pincode"
        return f"Found {result.get('count', 0)} account(s) for {side} pincode {result['pincode']}"

    if "count" in result and "summaries" in result:
        return f"Reviewed {result['count']} shipments"

    if "count" in result and "logs" in result:
        return f"Pulled up the history for {result['count']} shipments"

    if "count" in result:
        return f"Found {result['count']} results"

    if "total_rules" in result:
        return f"Found {result['total_rules']} setting(s) that decide which delivery company to use"

    if "total_accounts" in result:
        brands = result.get("carrier_brands") or []
        return f"Found {result['total_accounts']} delivery company account(s) across {len(brands)} carriers"

    if "total_entries" in result and "enabled_entries" in result:
        return (
            f"Checked how orders are split between delivery companies — "
            f"{result['enabled_entries']} of {result['total_entries']} splits are active"
        )

    if "total_matching" in result:
        rules = result.get("rules") or []
        if rules:
            names = ", ".join(r.get("name", "unnamed") for r in rules[:3])
            suffix = ", and more" if len(rules) > 3 else ""
            return f"Found {result['total_matching']} matching setting(s): {names}{suffix}"
        return f"Found {result['total_matching']} matching setting(s)"

    if "rule" in result:
        rule = result["rule"]
        return f"Opened the \"{rule.get('name', 'unnamed')}\" setting"

    if "flow_summary" in result:
        return "Checked the settings that control how delivery companies are picked"

    if "results" in result and "count" in result:
        first = result["results"][0] if result["results"] else {}
        ranked = first.get("total_ranked", 0)
        return f"Ran a test for {result['count']} order(s) — {ranked} delivery companies came up"

    if "entry_count" in result and "entries" in result:
        n = result["entry_count"]
        return f"Found {n} time(s) this shipment was sent to a delivery company"

    if "resolved" in result:
        resolved = result["resolved"]
        return f"This is for the \"{resolved.get('name', 'unknown')}\" account"

    if "matches" in result:
        count = result.get("match_count", len(result["matches"]))
        if count == 1:
            name = result["matches"][0].get("name", "unknown") if result["matches"] else "unknown"
            return f"Found the account: {name}"
        return f"Found {count} possible accounts — please pick the right one"

    return "Done"

_SCRATCHPAD_EXEMPT_TOOLS = frozenset({"inspect_evidence", "inspect_source_segment"})

# Tools whose result can change between identical calls, so an earlier result in
# the same conversation must never be reused (simulations, "run it again" actions).
_DEDUPE_EXEMPT_TOOLS = frozenset({"run_recommendation"})


def _find_cached_result(
    conversation_id: str, tool_name: str, tool_args: dict[str, Any]
) -> StoredToolResult | None:
    """Find an earlier identical call in this conversation whose scratchpad is already built.

    Re-fetching the same tool with the same args re-runs the whole chunk +
    one-Haiku-call-per-chunk pipeline over data we already summarized — the
    single largest source of wasted secondary-model tokens (a repeated
    list_rules can be dozens of Haiku calls on its own).
    """
    if tool_name in _DEDUPE_EXEMPT_TOOLS:
        return None
    for stored in tool_result_store.list(conversation_id):
        if stored.tool_name == tool_name and stored.args == tool_args and stored.settled:
            return stored
    return None


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

            llm_input: list[BaseMessage] = [build_system_message(system_prompt), *trimmed]

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
                async for chunk in runnable.astream(
                    llm_input, config=langchain_trace_config(conversation_id)
                ):
                    thinking_delta = _extract_thinking_delta(chunk.content)
                    if thinking_delta:
                        yield ("thinking_delta", {"delta": thinking_delta})
                    text_delta = _extract_text_delta(chunk.content)
                    if text_delta:
                        yield ("text_delta", {"delta": text_delta})
                    accumulated = chunk if accumulated is None else accumulated + chunk
            except Exception:
                logger.exception("Claude API error on iteration %d", iteration)
                yield (
                    "error",
                    {"code": "llm_error", "message": "The assistant is busy. Please try again."},
                )
                return

            if accumulated is None:
                yield ("error", {"code": "llm_error", "message": "Empty model response"})
                return

            iteration_usage = serialize_usage(accumulated.usage_metadata)
            if iteration_usage:
                usage_by_iteration.append(iteration_usage)
                total_usage = merge_usage(total_usage, iteration_usage)
                logger.info(
                    "Claude usage iteration %d: input=%d output=%d total=%d "
                    "cache_read=%d cache_write=%d uncached_input=%d",
                    iteration + 1,
                    iteration_usage["input_tokens"],
                    iteration_usage["output_tokens"],
                    iteration_usage["total_tokens"],
                    iteration_usage.get("cache_read_input_tokens", 0),
                    iteration_usage.get("cache_creation_input_tokens", 0),
                    iteration_usage["input_tokens"]
                    - iteration_usage.get("cache_read_input_tokens", 0)
                    - iteration_usage.get("cache_creation_input_tokens", 0),
                )

            ai_message = AIMessage(
                content=accumulated.content,
                tool_calls=accumulated.tool_calls,
                usage_metadata=accumulated.usage_metadata,
            )

            if not accumulated.tool_calls:
                messages.append(ai_message)
                # Final assistant turn — persist it, then close the stream.
                # Messages are persisted incrementally via message_complete, so
                # "done" no longer carries the full list.
                yield ("message_complete", {"message": ai_message})
                yield (
                    "done",
                    {
                        "usage": total_usage,
                        "usage_by_iteration": usage_by_iteration,
                        "secondary_usage": secondary_usage,
                    },
                )
                return

            messages.append(ai_message)
            # Assistant turn carrying tool_use blocks. Persisted together with
            # its ToolMessage(s) below so the replay history stays balanced.
            yield ("message_complete", {"message": ai_message})

            # Phase 1: invoke all tools (sequential — tools may have side effects)
            tool_invocations: list[
                tuple[str, str, dict, dict, list[str], ScratchpadBuildResult | None, bool]
            ] = []
            for tool_call in accumulated.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args") or {}
                tool_id = tool_call["id"]

                yield (
                    "tool_start",
                    {
                        "tool": tool_name,
                        "label": _friendly_tool_label(tool_name),
                        "args": tool_args,
                    },
                )

                tool_uses_scratchpad = use_scratchpad and tool_name not in _SCRATCHPAD_EXEMPT_TOOLS

                cached = (
                    _find_cached_result(conversation_id, tool_name, tool_args)
                    if tool_uses_scratchpad
                    else None
                )
                if cached is not None:
                    # Reuse the earlier identical call verbatim: no HTTP re-fetch,
                    # no re-chunking, and no Haiku calls at all.
                    result = cached.result
                    raw_data_parts = cached.raw_data_parts
                    cached_built = ScratchpadBuildResult(
                        scratchpad=cached.scratchpad,
                        batches=list(cached.scratchpad_batches),
                        usage=None,
                    )
                    # Alias under this tool_call_id so query_result(result_id) resolves
                    # for the id the model actually sees in this turn's envelope.
                    tool_result_store.put(
                        conversation_id,
                        StoredToolResult(
                            tool_call_id=tool_id,
                            tool_name=tool_name,
                            args=tool_args,
                            result=result,
                            scratchpad=cached.scratchpad,
                            scratchpad_batches=list(cached.scratchpad_batches),
                            raw_data_parts=raw_data_parts,
                            settled=True,
                        ),
                    )
                    logger.info(
                        "Reused cached tool result tool=%s result_id=%s (from %s) "
                        "— skipped re-fetch and %d Haiku calls",
                        tool_name,
                        tool_id,
                        cached.tool_call_id,
                        len(raw_data_parts),
                    )
                    tool_invocations.append(
                        (tool_id, tool_name, tool_args, result, raw_data_parts, cached_built, True)
                    )
                    continue

                tool = tools_by_name.get(tool_name)
                if tool is None:
                    result = {"error": f"Unknown tool: {tool_name}"}
                else:
                    result = await _invoke_tool(tool, tool_args)

                # A result small enough to hand over as-is should never be summarized:
                # the Haiku call costs tokens to produce something lossier than the
                # original, and the lost detail comes back as extra drill-down turns.
                result_chars = len(json.dumps(result, default=str))
                summarize = (
                    tool_uses_scratchpad and result_chars > settings.scratchpad_min_result_chars
                )
                raw_data_parts = prepare_raw_data_parts(result) if summarize else []

                if tool_uses_scratchpad:
                    # Stored either way — query_result(keywords=...) searches the stored
                    # raw JSON, and that stays useful even with no scratchpad built.
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
                            # Final already when nothing will be summarized; otherwise
                            # Phase 3 re-stores it with the notes and marks it settled.
                            settled=not summarize,
                        ),
                    )
                    logger.info(
                        "Stored full tool result tool=%s result_id=%s chars=%d "
                        "raw_data_parts=%d summarize=%s",
                        tool_name,
                        tool_id,
                        result_chars,
                        len(raw_data_parts),
                        summarize,
                    )

                tool_invocations.append(
                    (tool_id, tool_name, tool_args, result, raw_data_parts, None, summarize)
                )

            # Phase 2: build scratchpads in parallel across all tool results
            scratchpad_tasks = []
            for (
                tool_id,
                tool_name,
                tool_args,
                result,
                raw_data_parts,
                cached_built,
                summarize,
            ) in tool_invocations:
                if cached_built is not None:
                    async def _reuse(built=cached_built):
                        return built
                    scratchpad_tasks.append(_reuse())
                elif summarize and secondary_llm is not None and raw_data_parts:
                    scratchpad_tasks.append(
                        summarize_raw_data_parts(
                            secondary_llm, tool_name, tool_args, raw_data_parts, conversation_id
                        )
                    )
                else:
                    async def _immediate(tn=tool_name, r=result, use_fallback=summarize):
                        if use_fallback:
                            return fallback_scratchpad(tn, r)
                        return ScratchpadBuildResult(scratchpad="", batches=[], usage=None)
                    scratchpad_tasks.append(_immediate())

            scratchpad_results_raw = await asyncio.gather(
                *scratchpad_tasks, return_exceptions=True,
            )

            # Phase 3: assemble tool messages
            for idx, (
                tool_id,
                tool_name,
                tool_args,
                result,
                raw_data_parts,
                _cached_built,
                summarize,
            ) in enumerate(tool_invocations):
                built_or_exc = scratchpad_results_raw[idx]
                if isinstance(built_or_exc, Exception):
                    logger.exception(
                        "Scratchpad summarization failed for %s; using fallback",
                        tool_name,
                        exc_info=built_or_exc,
                    )
                    built = fallback_scratchpad(tool_name, result) if summarize else ScratchpadBuildResult(
                        scratchpad="", batches=[], usage=None
                    )
                else:
                    built = built_or_exc

                scratchpad = built.scratchpad
                scratchpad_batches = built.batches
                if built.usage:
                    secondary_usage = merge_usage(secondary_usage, built.usage)

                if summarize:
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
                            settled=True,
                        ),
                    )

                tool_message_content = _build_tool_message_content(
                    tool_call_id=tool_id,
                    tool_name=tool_name,
                    result=result,
                    scratchpad=scratchpad,
                    scratchpad_batches=scratchpad_batches,
                    raw_data_parts=raw_data_parts,
                    # Not summarized → the model gets the raw JSON verbatim, which is
                    # both cheaper and lossless compared to an envelope + notes.
                    use_scratchpad=summarize,
                )
                tool_message = ToolMessage(
                    content=tool_message_content,
                    tool_call_id=tool_id,
                    name=tool_name,
                    additional_kwargs=(
                        {"usage_metadata": built.usage} if built.usage else {}
                    ),
                )
                messages.append(tool_message)
                yield ("message_complete", {"message": tool_message})

                summary = _summarize_result(tool_name, result)
                yield (
                    "tool_result",
                    {
                        "tool": tool_name,
                        "label": _friendly_tool_label(tool_name),
                        "summary": summary,
                    },
                )

        yield ("error", {"code": "max_iterations", "message": "Maximum iterations reached"})
