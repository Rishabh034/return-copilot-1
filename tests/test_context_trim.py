"""trim_messages_to_budget — the last thing to touch a prompt before it ships.

Pure arithmetic over `max_context_tokens - reserved_output_tokens -
system_prompt_tokens`, so there is no database and no LLM here: this file needs
no RUN_DB_TESTS gate and runs in milliseconds.

It earns a dedicated file because both failure directions are silent. Trim too
little and Anthropic rejects the whole request; trim too much, or trim the
wrong end, and the model answers confidently from a mutilated conversation —
nobody sees an error, the answer is just quietly wrong.

Message costs are measured with `message_token_count` rather than hardcoded.
`count_tokens_approximately` is a LangChain implementation detail, and pinning
its arithmetic here would make this file fail on a LangChain upgrade for a
reason that has nothing to do with trimming.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from app.core.runtime import context
from app.core.runtime.context import message_token_count, trim_messages_to_budget


def _conversation(n: int, *, size: int = 200) -> list[HumanMessage]:
    """`n` messages of identical cost, each carrying its own index."""
    return [HumanMessage(content=f"[{i:03d}] " + "x" * size) for i in range(n)]


def _indices(messages: list[HumanMessage]) -> list[int]:
    return [int(m.content[1:4]) for m in messages]


@pytest.fixture(autouse=True)
def _no_output_reservation(monkeypatch):
    """Take the output reservation out of the arithmetic by default.

    Every test but `test_output_reservation_is_subtracted_from_the_window` is
    about which messages get selected, not about that subtraction.
    """
    monkeypatch.setattr(context.settings, "reserved_output_tokens", 0)


def _budget(monkeypatch, *, tokens: int, tail: int = 4) -> None:
    monkeypatch.setattr(context.settings, "max_context_tokens", tokens)
    monkeypatch.setattr(context.settings, "protected_tail_messages", tail)


# --- the no-op paths ---------------------------------------------------------


def test_a_conversation_within_budget_is_returned_untouched(monkeypatch):
    messages = _conversation(10)
    _budget(monkeypatch, tokens=10_000)

    # Identity, not equality: the common case must not pay to rebuild the list.
    assert trim_messages_to_budget(messages, system_prompt_tokens=0) is messages


def test_a_conversation_too_short_to_trim_is_left_alone(monkeypatch):
    """With only a head and a protected tail there is nothing in between.

    The guard matters because the slice arithmetic below it would otherwise
    produce a head and tail that overlap, duplicating messages.
    """
    messages = _conversation(5)
    unit = message_token_count(messages[0])
    _budget(monkeypatch, tokens=unit, tail=4)  # far too small; still no room to cut

    assert trim_messages_to_budget(messages, system_prompt_tokens=0) is messages


# --- what survives a trim ----------------------------------------------------


def test_the_opening_message_and_the_recent_tail_survive_a_trim(monkeypatch):
    messages = _conversation(20)
    unit = message_token_count(messages[0])
    _budget(monkeypatch, tokens=unit * 10, tail=4)

    kept = _indices(trim_messages_to_budget(messages, system_prompt_tokens=0))
    assert kept[0] == 0, "the opening message anchors the conversation"
    assert kept[-4:] == [16, 17, 18, 19], "the most recent turns are protected"
    assert len(kept) < 20, "something was actually cut"


def test_the_survivors_of_the_middle_are_the_most_recent_ones(monkeypatch):
    """The trimmable span is consumed newest-first.

    What survives must therefore be contiguous with the protected tail. A
    scattered or oldest-first selection would leave the model a conversation
    with holes punched through the part it needs most.
    """
    messages = _conversation(20)
    unit = message_token_count(messages[0])
    _budget(monkeypatch, tokens=unit * 10, tail=4)

    kept = _indices(trim_messages_to_budget(messages, system_prompt_tokens=0))
    assert kept[1:-4] == [11, 12, 13, 14, 15]


def test_the_result_is_an_ordered_subset_with_no_duplicates(monkeypatch):
    messages = _conversation(20)
    unit = message_token_count(messages[0])
    _budget(monkeypatch, tokens=unit * 10, tail=4)

    kept = _indices(trim_messages_to_budget(messages, system_prompt_tokens=0))
    assert kept == sorted(kept), "a reordered conversation is a corrupted one"
    assert len(kept) == len(set(kept)), "a repeated turn reads as the user saying it twice"
    assert set(kept) <= set(range(20)), "trimming must not invent messages"


def test_trimming_never_returns_more_messages_than_it_was_given(monkeypatch):
    """`annotated[-0:]` is the whole list, not an empty one.

    With `protected_tail_messages = 0` the tail slice silently selects every
    message, so the "trimmed" result is the opening message plus a second copy
    of the entire conversation — a trimmer that makes the prompt bigger, which
    is precisely the failure it exists to prevent. The sibling line computing
    `trimmable` guards this exact case; the tail slice must too.
    """
    messages = _conversation(8)
    unit = message_token_count(messages[0])
    _budget(monkeypatch, tokens=unit * 3, tail=0)

    kept = _indices(trim_messages_to_budget(messages, system_prompt_tokens=0))
    assert len(kept) <= len(messages)
    assert len(kept) == len(set(kept))


def test_a_budget_smaller_than_the_protected_messages_still_terminates(monkeypatch):
    """Head and tail are unconditional, so they can bust the budget on their own.

    Returning them over budget beats returning an empty prompt, but it is a
    deliberate choice rather than an accident — pin it so it stays one.
    """
    messages = _conversation(20)
    _budget(monkeypatch, tokens=1, tail=4)

    kept = _indices(trim_messages_to_budget(messages, system_prompt_tokens=0))
    assert kept == [0, 16, 17, 18, 19]


# --- what counts against the budget ------------------------------------------


def test_the_system_prompt_is_charged_against_the_same_budget(monkeypatch):
    """A long system prompt has to cost the conversation something.

    If it were free, growing the prompt would quietly push the real total past
    the model's window even though the trimmer reported success.
    """
    messages = _conversation(20)
    unit = message_token_count(messages[0])
    _budget(monkeypatch, tokens=unit * 12, tail=4)

    roomy = trim_messages_to_budget(messages, system_prompt_tokens=0)
    cramped = trim_messages_to_budget(messages, system_prompt_tokens=unit * 6)
    assert len(cramped) < len(roomy)


def test_output_reservation_is_subtracted_from_the_window(monkeypatch):
    """Reserving room for the reply is what stops a full prompt leaving the
    model no tokens to answer in."""
    messages = _conversation(20)
    unit = message_token_count(messages[0])
    _budget(monkeypatch, tokens=unit * 12, tail=4)

    generous = trim_messages_to_budget(messages, system_prompt_tokens=0)
    monkeypatch.setattr(context.settings, "reserved_output_tokens", unit * 6)
    stingy = trim_messages_to_budget(messages, system_prompt_tokens=0)
    assert len(stingy) < len(generous)
