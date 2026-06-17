from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_anthropic import ChatAnthropic

from app.config import settings
from app.rr_log_analyst.agent.context import merge_usage, serialize_usage

logger = logging.getLogger(__name__)

_SUMMARIZE_PART_PROMPT = """You are a tool-result note-taker. This is a separate summarization call for one stored data part.
The full tool result was already fetched and stored; you are only reading this part.

Rules:
- Preserve field names, IDs, counts, rule names, account names exactly
- Do NOT interpret or recommend — record what the data contains
- Use compact key: value / bullet structure, as raw as practical
- Record every field present in this part (including partial JSON fragments)
- This part is independent — summarize only what is shown below

Tool: {tool_name} ({args_summary})
Stored data part {part_index}/{part_total}:
{data_part}

Return ONLY the scratchpad notes for this part."""


@dataclass
class ScratchpadBuildResult:
    scratchpad: str
    batches: list[str]
    usage: dict[str, int] | None


def prepare_raw_data_parts(result: dict[str, Any], max_chars: int | None = None) -> list[str]:
    """Split the full tool JSON into parts sized for one Haiku context window each."""
    return chunk_json_result(result, max_chars=max_chars)


def chunk_json_result(result: dict[str, Any], max_chars: int | None = None) -> list[str]:
    """Split full tool JSON into Haiku-sized parts. No compaction — whole fetched data only."""
    limit = max_chars or settings.scratchpad_chunk_chars
    full = json.dumps(result, default=str)
    if len(full) <= limit:
        return [full]

    chunks: list[str] = []
    for key, value in result.items():
        if isinstance(value, list) and value:
            for item in value:
                piece = json.dumps({key: [item]}, default=str)
                if len(piece) <= limit:
                    chunks.append(piece)
                else:
                    for offset in range(0, len(piece), limit):
                        chunks.append(piece[offset : offset + limit])
            continue

        piece = json.dumps({key: value}, default=str)
        if len(piece) <= limit:
            chunks.append(piece)
            continue

        if isinstance(value, str):
            for offset in range(0, len(value), limit):
                chunks.append(json.dumps({key: value[offset : offset + limit]}, default=str))
        else:
            for offset in range(0, len(piece), limit):
                chunks.append(piece[offset : offset + limit])

    return chunks or [full]


def assemble_scratchpad(batch_summaries: list[str]) -> str:
    """Combine per-part Haiku summaries into one full scratchpad."""
    cleaned = [summary.strip() for summary in batch_summaries if summary.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]

    parts = [
        f"--- batch {index}/{len(cleaned)} ---\n{summary}"
        for index, summary in enumerate(cleaned, start=1)
    ]
    return "\n\n".join(parts)


def build_compact_envelope(
    *,
    result_id: str,
    tool_name: str,
    scratchpad: str,
    batches: list[str],
    raw_data_parts: list[str],
) -> dict[str, Any]:
    """Build the tool message envelope for the main LLM."""
    inline_limit = settings.scratchpad_inline_max_chars
    envelope: dict[str, Any] = {
        "result_id": result_id,
        "tool": tool_name,
        "raw_data_parts": len(raw_data_parts),
        "scratchpad_batches": len(batches),
        "scratchpad_total_chars": len(scratchpad),
        "note": (
            "Full tool JSON and all raw data parts are stored first, then scratchpad batches "
            "from separate Haiku calls. Use query_raw_data_part(result_id, part_index) for "
            "fetched source data, query_scratchpad_batch for one summary batch, "
            "query_full_scratchpad for the complete scratchpad, or query_tool_result for JSON keyword slices."
        ),
    }
    if len(scratchpad) <= inline_limit:
        envelope["scratchpad"] = scratchpad
    else:
        envelope["scratchpad_stored"] = True
    return envelope


def _extract_text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _args_summary(args: dict[str, Any]) -> str:
    if not args:
        return "no args"
    parts = [f"{key}={value!r}" for key, value in list(args.items())[:4]]
    suffix = "..." if len(args) > 4 else ""
    return ", ".join(parts) + suffix


async def summarize_raw_data_parts(
    llm: ChatAnthropic,
    tool_name: str,
    tool_args: dict[str, Any],
    raw_data_parts: list[str],
) -> ScratchpadBuildResult:
    """Run one new Haiku call per stored raw data part to build scratchpad batch summaries."""
    if not raw_data_parts:
        return ScratchpadBuildResult(scratchpad="", batches=[], usage=None)

    total_usage: dict[str, int] | None = None
    args_text = _args_summary(tool_args)
    part_total = len(raw_data_parts)

    logger.info(
        "Scratchpad summarization tool=%s: %d stored data parts → %d parallel Haiku calls",
        tool_name,
        part_total,
        part_total,
    )

    prompts = [
        _SUMMARIZE_PART_PROMPT.format(
            tool_name=tool_name,
            args_summary=args_text,
            part_index=index,
            part_total=part_total,
            data_part=data_part,
        )
        for index, data_part in enumerate(raw_data_parts, start=1)
    ]

    async def _summarize_one(prompt: str, index: int) -> str:
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            return response
        except Exception:
            logger.exception(
                "Scratchpad Haiku summarization failed on part %d/%d for tool %s",
                index,
                part_total,
                tool_name,
            )
            raise

    responses = await asyncio.gather(
        *[_summarize_one(prompt, i) for i, prompt in enumerate(prompts, start=1)]
    )

    batch_summaries: list[str] = []
    for response in responses:
        batch_summaries.append(_extract_text(response.content).strip())
        usage = serialize_usage(response.usage_metadata)
        total_usage = merge_usage(total_usage, usage)

    return ScratchpadBuildResult(
        scratchpad=assemble_scratchpad(batch_summaries),
        batches=batch_summaries,
        usage=total_usage,
    )


async def build_scratchpad(
    llm: ChatAnthropic,
    tool_name: str,
    tool_args: dict[str, Any],
    result: dict[str, Any],
) -> ScratchpadBuildResult:
    """Prepare raw parts from full tool data, then summarize each in a separate Haiku call."""
    raw_parts = prepare_raw_data_parts(result)
    return await summarize_raw_data_parts(llm, tool_name, tool_args, raw_parts)


def fallback_scratchpad(tool_name: str, result: dict[str, Any]) -> ScratchpadBuildResult:
    """Deterministic fallback when Haiku scratchpad is disabled or fails."""
    if "error" in result:
        text = f"tool={tool_name}\nerror={result['error']}"
        return ScratchpadBuildResult(scratchpad=text, batches=[text], usage=None)

    lines = [f"tool={tool_name}"]
    for key in ("count", "total_rules", "total_matching", "start_date", "end_date", "action_taken"):
        if key in result:
            lines.append(f"{key}={result[key]}")
    lines.append(f"top_level_keys={list(result.keys())}")
    text = "\n".join(lines)
    return ScratchpadBuildResult(scratchpad=text, batches=[text], usage=None)
