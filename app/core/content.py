"""Plain-text extraction from LangChain message content (a str or a list of blocks)."""

from __future__ import annotations


def _join_blocks(content: str | list, block_type: str) -> str:
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get(block_type, "")
        for block in content
        if isinstance(block, dict) and block.get("type") == block_type
    )


def message_text(content: str | list) -> str:
    """The visible text of a message: the string itself, or its joined text blocks."""
    if isinstance(content, str):
        return content
    return _join_blocks(content, "text")


def message_thinking(content: str | list) -> str:
    """The model's extended-thinking text, from an AI message's thinking blocks."""
    return _join_blocks(content, "thinking")
