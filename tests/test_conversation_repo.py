"""ConversationRepository against a real Postgres. FeedbackRepository has its
own file, tests/test_feedback_repo.py, since it only needs a conversation to
exist, not this repo's ownership/staleness rules.

Zero coverage before this file. Two things are deliberately *not* exercised
here, both for safety rather than lack of interest:

`purge_stale()` and `storage_stats()` scan and delete/count across every
conversation in the table with no enterprise or synthetic-id scoping at all —
calling either against a shared dev database risks deleting or miscounting
real rows that have nothing to do with this test file. `storage_stats_for_user`
is scoped and is exercised instead.

A genuine `next_seq`/`append_message` race (two concurrent turns racing to
claim the same seq) is real — there is no DB unique constraint on
`(conversation_id, seq)` — but asserting a race actually happens under
`asyncio.gather` is inherently timing-dependent and would make this file
flaky. `test_append_message_does_not_enforce_a_unique_seq_per_conversation`
proves the same gap deterministically instead, by calling `append_message`
twice with the same explicit seq and showing neither call refuses.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_conversation_repo.py

Rows use a synthetic enterprise id well outside the real range and are removed
in the fixture (conversation rows cascade to their messages and feedback at
the DB level), so this is safe to run against a dev database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set RUN_DB_TESTS=1 with DATABASE_URL pointing at a migrated Postgres",
)

if RUN_DB_TESTS:
    from langchain_core.messages import HumanMessage
    from sqlalchemy import delete, select

    from app.core import registry
    from app.db import AsyncSessionLocal, engine
    from app.core.conversations.conversation_model import Conversation
    from app.core.conversations.conversation_repo import (
        CONVERSATION_STALE_AFTER,
        LEGACY_CONVERSATION_CUTOFF,
        ConversationRepository,
    )
    from app.core.conversations.message_model import Message

ENTERPRISE_ID = 999_600_001
OTHER_ENTERPRISE_ID = 999_600_002
USER_ID = 1
OTHER_USER_ID = 2


def run(coro):
    async def _scoped():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_scoped())


async def _wipe():
    async with AsyncSessionLocal() as s:
        # Message and Feedback both FK to conversation.id with ondelete=CASCADE,
        # so deleting the conversations is enough.
        await s.execute(
            delete(Conversation).where(
                Conversation.enterprise_id.in_((ENTERPRISE_ID, OTHER_ENTERPRISE_ID))
            )
        )
        await s.commit()


@pytest.fixture(autouse=True)
def clean():
    run(_wipe())
    yield
    run(_wipe())


@pytest.fixture(scope="module")
def agents():
    return registry.get("allocation").slug, "other-agent"


async def _create(*, agent_slug, user_id=USER_ID, enterprise_id=ENTERPRISE_ID,
                   is_internal_user=False, internal_user_id=None):
    async with AsyncSessionLocal() as s:
        conv = await ConversationRepository(s).create(
            user_id=user_id,
            enterprise_id=enterprise_id,
            agent_slug=agent_slug,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
        )
        await s.commit()
        return conv.id


async def _create_with_created_at(*, agent_slug, created_at):
    """Bypass create()'s default `now`, for staleness-boundary tests."""
    async with AsyncSessionLocal() as s:
        conv = Conversation(
            id=uuid.uuid4(),
            user_id=USER_ID,
            enterprise_id=ENTERPRISE_ID,
            agent_slug=agent_slug,
            created_at=created_at,
            updated_at=created_at,
        )
        s.add(conv)
        await s.commit()
        return conv.id


async def _owned(conversation_id, *, user_id=USER_ID, enterprise_id=ENTERPRISE_ID,
                  is_internal_user=False, internal_user_id=None, now=None):
    async with AsyncSessionLocal() as s:
        return await ConversationRepository(s).get_owned(
            conversation_id,
            user_id=user_id,
            enterprise_id=enterprise_id,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            now=now,
        )


# --- ownership key: all four fields must match ---------------------------------


def test_get_owned_requires_every_identity_field_to_match(agents):
    allocation, _ = agents
    conv_id = run(_create(agent_slug=allocation))

    assert run(_owned(conv_id)) is not None
    assert run(_owned(conv_id, is_internal_user=True)) is None, "internal flag must match"
    assert run(_owned(conv_id, user_id=OTHER_USER_ID)) is None
    assert run(_owned(conv_id, enterprise_id=OTHER_ENTERPRISE_ID)) is None


def test_shared_internal_account_ownership_is_split_by_internal_user_id(agents):
    allocation, _ = agents
    conv_id = run(
        _create(agent_slug=allocation, is_internal_user=True, internal_user_id="me")
    )

    assert run(_owned(conv_id, is_internal_user=True, internal_user_id="me")) is not None
    assert run(_owned(conv_id, is_internal_user=True, internal_user_id="colleague")) is None


def test_get_owned_does_not_scope_by_agent_slug(agents):
    """Documented behavior, not a fix: unlike `list_for_user`, `get_owned` takes
    no `agent_slug` at all — a conversation is reachable by direct id from any
    agent's mount as long as the rest of the identity matches."""
    allocation, _ = agents
    conv_id = run(_create(agent_slug=allocation))
    assert run(_owned(conv_id)) is not None  # no agent_slug param exists to narrow this


def test_list_for_user_scopes_by_agent_slug(agents):
    allocation, other_agent = agents
    allocation_conv = run(_create(agent_slug=allocation))
    run(_create(agent_slug=other_agent))

    async def go(agent_slug):
        async with AsyncSessionLocal() as s:
            rows = await ConversationRepository(s).list_for_user(
                user_id=USER_ID, enterprise_id=ENTERPRISE_ID, agent_slug=agent_slug
            )
            return [r.id for r in rows]

    assert run(go(allocation)) == [allocation_conv]


def test_exists_has_no_ownership_scoping(agents):
    allocation, _ = agents
    conv_id = run(_create(agent_slug=allocation))

    async def go():
        async with AsyncSessionLocal() as s:
            return await ConversationRepository(s).exists(conv_id)

    assert run(go()) is True


# --- staleness boundaries ------------------------------------------------------


def test_a_conversation_created_before_the_legacy_cutoff_expires_after_14_days(agents):
    allocation, _ = agents
    created_at = LEGACY_CONVERSATION_CUTOFF - timedelta(days=1)
    conv_id = run(_create_with_created_at(agent_slug=allocation, created_at=created_at))

    still_fresh = created_at + CONVERSATION_STALE_AFTER - timedelta(seconds=1)
    assert run(_owned(conv_id, now=still_fresh)) is not None

    just_stale = created_at + CONVERSATION_STALE_AFTER  # exact boundary: clause is strict `>`
    assert run(_owned(conv_id, now=just_stale)) is None


def test_a_conversation_created_on_or_after_the_cutoff_never_expires(agents):
    allocation, _ = agents
    conv_id = run(
        _create_with_created_at(agent_slug=allocation, created_at=LEGACY_CONVERSATION_CUTOFF)
    )

    far_future = LEGACY_CONVERSATION_CUTOFF + timedelta(days=3650)
    assert run(_owned(conv_id, now=far_future)) is not None


# --- the two documented, un-fixed gaps ------------------------------------------


def test_next_seq_counts_partial_messages_that_load_messages_then_skips(agents):
    allocation, _ = agents
    conv_id = run(_create(agent_slug=allocation))

    async def go():
        async with AsyncSessionLocal() as s:
            repo = ConversationRepository(s)
            conv = await s.get(Conversation, conv_id)
            await repo.append_message(
                conv, seq=0, message=HumanMessage(content="hi"), status="partial"
            )
            await s.commit()
            return await repo.next_seq(conv_id), await repo.load_messages(conv_id)

    next_seq, replayed = run(go())
    assert next_seq == 1, "next_seq counts every row regardless of status"
    assert replayed == [], "load_messages replays only status='complete' rows"


def test_append_message_does_not_enforce_a_unique_seq_per_conversation(agents):
    allocation, _ = agents
    conv_id = run(_create(agent_slug=allocation))

    async def go():
        async with AsyncSessionLocal() as s:
            repo = ConversationRepository(s)
            conv = await s.get(Conversation, conv_id)
            await repo.append_message(conv, seq=0, message=HumanMessage(content="first"))
            await repo.append_message(conv, seq=0, message=HumanMessage(content="also first"))
            await s.commit()

        async with AsyncSessionLocal() as s:
            result = await s.execute(
                select(Message).where(Message.conversation_id == conv_id, Message.seq == 0)
            )
            return result.scalars().all()

    rows = run(go())
    assert len(rows) == 2, "no unique constraint on (conversation_id, seq) rejects the duplicate"


# --- storage_stats_for_user: scoped, safe to assert exactly --------------------


def test_storage_stats_for_user_counts_only_that_users_conversations(agents):
    allocation, other_agent = agents
    conv_id = run(_create(agent_slug=allocation))
    run(_create(agent_slug=other_agent))  # same user: both should count
    run(_create(agent_slug=allocation, user_id=OTHER_USER_ID))  # different user: must not

    async def go():
        async with AsyncSessionLocal() as s:
            repo = ConversationRepository(s)
            conv = await s.get(Conversation, conv_id)
            await repo.append_message(conv, seq=0, message=HumanMessage(content="hi"))
            await s.commit()
            return await repo.storage_stats_for_user(
                user_id=USER_ID, enterprise_id=ENTERPRISE_ID
            )

    stats = run(go())
    assert stats["conversation_count"] == 2
    assert stats["message_count"] == 1
