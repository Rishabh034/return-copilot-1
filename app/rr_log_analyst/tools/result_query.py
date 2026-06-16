from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.agent.result_query import match_json_keywords
from app.rr_log_analyst.memory.tool_results import tool_result_store
from app.rr_log_analyst.tools.context import ToolContext


class QueryToolResultInput(BaseModel):
    result_id: str = Field(
        description="The result_id (tool_call_id) from a prior tool scratchpad envelope.",
    )
    keywords: list[str] = Field(
        description="Keywords to match against stored JSON keys and string values.",
        min_length=1,
    )
    max_matches: int = Field(
        default=20,
        description="Maximum number of matching subtrees to return.",
        ge=1,
        le=100,
    )


class QueryScratchpadBatchInput(BaseModel):
    result_id: str = Field(
        description="The result_id (tool_call_id) from a prior tool scratchpad envelope.",
    )
    batch_index: int = Field(
        description="1-based scratchpad batch index (see scratchpad_batches count in envelope).",
        ge=1,
    )


class QueryFullScratchpadInput(BaseModel):
    result_id: str = Field(
        description="The result_id (tool_call_id) from a prior tool scratchpad envelope.",
    )


class QueryRawDataPartInput(BaseModel):
    result_id: str = Field(
        description="The result_id (tool_call_id) from a prior tool result envelope.",
    )
    part_index: int = Field(
        description="1-based raw data part index (see raw_data_parts count in envelope).",
        ge=1,
    )


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def query_tool_result(
        result_id: str,
        keywords: list[str],
        max_matches: int = 20,
    ) -> dict[str, Any]:
        if not context.conversation_id:
            return {"error": "No conversation context for stored tool results."}

        stored = tool_result_store.get(context.conversation_id, result_id)
        if stored is None:
            available = [item.tool_call_id for item in tool_result_store.list(context.conversation_id)]
            return {
                "error": f"No stored result for result_id={result_id!r}.",
                "available_result_ids": available[:20],
            }

        matched = match_json_keywords(
            stored.result,
            [keyword.lower() for keyword in keywords],
            max_matches=max_matches,
        )
        return {
            "result_id": result_id,
            "tool": stored.tool_name,
            "args": stored.args,
            **matched,
        }

    async def query_scratchpad_batch(result_id: str, batch_index: int) -> dict[str, Any]:
        if not context.conversation_id:
            return {"error": "No conversation context for stored tool results."}

        stored = tool_result_store.get(context.conversation_id, result_id)
        if stored is None:
            available = [item.tool_call_id for item in tool_result_store.list(context.conversation_id)]
            return {
                "error": f"No stored result for result_id={result_id!r}.",
                "available_result_ids": available[:20],
            }

        total = len(stored.scratchpad_batches)
        if total == 0:
            return {
                "error": "No scratchpad batches stored for this result.",
                "result_id": result_id,
            }
        if batch_index < 1 or batch_index > total:
            return {
                "error": f"batch_index must be between 1 and {total}.",
                "result_id": result_id,
                "total_batches": total,
            }

        return {
            "result_id": result_id,
            "tool": stored.tool_name,
            "batch_index": batch_index,
            "total_batches": total,
            "content": stored.scratchpad_batches[batch_index - 1],
        }

    async def query_full_scratchpad(result_id: str) -> dict[str, Any]:
        if not context.conversation_id:
            return {"error": "No conversation context for stored tool results."}

        stored = tool_result_store.get(context.conversation_id, result_id)
        if stored is None:
            available = [item.tool_call_id for item in tool_result_store.list(context.conversation_id)]
            return {
                "error": f"No stored result for result_id={result_id!r}.",
                "available_result_ids": available[:20],
            }

        return {
            "result_id": result_id,
            "tool": stored.tool_name,
            "scratchpad_total_chars": len(stored.scratchpad),
            "scratchpad_batches": len(stored.scratchpad_batches),
            "scratchpad": stored.scratchpad,
        }

    async def query_raw_data_part(result_id: str, part_index: int) -> dict[str, Any]:
        if not context.conversation_id:
            return {"error": "No conversation context for stored tool results."}

        stored = tool_result_store.get(context.conversation_id, result_id)
        if stored is None:
            available = [item.tool_call_id for item in tool_result_store.list(context.conversation_id)]
            return {
                "error": f"No stored result for result_id={result_id!r}.",
                "available_result_ids": available[:20],
            }

        total = len(stored.raw_data_parts)
        if total == 0:
            return {
                "error": "No raw data parts stored for this result.",
                "result_id": result_id,
            }
        if part_index < 1 or part_index > total:
            return {
                "error": f"part_index must be between 1 and {total}.",
                "result_id": result_id,
                "total_parts": total,
            }

        return {
            "result_id": result_id,
            "tool": stored.tool_name,
            "part_index": part_index,
            "total_parts": total,
            "content": stored.raw_data_parts[part_index - 1],
        }

    return [
        StructuredTool.from_function(
            coroutine=query_tool_result,
            name="query_tool_result",
            description=(
                "Fetch keyword-matched slices from a stored tool result by result_id. "
                "Use when the scratchpad lacks a specific field (rule name, stage key, account, etc.)."
            ),
            args_schema=QueryToolResultInput,
        ),
        StructuredTool.from_function(
            coroutine=query_scratchpad_batch,
            name="query_scratchpad_batch",
            description=(
                "Read one stored scratchpad batch by result_id and 1-based batch_index. "
                "Use when the scratchpad preview is not enough — large tool results are summarized "
                "in multiple batches kept in memory."
            ),
            args_schema=QueryScratchpadBatchInput,
        ),
        StructuredTool.from_function(
            coroutine=query_full_scratchpad,
            name="query_full_scratchpad",
            description=(
                "Return the complete stored scratchpad for a result_id with no length cap. "
                "Use when the envelope only shows scratchpad_stored=true or you need the full notes."
            ),
            args_schema=QueryFullScratchpadInput,
        ),
        StructuredTool.from_function(
            coroutine=query_raw_data_part,
            name="query_raw_data_part",
            description=(
                "Read one stored raw fetched-data part by result_id and 1-based part_index. "
                "These parts are the full tool JSON split to fit Haiku context before summarization."
            ),
            args_schema=QueryRawDataPartInput,
        ),
    ]
