"""UserChats._scope — which chats a staff listing is allowed to return.

`_scope` deliberately treats `None` as "do not filter" rather than "match
NULL", because comparing the non-nullable `is_internal_user` column to None
yields `IS NULL` and silently matches nothing. That makes every filter here
opt-in, which puts the weight of the module's isolation on a single
unconditional clause: `enterprise_id`.

That asymmetry is what this file pins. Every other filter may be omitted and
the answer merely widens; omit the enterprise scope and one customer's chats
appear in another's listing. `test_a_listing_never_crosses_enterprises` is the
one to keep — it leaves every optional argument at its default, which is the
shape a caller most easily writes by accident.

`/admin/user-chats` is gated on ClickPost staff rather than on the caller
owning the rows, so nothing below asserts ownership; this module is not the
layer that enforces it.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_user_chats.py

Rows use synthetic enterprise ids well outside the real range and are removed
in the fixture, so this is safe to run against a dev database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set RUN_DB_TESTS=1 with DATABASE_URL pointing at a migrated Postgres",
)

if RUN_DB_TESTS:
    from sqlalchemy import delete

    from app.db import AsyncSessionLocal, engine
    from app.core.conversations.conversation_model import Conversation
    from app.core.conversations.user_chats import UserChats

ENTERPRISE_ID = 999_600_001
OTHER_ENTERPRISE_ID = 999_600_002
CUSTOMER = 7
SHARED_STAFF_ACCOUNT = 1
AGENT = "allocation"

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def run(coro):
    """Fresh loop per test, then drop the pool — see test_budget_repo_integration."""

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
                Conversation.enterprise_id.in_([ENTERPRISE_ID, OTHER_ENTERPRISE_ID])
            )
        )
        await s.commit()


@pytest.fixture(autouse=True)
def clean():
    run(_wipe())
    yield
    run(_wipe())


async def _chat(
    *,
    enterprise_id=ENTERPRISE_ID,
    user_id=CUSTOMER,
    is_internal_user=False,
    internal_user_id=None,
    created_at=NOW,
) -> uuid.UUID:
    async with AsyncSessionLocal() as s:
        conv = Conversation(
            id=uuid.uuid4(),
            user_id=user_id,
            enterprise_id=enterprise_id,
            agent_slug=AGENT,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            created_at=created_at,
            updated_at=created_at,
        )
        s.add(conv)
        await s.commit()
        return conv.id


async def _list(**filters):
    async with AsyncSessionLocal() as s:
        rows = await UserChats(s).list_chats(enterprise_id=ENTERPRISE_ID, **filters)
        return {r.conversation_id for r in rows}


async def _count(**filters):
    async with AsyncSessionLocal() as s:
        return await UserChats(s).count_chats(enterprise_id=ENTERPRISE_ID, **filters)


# --- the clause that is never optional ---------------------------------------


def test_a_listing_never_crosses_enterprises():
    """Every optional filter left at its default — the widest possible query.

    `_scope` adds a clause only when its argument is not None, so this is the
    call that exercises the fewest filters. The enterprise scope is the one
    that still has to hold.
    """

    async def go():
        mine = await _chat()
        theirs = await _chat(enterprise_id=OTHER_ENTERPRISE_ID)
        return mine, theirs, await _list()

    mine, theirs, listed = run(go())
    assert mine in listed
    assert theirs not in listed, "another enterprise's chat reached this listing"


def test_an_enterprise_with_no_chats_sees_nothing_rather_than_everything():
    """A missing scope usually shows up as "empty query returns the world"."""

    async def go():
        await _chat(enterprise_id=OTHER_ENTERPRISE_ID)
        await _chat(enterprise_id=OTHER_ENTERPRISE_ID)
        return await _list(), await _count()

    listed, total = run(go())
    assert listed == set()
    assert total == 0


# --- the opt-in filters ------------------------------------------------------


def test_an_unfiltered_listing_includes_both_staff_and_customer_chats():
    """`is_internal_user=None` means "do not filter", not "customers only".

    Documented behaviour rather than an oversight: the staff surface wants
    both by default. Pinned so narrowing it later is a deliberate change.
    """

    async def go():
        customer = await _chat(is_internal_user=False)
        staff = await _chat(
            user_id=SHARED_STAFF_ACCOUNT, is_internal_user=True, internal_user_id="me"
        )
        return customer, staff, await _list()

    customer, staff, listed = run(go())
    assert listed == {customer, staff}


def test_filtering_to_customers_excludes_staff_chats():
    async def go():
        customer = await _chat(is_internal_user=False)
        await _chat(
            user_id=SHARED_STAFF_ACCOUNT, is_internal_user=True, internal_user_id="me"
        )
        return customer, await _list(is_internal_user=False)

    customer, listed = run(go())
    assert listed == {customer}


def test_filtering_to_staff_excludes_customer_chats():
    async def go():
        await _chat(is_internal_user=False)
        staff = await _chat(
            user_id=SHARED_STAFF_ACCOUNT, is_internal_user=True, internal_user_id="me"
        )
        return staff, await _list(is_internal_user=True)

    staff, listed = run(go())
    assert listed == {staff}


def test_the_shared_staff_account_is_split_by_internal_user_id():
    """ClickPost staff share one Django user_id; the person is internal_user_id.

    Filtering on user_id alone would merge every employee into one listing.
    """

    async def go():
        mine = await _chat(
            user_id=SHARED_STAFF_ACCOUNT, is_internal_user=True, internal_user_id="me"
        )
        await _chat(
            user_id=SHARED_STAFF_ACCOUNT,
            is_internal_user=True,
            internal_user_id="colleague",
        )
        return mine, await _list(is_internal_user=True, internal_user_id="me")

    mine, listed = run(go())
    assert listed == {mine}


def test_the_date_window_is_half_open():
    """`>= frm` and `< to`, so a chat created exactly at `to` belongs to the
    next window rather than being counted in both."""

    async def go():
        before = await _chat(created_at=NOW - timedelta(days=1))
        inside = await _chat(created_at=NOW)
        at_end = await _chat(created_at=NOW + timedelta(days=1))
        listed = await _list(frm=NOW, to=NOW + timedelta(days=1))
        return before, inside, at_end, listed

    before, inside, at_end, listed = run(go())
    assert listed == {inside}
    assert before not in listed
    assert at_end not in listed, "`to` is exclusive"


# --- count and list must not disagree ----------------------------------------


def test_count_and_list_apply_the_same_filters():
    """`count_chats` drives the page count the API validates `page` against.

    If the two ever scoped differently, a caller could be refused a page that
    the listing would happily have served, or served a page the count says
    does not exist.
    """

    async def go():
        await _chat(is_internal_user=False)
        await _chat(is_internal_user=False)
        await _chat(
            user_id=SHARED_STAFF_ACCOUNT, is_internal_user=True, internal_user_id="me"
        )
        await _chat(enterprise_id=OTHER_ENTERPRISE_ID)

        out = {}
        for label, filters in (
            ("unfiltered", {}),
            ("customers", {"is_internal_user": False}),
            ("staff", {"is_internal_user": True}),
            ("one person", {"is_internal_user": True, "internal_user_id": "me"}),
        ):
            out[label] = (len(await _list(**filters)), await _count(**filters))
        return out

    for label, (listed, counted) in run(go()).items():
        assert listed == counted, f"{label}: listing returned {listed}, count said {counted}"
