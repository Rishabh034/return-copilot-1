"""Last-mile filtering of assistant text, applied at every boundary it leaves by.

The Return & Exchange fork masked PII on the streamed delta only:

    yield ("text_delta", {"delta": mask_pii(text_delta)})
    accumulated = chunk if accumulated is None else accumulated + chunk

`accumulated` is what gets persisted, so the browser showed masked text while
the database stored the raw value — backwards for a data-protection control.
Masking each delta independently also cannot match a pattern split across two
chunks, which is most of them: Anthropic does not stream on token boundaries
that respect phone numbers.

So a filter is applied at both boundaries, and the streaming form is allowed to
hold text back until it can be sure. `hold` is flushed at end of turn.
"""

from __future__ import annotations

from typing import Protocol


class OutputFilter(Protocol):
    def filter_stream(self, buffer: str) -> tuple[str, str]:
        """Split a buffer into (safe to emit now, hold until more arrives)."""

    def filter_final(self, text: str) -> str:
        """Filter the complete text, before it is persisted."""


class NullFilter:
    """No filtering. What an agent gets when it declares none.

    `stream_hold_chars = 0` matters as much as the no-op methods: buffering to
    look for patterns that can never match would delay every token for every
    agent that filters nothing, which is most of them.
    """

    stream_hold_chars = 0

    def filter_stream(self, buffer: str) -> tuple[str, str]:
        return buffer, ""

    def filter_final(self, text: str) -> str:
        return text


NULL_FILTER: OutputFilter = NullFilter()


class StreamFilter:
    """Applies `filter_final` to a stream, holding back a possible partial match.

    An agent declares `stream_hold_chars` and the pattern-level work in
    `filter_final`, and gets correct streaming behaviour for free: the tail that could still grow into a match is
    withheld, and `flush()` releases whatever is left when the turn ends.

    `hold_chars` MUST exceed the longest string the filter can match, or a match
    straddling the emit boundary is cut in half and passes through unfiltered —
    the same bug as filtering each delta, just rarer and therefore worse. The
    default of 64 clears an email address, an international phone number and any
    tracking number we issue. Raise it rather than tune it down.
    """

    def __init__(self, output_filter: OutputFilter, hold_chars: int | None = None) -> None:
        self._filter = output_filter
        if hold_chars is None:
            hold_chars = getattr(output_filter, "stream_hold_chars", 64)
        self._hold_chars = hold_chars
        self._pending = ""

    def push(self, delta: str) -> str:
        # A filter that holds nothing back must not delay anything: pass the
        # delta straight through so streaming granularity is unchanged.
        if self._hold_chars == 0:
            return self._filter.filter_final(delta)
        self._pending += delta
        if len(self._pending) <= self._hold_chars:
            return ""
        emit, self._pending = self._pending[: -self._hold_chars], self._pending[-self._hold_chars :]
        return self._filter.filter_final(emit)

    def flush(self) -> str:
        remainder, self._pending = self._pending, ""
        return self._filter.filter_final(remainder) if remainder else ""
