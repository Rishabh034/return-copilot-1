"""FeedbackRepository against a real Postgres. Zero coverage before this file,
and no ownership check inside `create()` itself either — it relied entirely
on its one caller (app/core/routers/feedback.py) having already checked via
`ConversationRepository.get_owned`. `create()` now checks independently too.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_feedback_repo.py

Rows use a synthetic enterprise id well outside the real range; feedback rows
cascade-delete with their conversation at the DB level, so wiping the
conversations is enough and this is safe to run against a dev database.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set RUN_DB_TESTS=1 with DATABASE_URL pointing at a migrated Postgres",
)

if RUN_DB_TESTS:
    from sqlalchemy import delete, select

    from app.core import registry
    from app.db import AsyncSessionLocal, engine
    from app.core.conversations.conversation_model import Conversation
    from app.core.conversations.conversation_repo import ConversationRepository
    from app.core.conversations.feedback_model import Feedback
    from app.core.conversations.feedback_repo import FeedbackRepository

ENTERPRISE_ID = 999_700_001
OTHER_ENTERPRISE_ID = 999_700_002
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


async def _conversation(
    *,
    agent_slug,
    user_id=USER_ID,
    enterprise_id=ENTERPRISE_ID,
    is_internal_user=False,
    internal_user_id=None,
):
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


def test_feedback_create_persists_the_given_fields():
    allocation = registry.get("allocation").slug
    conv_id = run(_conversation(agent_slug=allocation))

    async def go():
        async with AsyncSessionLocal() as s:
            row = await FeedbackRepository(s).create(
                conversation_id=conv_id,
                seq=0,
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                positive=True,
                comment="great answer",
            )
            await s.commit()
            return row.id

    feedback_id = run(go())

    async def read():
        async with AsyncSessionLocal() as s:
            return await s.get(Feedback, feedback_id)

    row = run(read())
    assert (row.positive, row.comment) == (True, "great answer")


def test_feedback_create_defaults_comment_and_internal_fields():
    allocation = registry.get("allocation").slug
    conv_id = run(_conversation(agent_slug=allocation))

    async def go():
        async with AsyncSessionLocal() as s:
            row = await FeedbackRepository(s).create(
                conversation_id=conv_id,
                seq=0,
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                positive=False,
                comment=None,
            )
            await s.commit()
            return row.id, row.comment, row.internal_user_id, row.is_internal_user

    feedback_id, comment, internal_user_id, is_internal_user = run(go())
    assert feedback_id is not None
    assert comment is None
    assert internal_user_id is None
    assert is_internal_user is False


STAFF = {"is_internal_user": True, "internal_user_id": "me"}


@pytest.mark.parametrize(
    "owner, caller",
    [
        pytest.param({}, {"user_id": OTHER_USER_ID}, id="other-user"),
        pytest.param({}, {"enterprise_id": OTHER_ENTERPRISE_ID}, id="other-enterprise"),
        # Only the flag differs. The router never sends this pair (it requires
        # an internal_user_id for internal callers), which is exactly why the
        # repository's own flag check is otherwise untested.
        pytest.param({}, {"is_internal_user": True}, id="internal-flag-only"),
        pytest.param({}, STAFF, id="staff-on-customer-chat"),
        pytest.param(STAFF, {}, id="customer-on-staff-chat"),
        pytest.param(
            STAFF, {**STAFF, "internal_user_id": "colleague"}, id="other-staff-member"
        ),
    ],
)
def test_feedback_create_refuses_a_conversation_the_caller_does_not_own(owner, caller):
    """`create()` checks ownership itself, independent of whatever the router
    already checked.

    Each case differs from the owner in exactly one identity field. A caller
    that differed in several at once would be refused even if all but one of
    the checks were deleted, so the test could not tell them apart.
    """
    from app.exceptions import NotFoundError

    allocation = registry.get("allocation").slug
    conv_id = run(_conversation(agent_slug=allocation, **owner))

    identity = {
        "user_id": USER_ID,
        "enterprise_id": ENTERPRISE_ID,
        "is_internal_user": False,
        "internal_user_id": None,
        **caller,
    }

    async def go():
        async with AsyncSessionLocal() as s:
            await FeedbackRepository(s).create(
                conversation_id=conv_id,
                seq=0,
                positive=False,
                comment="attached to someone else's conversation",
                **identity,
            )

    with pytest.raises(NotFoundError):
        run(go())


def test_staff_can_leave_feedback_on_their_own_chat():
    """The positive half of the staff cases above, so they are not passing
    merely because staff can never leave feedback at all."""
    allocation = registry.get("allocation").slug
    conv_id = run(_conversation(agent_slug=allocation, **STAFF))

    async def go():
        async with AsyncSessionLocal() as s:
            row = await FeedbackRepository(s).create(
                conversation_id=conv_id,
                seq=0,
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                positive=True,
                comment=None,
                **STAFF,
            )
            await s.commit()
            return row.internal_user_id

    assert run(go()) == "me"


def test_feedback_create_refuses_an_unknown_conversation_id():
    from app.exceptions import NotFoundError

    async def go():
        async with AsyncSessionLocal() as s:
            await FeedbackRepository(s).create(
                conversation_id=uuid.uuid4(),
                seq=0,
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                positive=True,
                comment=None,
            )

    with pytest.raises(NotFoundError):
        run(go())


def test_feedback_has_no_uniqueness_per_conversation_and_seq():
    """A second, contradictory vote on the same turn is not rejected either —
    both rows persist, so a reader summing feedback must decide for itself how
    to reconcile repeats rather than relying on the schema to prevent them."""
    allocation = registry.get("allocation").slug
    conv_id = run(_conversation(agent_slug=allocation))

    async def go():
        async with AsyncSessionLocal() as s:
            repo = FeedbackRepository(s)
            await repo.create(
                conversation_id=conv_id, seq=0, user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID, positive=True, comment=None,
            )
            await repo.create(
                conversation_id=conv_id, seq=0, user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID, positive=False, comment="changed my mind",
            )
            await s.commit()
            result = await s.execute(
                select(Feedback).where(Feedback.conversation_id == conv_id, Feedback.seq == 0)
            )
            return result.scalars().all()

    rows = run(go())
    assert len(rows) == 2
