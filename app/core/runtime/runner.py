from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Callable, Mapping

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.clients.clickpost import ClickpostAPIError, ClickpostAuth, ClickpostPermissionError
from app.clients.databricks import DatabricksQueryError, get_databricks_client
from app.core.content import message_text, message_thinking
from app.clients.llm import create_chat_model, create_secondary_chat_model
from app.clients.observability import langchain_trace_config, mark_span_error, otel_span
from app.config import settings
from app.core.runtime.context import (
    build_system_message,
    merge_usage,
    prepare_messages,
    serialize_usage,
)
from app.core.runtime.result_query import (
    build_auto_prefetch_message,
    extract_query_keywords,
)
from app.core.runtime.errors import ToolErrorCode, summary_for_result, tool_error
from app.core.runtime.filters import NULL_FILTER, NullFilter, OutputFilter, StreamFilter
from app.core.runtime.policy import (
    DEFAULT_POLICY,
    ToolGroupPolicy,
    ToolPolicy,
    resolve_group_winners,
)
from app.core.runtime.presentation import DEFAULT_PRESENTATION, Presentation
from app.core.runtime.scratchpad import (
    ScratchpadBuildResult,
    build_compact_envelope,
    fallback_scratchpad,
    prepare_raw_data_parts,
    summarize_raw_data_parts,
)
from app.core.runtime.tool_results import StoredToolResult, tool_result_store
from app.core.spec import RuntimeSettings
from app.core.tool_context import CLICKPOST, DATABRICKS, ToolContext
from app.utilities.alerts import SlackAlertTool, tool_errors_slack_update

logger = logging.getLogger(__name__)

BuildTools = Callable[[ToolContext], list[BaseTool]]



def _policy_for(tool: BaseTool | None) -> ToolPolicy:
    """A tool declares its own policy; anything that does not gets the default."""
    return getattr(tool, "agent_policy", DEFAULT_POLICY) if tool else DEFAULT_POLICY


def _find_cached_result(
    conversation_id: str, tool_name: str, tool_args: dict[str, Any], reusable: bool
) -> StoredToolResult | None:
    """Find an earlier identical call in this conversation whose scratchpad is already built.

    Re-fetching the same tool with the same args re-runs the whole chunk +
    one-Haiku-call-per-chunk pipeline over data we already summarized — the
    single largest source of wasted secondary-model tokens (a repeated
    list_rules can be dozens of Haiku calls on its own).
    """
    if not reusable:
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


def _filter_content(content: str | list, output_filter: OutputFilter) -> str | list:
    """Apply the agent's output filter to assistant content before persistence.

    Streamed content arrives as a list of text blocks split on chunk
    boundaries, so filtering each block separately reproduces exactly the bug
    this exists to fix: a phone number spanning two blocks matches in neither.
    The text blocks are joined, filtered once, and returned as a single block,
    which is how every reader already treats them.

    A no-op filter returns the content untouched, so an agent that declares no
    filter keeps the block structure it had.
    """
    if isinstance(output_filter, NullFilter):
        return content
    if isinstance(content, str):
        return output_filter.filter_final(content)
    if not isinstance(content, list):
        return content

    filtered = output_filter.filter_final(message_text(content))

    out: list = []
    placed = False
    for block in content:
        is_text = isinstance(block, dict) and block.get("type") == "text"
        if not is_text:
            out.append(block)
        elif not placed:
            out.append({**block, "text": filtered})
            placed = True
    return out


def _latest_user_text(messages: list[BaseMessage]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            if isinstance(content, str) and content.startswith("[Auto-fetched tool result slices"):
                continue
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return message_text(content) or None
    return None


async def _invoke_tool(
    tool: BaseTool, args: dict[str, Any], conversation_id: str, timeout_seconds: int
) -> dict[str, Any]:
    with otel_span(f"tool:{tool.name}", conversation_id=conversation_id) as span:
        try:
            result = await asyncio.wait_for(tool.ainvoke(args), timeout=timeout_seconds)
            if isinstance(result, dict):
                return result
            return {"result": result}
        except asyncio.TimeoutError as e:
            caught_exc = e
            error = tool_error(
                ToolErrorCode.TIMEOUT,
                tool_name=tool.name,
                detail=f"timed out after {timeout_seconds}s",
            )
        except ClickpostPermissionError as e:
            caught_exc = e
            error = tool_error(
                ToolErrorCode.PERMISSION_DENIED, tool_name=tool.name, detail=str(e)
            )
        except (ClickpostAPIError, DatabricksQueryError) as e:
            caught_exc = e
            error = tool_error(ToolErrorCode.UPSTREAM_ERROR, tool_name=tool.name, detail=str(e))
        except ValidationError as e:
            caught_exc = e
            # The model's own malformed call. It is told exactly what to fix, and the
            # pydantic message is the one piece of detail that is safe to pass on
            # because it describes the arguments the model itself supplied.
            error = tool_error(
                ToolErrorCode.INVALID_ARGUMENTS,
                tool_name=tool.name,
                detail=str(e),
                model_message=f"Invalid tool arguments: {e.errors()[0]['msg']}. Correct them and call again.",
            )
        except Exception as e:
            caught_exc = e
            logger.exception("Tool %s failed", tool.name)
            error = tool_error(ToolErrorCode.INTERNAL, tool_name=tool.name, detail=str(e))

        # Caught above rather than left to propagate, so the span otherwise
        # looks like an ordinary successful one - mark it explicitly instead.
        # (`except ... as e` unbinds `e` once its block ends, hence `caught_exc`.)
        mark_span_error(span, caught_exc, f"[{error.code}] {error.detail}")

        logger.warning(
            "Tool %s failed code=%s detail=%s", tool.name, error.code, error.detail
        )
        # Slack gets the engineer-facing detail; the model never does.
        await asyncio.to_thread(
            tool_errors_slack_update,
            SlackAlertTool(
                conversation_id=conversation_id,
                tool_name=tool.name,
                error=f"[{error.code}] {error.detail}",
            ),
        )
        return error.as_tool_result()


class AgentRunner:
    def __init__(
        self,
        clickpost_client: Any,
        build_tools: BuildTools | None = None,
        agent_slug: str = "unknown",
        runtime: RuntimeSettings | None = None,
        presentation: Presentation = DEFAULT_PRESENTATION,
        tool_groups: dict[str, ToolGroupPolicy] | None = None,
        output_filter: OutputFilter = NULL_FILTER,
        clients: Mapping[str, Any] | None = None,
    ):
        self.clickpost_client = clickpost_client
        # Extra services this agent declared on its spec. The Clickpost client
        # stays the one the platform constructed; an agent cannot replace it.
        self._clients = dict(clients or {})
        # Tags this agent's traces so Langfuse can separate them by agent.
        self.agent_slug = agent_slug
        # The agent's tools, supplied by its spec. The loop must never import an
        # agent package: that is the dependency inversion this whole refactor
        # exists to remove.
        self.build_tools: BuildTools = build_tools or (lambda _context: [])
        # Model, limits and timeouts come from the agent's spec, not from the
        # global settings this loop used to read directly.
        self.runtime = runtime or RuntimeSettings.from_settings()
        # The agent's vocabulary for its own tools. Defaulted so the loop stays
        # runnable — and testable — without one.
        self.presentation = presentation
        self.tool_groups = tool_groups or {}
        # Applied to streamed text AND to the text that is persisted.
        self.output_filter = output_filter

    async def handle_message_stream(
        self,
        messages: list[BaseMessage],
        system_prompt: str,
        auth: ClickpostAuth,
        conversation_id: str,
        context_note: str | None = None,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Agentic loop using ChatAnthropic.bind_tools(). Yields SSE event tuples."""

        clients = dict(self._clients)
        clients[CLICKPOST] = self.clickpost_client
        clients[DATABRICKS] = get_databricks_client()
        tool_context = ToolContext(
            auth=auth,
            clients=clients,
            conversation_id=conversation_id,
        )
        tools = self.build_tools(tool_context)
        tools_by_name = {tool.name: tool for tool in tools}
        llm = create_chat_model(self.runtime)
        secondary_llm = (
            create_secondary_chat_model(self.runtime)
            if self.runtime.scratchpad_enabled
            else None
        )
        use_scratchpad = self.runtime.scratchpad_enabled
        total_usage: dict[str, int] | None = None
        secondary_usage: dict[str, int] | None = None
        usage_by_iteration: list[dict[str, int]] = []

        for iteration in range(self.runtime.max_iterations):
            trimmed = prepare_messages(messages, system_prompt, conversation_id=conversation_id)
            is_last = iteration == self.runtime.max_iterations - 1

            if is_last:
                trimmed = [
                    *trimmed,
                    HumanMessage(
                        content="You've used all available iterations. Provide your best final answer now."
                    ),
                ]

            llm_input: list[BaseMessage] = [
                build_system_message(system_prompt, context_note),
                *trimmed,
            ]

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
            # Buffers streamed text so a pattern straddling two chunks is still
            # matched. Filtering each delta in isolation cannot see across the
            # boundary, and Anthropic does not stream on boundaries that respect
            # phone numbers.
            stream_filter = StreamFilter(self.output_filter)

            try:
                async for chunk in runnable.astream(
                    llm_input, config=langchain_trace_config(conversation_id, agent_slug=self.agent_slug)
                ):
                    thinking_delta = message_thinking(chunk.content)
                    if thinking_delta:
                        yield ("thinking_delta", {"delta": thinking_delta})
                    text_delta = message_text(chunk.content)
                    if text_delta:
                        emit = stream_filter.push(text_delta)
                        if emit:
                            yield ("text_delta", {"delta": emit})
                    accumulated = chunk if accumulated is None else accumulated + chunk
            except Exception:
                logger.exception("Claude API error on iteration %d", iteration)
                yield (
                    "error",
                    {"code": "llm_error", "message": "The assistant is busy. Please try again."},
                )
                return

            trailing = stream_filter.flush()
            if trailing:
                yield ("text_delta", {"delta": trailing})

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
                # Filtered before it is persisted, not only before it is streamed.
                # The Return & Exchange fork filtered the delta and kept the raw
                # value in `accumulated`, so the browser showed masked text while
                # the database stored the original.
                content=_filter_content(accumulated.content, self.output_filter),
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

            # At most N tools per declared group run in one turn. Decided up
            # front so the choice cannot depend on the order tools happen to
            # be invoked in.
            requested_by_group: dict[str, set[str]] = {}
            for tool_call in accumulated.tool_calls:
                group = _policy_for(tools_by_name.get(tool_call["name"])).group
                if group:
                    requested_by_group.setdefault(group, set()).add(tool_call["name"])
            skipped_by_group = resolve_group_winners(
                requested_by_group, self.tool_groups, _latest_user_text(trimmed)
            )
            skipped_tools = {name for names in skipped_by_group.values() for name in names}
            if skipped_tools:
                logger.info(
                    "Tool group budget skipped=%s conversation_id=%s",
                    sorted(skipped_tools),
                    conversation_id,
                )

            for tool_call in accumulated.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call.get("args") or {}
                tool_id = tool_call["id"]

                yield (
                    "tool_start",
                    {
                        "tool": tool_name,
                        "label": self.presentation.tool_label(tool_name),
                        "args": tool_args,
                    },
                )

                policy = _policy_for(tools_by_name.get(tool_name))
                tool_uses_scratchpad = use_scratchpad and policy.scratchpad

                cached = (
                    _find_cached_result(
                        conversation_id, tool_name, tool_args, reusable=policy.reusable
                    )
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
                if tool_name in skipped_tools:
                    result = tool_error(
                        ToolErrorCode.POLICY_SKIPPED,
                        tool_name=tool_name,
                        detail=f"group budget for {_policy_for(tool).group!r}",
                    ).as_tool_result()
                elif tool is None:
                    result = tool_error(
                        ToolErrorCode.INTERNAL,
                        tool_name=tool_name,
                        detail=f"unknown tool {tool_name!r}",
                        model_message=f"Unknown tool: {tool_name}",
                    ).as_tool_result()
                else:
                    result = await _invoke_tool(
                        tool,
                        tool_args,
                        conversation_id=conversation_id,
                        timeout_seconds=self.runtime.tool_timeout_seconds,
                    )

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
                            secondary_llm,
                            tool_name,
                            tool_args,
                            raw_data_parts,
                            conversation_id,
                            self.agent_slug,
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

                # A coded failure reads the same whatever the agent is; only a
                # successful result needs the agent's vocabulary.
                summary = summary_for_result(result) or self.presentation.summarize(
                    tool_name, result
                )
                yield (
                    "tool_result",
                    {
                        "tool": tool_name,
                        "label": self.presentation.tool_label(tool_name),
                        "summary": summary,
                    },
                )

        yield ("error", {"code": "max_iterations", "message": "Maximum iterations reached"})
