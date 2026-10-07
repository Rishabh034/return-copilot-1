from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_anthropic import ChatAnthropic

from app.clients.observability import langchain_trace_config
from app.core.content import message_text
from app.config import settings
from app.core.runtime.context import merge_usage, serialize_usage

logger = logging.getLogger(__name__)

_SUMMARIZE_PART_PROMPT = """Extract decision-relevant evidence from the supplied source segment.
Preserve only what an allocation analyst needs to decide next steps.

Rules:
- Record only decision-relevant fields: stage/status, rule IDs/names and their match outcome,
  courier/account identifiers, and any explicit pass/fail or error reason
- Omit fields that are purely descriptive, redundant, or not useful for deciding next steps
- Preserve field names, IDs, and values you do record exactly — do NOT interpret or recommend
- Use compact key: value / bullet structure
- This part is independent — summarize only what is shown below

Source: {tool_name} ({args_summary})
Segment {part_index}/{part_total}:
{data_part}

Return only the compact evidence summary."""


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
            # Greedily pack items into as few chunks as fit under the limit —
            # one item per chunk wastes a whole Haiku call on items that are
            # a fraction of scratchpad_chunk_chars (e.g. dozens of small rules).
            batch: list[Any] = []
            for item in value:
                candidate = batch + [item]
                if len(json.dumps({key: candidate}, default=str)) <= limit:
                    batch = candidate
                    continue

                if batch:
                    chunks.append(json.dumps({key: batch}, default=str))
                    batch = [item]
                    if len(json.dumps({key: batch}, default=str)) <= limit:
                        continue

                # this single item alone exceeds the limit — slice it directly
                piece = json.dumps({key: [item]}, default=str)
                for offset in range(0, len(piece), limit):
                    chunks.append(piece[offset : offset + limit])
                batch = []
            if batch:
                chunks.append(json.dumps({key: batch}, default=str))
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
        "source": tool_name,
        "source_segments": len(raw_data_parts),
        "summary_segments": len(batches),
        "summary_chars": len(scratchpad),
        "note": (
            "Use inspect_evidence(result_id, keywords=..., batch_index=...) to examine "
            "relevant evidence, or inspect_source_segment(result_id, part_index) when a "
            "specific source segment is needed."
        ),
    }
    if len(scratchpad) <= inline_limit:
        envelope["evidence_summary"] = scratchpad
    else:
        envelope["summary_available"] = True
    return envelope


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
    conversation_id: str | None = None,
    agent_slug: str | None = None,
) -> ScratchpadBuildResult:
    """Run one new Haiku call per stored raw data part to build scratchpad batch summaries."""
    if not raw_data_parts:
        return ScratchpadBuildResult(scratchpad="", batches=[], usage=None)

    total_usage: dict[str, int] | None = None
    args_text = _args_summary(tool_args)
    part_total = len(raw_data_parts)

    # A part already smaller than the summarization prompt itself is its own best=
    passthrough_limit = settings.scratchpad_passthrough_max_chars
    llm_indexes = [
        index for index, part in enumerate(raw_data_parts) if len(part) > passthrough_limit
    ]

    logger.info(
        "Scratchpad summarization tool=%s: %d stored data parts → %d Haiku calls "
        "(%d passed through verbatim, max %d concurrent)",
        tool_name,
        part_total,
        len(llm_indexes),
        part_total - len(llm_indexes),
        settings.scratchpad_max_concurrent_summaries,
    )

    semaphore = asyncio.Semaphore(max(1, settings.scratchpad_max_concurrent_summaries))

    async def _summarize_one(index: int):
        prompt = _SUMMARIZE_PART_PROMPT.format(
            tool_name=tool_name,
            args_summary=args_text,
            part_index=index + 1,
            part_total=part_total,
            data_part=raw_data_parts[index],
        )
        try:
            # Bound the fan-out: an unbounded gather over dozens of parts
            # self-throttles into retries and long tail latency.
            async with semaphore:
                return await llm.ainvoke(
                    [HumanMessage(content=prompt)],
                    config=langchain_trace_config(conversation_id, agent_slug=agent_slug),
                )
        except Exception:
            logger.exception(
                "Scratchpad Haiku summarization failed on part %d/%d for tool %s",
                index + 1,
                part_total,
                tool_name,
            )
            raise

    responses = await asyncio.gather(*[_summarize_one(index) for index in llm_indexes])

    batch_summaries: list[str] = list(raw_data_parts)
    for index, response in zip(llm_indexes, responses):
        batch_summaries[index] = message_text(response.content).strip()
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
    conversation_id: str | None = None,
) -> ScratchpadBuildResult:
    """Prepare raw parts from full tool data, then summarize each in a separate Haiku call."""
    raw_parts = prepare_raw_data_parts(result)
    return await summarize_raw_data_parts(
        llm, tool_name, tool_args, raw_parts, conversation_id=conversation_id
    )


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
