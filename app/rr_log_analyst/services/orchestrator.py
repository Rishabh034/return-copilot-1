from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator

import asyncpg
import anthropic

from app.config import settings
from app.rr_log_analyst.services.context_assembler import build_context, load_messages
from app.rr_log_analyst.services.tool_executor import execute_tool
from app.rr_log_analyst.tools import ToolContext, get_claude_tool_schemas

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        db: asyncpg.Connection,
        clickpost_client: Any,
        anthropic_client: anthropic.AsyncAnthropic,
    ):
        self.db = db
        self.clickpost_client = clickpost_client
        self.anthropic = anthropic_client

    async def handle_message_stream(
        self,
        conversation_id: str,
        user_text: str,
        enterprise_id: int,
        system_prompt: str,
        model: str,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        """Main agentic loop. Yields (event_type, data) tuples for SSE."""

        next_seq = await self._insert_and_get_seq(
            conversation_id, role="user", content=user_text,
        )

        tool_schemas = get_claude_tool_schemas()
        tool_context = ToolContext(
            enterprise_id=enterprise_id,
            clickpost_client=self.clickpost_client,
        )

        for iteration in range(settings.rr_analyst_max_iterations):
            rows = await load_messages(self.db, conversation_id)
            api_messages = build_context(rows, system_prompt)

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

                response = await self.anthropic.messages.create(**create_kwargs)
            except anthropic.APIError as e:
                logger.exception("Claude API error on iteration %d", iteration)
                yield ("error", {"code": "llm_error", "message": str(e)})
                return

            text_parts: list[str] = []
            tool_uses: list[dict] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    yield ("text_delta", {"delta": block.text})
                elif block.type == "tool_use":
                    tool_uses.append({
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            combined_text = "\n".join(text_parts) if text_parts else None

            if not tool_uses:
                final_msg_id = str(uuid.uuid4())
                await self._insert_and_get_seq(
                    conversation_id,
                    role="assistant",
                    content=combined_text,
                    msg_id=final_msg_id,
                    token_count=getattr(response.usage, "output_tokens", None),
                )
                await self._maybe_set_title(conversation_id, user_text)
                yield ("done", {"message_id": final_msg_id})
                return

            for tu in tool_uses:
                yield ("tool_start", {"tool": tu["name"], "args": tu["input"]})

                await self._insert_and_get_seq(
                    conversation_id,
                    role="assistant",
                    content=combined_text,
                    tool_call_id=tu["id"],
                    tool_name=tu["name"],
                    tool_args=tu["input"],
                )
                combined_text = None

                result = await execute_tool(tu["name"], tu["input"], tool_context)

                await self._insert_and_get_seq(
                    conversation_id,
                    role="tool",
                    tool_call_id=tu["id"],
                    tool_name=tu["name"],
                    tool_result=result,
                )

                summary = _summarize_result(tu["name"], result)
                yield ("tool_result", {"tool": tu["name"], "summary": summary})

        yield ("error", {"code": "max_iterations", "message": "Maximum iterations reached"})

    async def _insert_and_get_seq(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str | None = None,
        msg_id: str | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        tool_args: dict | None = None,
        tool_result: dict | None = None,
        token_count: int | None = None,
    ) -> int:
        """Atomic insert: compute seq and insert in one statement. Returns seq."""
        if msg_id is None:
            msg_id = str(uuid.uuid4())
        row = await self.db.fetchrow(
            """
            INSERT INTO rr_log_analyst_message
                (id, conversation_id, seq, role, content, tool_call_id, tool_name, tool_args, tool_result, token_count)
            VALUES (
                $1, $2,
                (SELECT COALESCE(MAX(seq), 0) + 1 FROM rr_log_analyst_message WHERE conversation_id = $2),
                $3, $4, $5, $6, $7, $8, $9
            )
            RETURNING seq
            """,
            msg_id,
            conversation_id,
            role,
            content,
            tool_call_id,
            tool_name,
            tool_args,
            tool_result,
            token_count,
        )
        await self.db.execute(
            "UPDATE rr_log_analyst_conversation SET updated_at = now() WHERE id = $1",
            conversation_id,
        )
        return row["seq"]

    async def _maybe_set_title(self, conversation_id: str, user_text: str) -> None:
        row = await self.db.fetchrow(
            "SELECT title FROM rr_log_analyst_conversation WHERE id = $1",
            conversation_id,
        )
        if row and not row["title"]:
            title = user_text[:100].strip()
            if len(user_text) > 100:
                title += "..."
            await self.db.execute(
                "UPDATE rr_log_analyst_conversation SET title = $1, updated_at = now() WHERE id = $2",
                title,
                conversation_id,
            )


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
