from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.rr_log_analyst.memory.tool_results import tool_result_store
from app.rr_log_analyst.tools.context import ToolContext


class QueryResultInput(BaseModel):
    result_id: str = Field(
        description="The result_id from prior evidence.",
    )
    keywords: list[str] | None = Field(
        default=None,
        description=(
            "Keywords to match against source keys and string values. "
            "Omit to return the evidence summary."
        ),
    )
    max_matches: int = Field(
        default=20,
        description="Maximum number of matching source sections to return.",
        ge=1,
        le=100,
    )
    batch_index: int | None = Field(
        default=None,
        description=(
            "Optional 1-based summary segment. Omit for the complete summary; "
            "set to read one segment when multiple summary segments are available."
        ),
        ge=1,
    )


class QueryRawDataPartInput(BaseModel):
    result_id: str = Field(
        description="The result_id from prior evidence.",
    )
    part_index: int = Field(
        description="1-based source segment index.",
        ge=1,
    )


def build_tools(context: ToolContext) -> list[StructuredTool]:
    async def query_result(
        result_id: str,
        keywords: list[str] | None = None,
        max_matches: int = 20,
        batch_index: int | None = None,
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

        response: dict[str, Any] = {
            "result_id": result_id,
            "source": stored.tool_name,
        }

        if keywords:
            from app.rr_log_analyst.agent.result_query import match_json_keywords  # deferred to break circular import

            response["args"] = stored.args
            response["matches"] = match_json_keywords(
                stored.result,
                [keyword.lower() for keyword in keywords],
                max_matches=max_matches,
            )
            # Keyword search already targets a specific slice — don't also bundle
            # the (potentially huge) scratchpad text unless batch_index was explicitly asked for.
            if batch_index is None:
                return response

        if batch_index is None:
            response["summary_chars"] = len(stored.scratchpad)
            response["summary_segments"] = len(stored.scratchpad_batches)
            response["evidence_summary"] = stored.scratchpad
        else:
            total = len(stored.scratchpad_batches)
            if total == 0:
                response["summary_error"] = "No summary segments are available for this result."
            elif batch_index < 1 or batch_index > total:
                response["scratchpad_error"] = f"batch_index must be between 1 and {total}."
                response["total_batches"] = total
            else:
                response["batch_index"] = batch_index
                response["total_batches"] = total
                response["summary_segment"] = stored.scratchpad_batches[batch_index - 1]

        return response

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
                "error": "No source segments are available for this result.",
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
            "source": stored.tool_name,
            "part_index": part_index,
            "total_parts": total,
            "content": stored.raw_data_parts[part_index - 1],
        }

    return [
        StructuredTool.from_function(
            coroutine=query_result,
            name="inspect_evidence",
            description=(
                "Inspect prior evidence by result_id. Use keywords for focused source matches. "
                "Omit keywords and batch_index for the complete evidence summary, or provide "
                "batch_index for one summary segment."
            ),
            args_schema=QueryResultInput,
        ),
        StructuredTool.from_function(
            coroutine=query_raw_data_part,
            name="inspect_source_segment",
            description=(
                "Inspect one source segment by result_id and 1-based part_index."
            ),
            args_schema=QueryRawDataPartInput,
        ),
    ]
