"""Titling is billed, so its tokens must reach the ledger.

`generate_conversation_title` used to return just the title and drop
`response.usage_metadata`. The call is a real Haiku request: Anthropic charged
for it and Langfuse traced it, but `llm_usage_ledger` never saw it, so it
counted against nobody's cap and left a permanent unexplained delta in any
reconciliation against Langfuse.

These tests pin the contract that fixes it — usage comes back even when the
title itself is unusable, because the tokens were spent either way.
"""

from __future__ import annotations

import asyncio

from app.core.runtime.context import merge_usage
from app.core.conversations import title as title_service

USAGE = {
    "input_tokens": 612,
    "output_tokens": 9,
    "total_tokens": 621,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
}


class _FakeResponse:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage_metadata = usage


def _patch_model(monkeypatch, response=None, raises=None):
    class _FakeModel:
        async def ainvoke(self, _messages, config=None):
            if raises is not None:
                raise raises
            return response

    monkeypatch.setattr(title_service, "create_secondary_chat_model", lambda: _FakeModel())


class TestReturnsUsage:
    def test_returns_title_and_usage(self, monkeypatch):
        _patch_model(monkeypatch, _FakeResponse("Refund status query", USAGE))
        title, usage = asyncio.run(
            title_service.generate_conversation_title(user_message="where is my refund")
        )
        assert title == "Refund status query"
        assert usage == USAGE

    def test_usage_survives_an_unusable_title(self, monkeypatch):
        """A blank answer still cost tokens; dropping them would under-bill."""
        _patch_model(monkeypatch, _FakeResponse("   ", USAGE))
        title, usage = asyncio.run(
            title_service.generate_conversation_title(user_message="where is my refund")
        )
        assert title is None
        assert usage == USAGE

    def test_title_is_truncated_but_usage_is_not_affected(self, monkeypatch):
        _patch_model(monkeypatch, _FakeResponse("x" * 400, USAGE))
        title, usage = asyncio.run(
            title_service.generate_conversation_title(user_message="hello")
        )
        assert len(title) == title_service._MAX_TITLE_CHARS
        assert usage == USAGE


class TestNoCallMade:
    def test_empty_message_makes_no_call_and_reports_no_usage(self, monkeypatch):
        _patch_model(monkeypatch, raises=AssertionError("must not be called"))
        assert asyncio.run(
            title_service.generate_conversation_title(user_message="   ")
        ) == (None, None)

    def test_failure_reports_no_usage(self, monkeypatch):
        """Nothing is recoverable from an exception, so this path under-counts
        by design rather than guessing at a token count."""
        _patch_model(monkeypatch, raises=RuntimeError("anthropic down"))
        assert asyncio.run(
            title_service.generate_conversation_title(user_message="where is my refund")
        ) == (None, None)


class TestMergesIntoSecondaryUsage:
    """The router folds title usage into `secondary_usage`, which the ledger
    prices at the secondary model's rate — the same model titling uses."""

    def test_adds_to_existing_scratchpad_usage(self):
        scratchpad = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        merged = merge_usage(scratchpad, USAGE)
        assert merged["input_tokens"] == 712
        assert merged["output_tokens"] == 29

    def test_is_the_whole_figure_when_there_was_no_scratchpad_call(self):
        assert merge_usage({}, USAGE) == USAGE

    def test_absent_title_usage_changes_nothing(self):
        """Every turn after the first has no titling call; those must settle
        exactly as they did before."""
        scratchpad = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        assert merge_usage(scratchpad, None) == scratchpad
