"""UserTokenRepository — the 24h rolling rate limiter, gating every turn.

This is the busiest limit in the system and, until now, had no test coverage
at all. Two things earn a dedicated file rather than folding into
tests/test_budget_repo_integration.py: the "only growth over the largest
prompt counts" billing rule (easy to get backwards, expensive if so — it
silently either double-bills every turn or never bills anything), and the new
per-agent isolation this repo just gained (agent_slug is now part of the
active-window uniqueness — see migration 3115dd2060f1). `agent_slug` is a
plain string column with no FK to the agent registry (see
user_token_model.py), so the isolation tests use one real registered slug
(`allocation`) plus a synthetic second one, rather than needing a second
agent registered just for tests.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_user_token_repo.py

Rows use a synthetic enterprise id well outside the real range and are removed
in the fixture, so this is safe to run against a dev database.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

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
    from app.core.billing.user_token_model import UserToken
    from app.core.billing.user_token_repo import UserTokenRepository

ENTERPRISE_ID = 999_400_001
USER_ID = 1
OTHER_USER_ID = 2


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
        await s.execute(delete(UserToken).where(UserToken.enterprise_id == ENTERPRISE_ID))
        await s.commit()


@pytest.fixture(autouse=True)
def clean():
    run(_wipe())
    yield
    run(_wipe())


@pytest.fixture(scope="module")
def agents():
    """One real registered slug, plus a synthetic second one for isolation
    tests — agent_slug has no FK to the registry, so a literal is sufficient.
    """
    return registry.get("allocation").slug, "other-agent"


IDENTITIES = [
    pytest.param({"is_internal_user": False, "internal_user_id": None}, id="external"),
    pytest.param({"is_internal_user": True, "internal_user_id": "employee-1"}, id="internal"),
]


@pytest.fixture(params=IDENTITIES)
def identity(request):
    """External customer vs. ClickPost staff.

    Every lifecycle rule below (limit enforcement, token dedup, extra-token
    ceiling, concurrent locking) must hold identically for both — an internal
    actor is not a different code path, just a different identity key.
    """
    return request.param


async def _owned(*, user_id=USER_ID, agent_slug, is_internal_user=False, internal_user_id=None):
    async with AsyncSessionLocal() as s:
        return await UserTokenRepository(s).get_owned(
            user_id=user_id,
            enterprise_id=ENTERPRISE_ID,
            agent_slug=agent_slug,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
        )


async def _create(
    *,
    user_id=USER_ID,
    agent_slug,
    is_internal_user=False,
    internal_user_id=None,
    now=None,
    max_input_tokens=1_000,
    max_output_tokens=500,
):
    async with AsyncSessionLocal() as s:
        row = await UserTokenRepository(s).create(
            user_id=user_id,
            enterprise_id=ENTERPRISE_ID,
            agent_slug=agent_slug,
            is_internal_user=is_internal_user,
            internal_user_id=internal_user_id,
            now=now,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
        )
        await s.commit()
        return row.id


# --- lifecycle ---------------------------------------------------------------


def test_get_or_create_is_idempotent_within_a_window(agents, identity):
    allocation, _ = agents

    async def go():
        async with AsyncSessionLocal() as s:
            repo = UserTokenRepository(s)
            first = await repo.get_or_create(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            second = await repo.get_or_create(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            await s.commit()
            return first.id, second.id

    first, second = run(go())
    assert first == second


def test_exists_reflects_only_active_rows_for_the_exact_identity(agents, identity):
    allocation, _ = agents
    run(_create(agent_slug=allocation, **identity))

    async def go():
        async with AsyncSessionLocal() as s:
            repo = UserTokenRepository(s)
            same = await repo.exists(
                USER_ID,
                ENTERPRISE_ID,
                allocation,
                identity["is_internal_user"],
                identity["internal_user_id"],
            )
            other_user = await repo.exists(
                OTHER_USER_ID,
                ENTERPRISE_ID,
                allocation,
                identity["is_internal_user"],
                identity["internal_user_id"],
            )
            return same, other_user

    same, other_user = run(go())
    assert same is True
    assert other_user is False


# --- can_start -----------------------------------------------------------------


def test_can_start_allows_under_the_limit_and_refuses_at_it(agents, identity):
    allocation, _ = agents
    run(_create(agent_slug=allocation, max_input_tokens=1_000, max_output_tokens=500, **identity))

    async def under():
        async with AsyncSessionLocal() as s:
            return await UserTokenRepository(s).can_start(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )

    assert run(under()) is True

    # Push usage to exactly the effective limit (1500), reset_at left untouched
    # (still far in the future) so this is a same-window at-limit check.
    async def exhaust():
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).get_owned(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            row.used_input_tokens = 1_000
            row.used_output_tokens = 500
            await s.commit()

    run(exhaust())
    assert run(under()) is False


def test_can_start_rolls_the_window_over_once_reset_at_has_passed(agents, identity):
    allocation, _ = agents
    past = datetime.now(timezone.utc) - timedelta(hours=48)
    row_id = run(_create(agent_slug=allocation, now=past, **identity))

    async def exhaust_and_expire():
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).get_owned(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            row.used_input_tokens = 1_000
            row.used_output_tokens = 500
            # created via now=past, so reset_at is already 24h before "now"
            await s.commit()

    run(exhaust_and_expire())

    async def start():
        async with AsyncSessionLocal() as s:
            allowed = await UserTokenRepository(s).can_start(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            # can_start only flushes the rollover, it never commits — that's
            # the caller's job (see messages.py's send_message).
            await s.commit()
            return allowed

    assert run(start()) is True

    # The old row is retired, not reused; a fresh, active, empty window exists.
    async def check():
        async with AsyncSessionLocal() as s:
            old = (
                await s.execute(select(UserToken).where(UserToken.id == row_id))
            ).scalar_one()
            new = await UserTokenRepository(s).get_owned(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            return old.is_active, new.id, new.used_input_tokens, new.reset_at

    old_active, new_id, new_used, new_reset_at = run(check())
    assert old_active is False
    assert new_id != row_id
    assert new_used == 0
    assert new_reset_at > datetime.now(timezone.utc)


# --- update_tokens: the billing rule ------------------------------------------


def test_update_tokens_only_charges_growth_over_the_largest_prompt_seen(agents, identity):
    allocation, _ = agents
    run(_create(agent_slug=allocation, **identity))

    async def update(input_tokens, output_tokens=0):
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).update_tokens(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                **identity,
            )
            await s.commit()
            return row.used_input_tokens, row.last_message_token_usage

    # First turn: the whole prompt is new.
    used, last = run(update(1_000))
    assert (used, last) == (1_000, 1_000)

    # Second turn resends the first 1,000 plus 200 new tokens of conversation.
    # Only the 200 is new spend, not the resent 1,000.
    used, last = run(update(1_200))
    assert (used, last) == (1_200, 1_200)

    # A turn that happens to bill a *smaller* prompt than the peak (e.g. context
    # was trimmed) charges nothing new, and must never lower the high-water mark.
    used, last = run(update(800))
    assert (used, last) == (1_200, 1_200)


def test_update_tokens_side_input_always_charges_in_full(agents, identity):
    allocation, _ = agents
    run(_create(agent_slug=allocation, **identity))

    async def update():
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).update_tokens(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                input_tokens=100,
                side_input_tokens=50,
                **identity,
            )
            await s.commit()
            return row.used_input_tokens

    # Unlike the deduped prompt tokens, side_input_tokens (scratchpad calls)
    # are independent calls and are billed in full every time.
    assert run(update()) == 150
    assert run(update()) == 200  # +0 prompt growth (100 again) +50 side, not deduped


def test_update_tokens_extends_reset_at_on_overage_and_never_shortens_it(agents, identity, monkeypatch):
    import app.core.billing.user_token_repo as user_token_repo

    allocation, _ = agents
    window_start = datetime.now(timezone.utc)
    run(_create(
        agent_slug=allocation,
        max_input_tokens=100,
        max_output_tokens=0,
        now=window_start,
        **identity,
    ))

    # `factor` is redrawn from random.uniform() on every over-limit call, so
    # "never shortens" has to be tested by varying the factor, not by growing
    # used_tokens — overage_ratio only ever grows as used_tokens grows, so a
    # bigger candidate on a later call proves nothing about the *shorten*
    # guard on its own.
    factors = iter([0.5, 0.9, 0.1])
    monkeypatch.setattr(user_token_repo.random, "uniform", lambda a, b: next(factors))

    async def update(input_tokens):
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).update_tokens(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                input_tokens=input_tokens,
                **identity,
            )
            await s.commit()
            return row.reset_at

    # effective_tokens=100. First call: 150 used, 50 over -> overage_ratio=0.5,
    # factor=0.5. candidate = window_start + 24h + 24h*0.5*0.5 = +30h.
    reset_1 = run(update(150))
    expected_1 = window_start + user_token_repo.WINDOW + user_token_repo.WINDOW * 0.5 * 0.5
    assert abs((reset_1 - expected_1).total_seconds()) < 2

    # Second call: same input_tokens, so no growth and the same overage_ratio
    # (0.5), but factor=0.9 draws a bigger candidate (+34.8h) -> reset_at grows.
    reset_2 = run(update(150))
    assert reset_2 > reset_1

    # Third call: same overage_ratio again, but factor=0.1 draws a *smaller*
    # candidate (+25.2h) than what's already been earned. reset_at must not
    # shrink back down to it.
    reset_3 = run(update(150))
    assert reset_3 == reset_2


# --- extra tokens --------------------------------------------------------------


def test_add_extra_tokens_requires_the_base_limit_to_be_reached(agents, identity):
    allocation, _ = agents
    run(_create(agent_slug=allocation, max_input_tokens=100, max_output_tokens=0, **identity))

    async def claim():
        async with AsyncSessionLocal() as s:
            await UserTokenRepository(s).add_extra_tokens(
                enterprise_id=ENTERPRISE_ID,
                user_id=USER_ID,
                agent_slug=allocation,
                **identity,
            )

    with pytest.raises(ValueError, match="not been reached"):
        run(claim())


def test_add_extra_tokens_raises_reset_at_leaves_effective_limit_unchanged(agents, identity):
    allocation, _ = agents
    run(_create(agent_slug=allocation, max_input_tokens=100, max_output_tokens=0, **identity))

    async def exhaust():
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).get_owned(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            row.used_input_tokens = 100
            await s.commit()
            return row.reset_at

    reset_before = run(exhaust())

    async def claim():
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).add_extra_tokens(
                enterprise_id=ENTERPRISE_ID,
                user_id=USER_ID,
                agent_slug=allocation,
                **identity,
            )
            await s.commit()
            return row.extra_token_used, row.extra_token_count_request, row.reset_at

    used, count, reset_after = run(claim())
    assert (used, count) == (True, 1)
    # Claiming raises the ceiling for this window; it must not also buy time.
    assert reset_after == reset_before

    with pytest.raises(ValueError, match="already been claimed"):
        run(claim())


def test_add_extra_tokens_across_an_expired_window_raises_instead_of_granting(agents, identity):
    allocation, _ = agents
    past = datetime.now(timezone.utc) - timedelta(hours=48)
    run(_create(
        agent_slug=allocation,
        max_input_tokens=100,
        max_output_tokens=0,
        now=past,
        **identity,
    ))

    async def exhaust():
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).get_owned(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            row.used_input_tokens = 100
            await s.commit()

    run(exhaust())

    async def claim_then_rollback():
        # app/core/routers/common.py::claim_extra_tokens is the only caller,
        # and it explicitly rolls back on this ValueError. add_extra_tokens
        # only flushes the rollover it attempts before raising, so that
        # rollback undoes it too — pin down that real end-to-end path, not a
        # hypothetical one where the caller commits anyway.
        async with AsyncSessionLocal() as s:
            try:
                await UserTokenRepository(s).add_extra_tokens(
                    enterprise_id=ENTERPRISE_ID,
                    user_id=USER_ID,
                    agent_slug=allocation,
                    **identity,
                )
            except ValueError:
                await s.rollback()
                raise

    with pytest.raises(ValueError, match="window has reset"):
        run(claim_then_rollback())

    # The attempted rollover never stuck; the caller's rollback discarded it,
    # so the same exhausted, expired row is still what's active.
    row = run(_owned(agent_slug=allocation, **identity))
    assert row.used_input_tokens == 100


# --- per-agent isolation: the reason this file exists -------------------------


def test_two_agents_get_independent_windows_for_the_same_user(agents, identity):
    allocation, other_agent = agents
    run(_create(agent_slug=allocation, max_input_tokens=100, max_output_tokens=0, **identity))
    run(_create(agent_slug=other_agent, max_input_tokens=100, max_output_tokens=0, **identity))

    async def exhaust(agent_slug):
        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).get_owned(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=agent_slug,
                **identity,
            )
            row.used_input_tokens = 100
            await s.commit()

    run(exhaust(allocation))

    async def start(agent_slug):
        async with AsyncSessionLocal() as s:
            return await UserTokenRepository(s).can_start(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=agent_slug,
                **identity,
            )

    # allocation is exhausted; other_agent, for the same user and enterprise, is not.
    assert run(start(allocation)) is False
    assert run(start(other_agent)) is True


def test_get_owned_for_one_agent_never_returns_another_agents_row(agents, identity):
    allocation, other_agent = agents
    alloc_id = run(_create(agent_slug=allocation, **identity))
    other_agent_id = run(_create(agent_slug=other_agent, **identity))
    assert alloc_id != other_agent_id

    alloc_row = run(_owned(agent_slug=allocation, **identity))
    other_agent_row = run(_owned(agent_slug=other_agent, **identity))
    assert alloc_row.id == alloc_id
    assert other_agent_row.id == other_agent_id


# --- internal vs external identity ---------------------------------------------


def test_external_and_internal_rows_for_the_same_user_id_are_independent(agents):
    allocation, _ = agents
    external_id = run(_create(agent_slug=allocation, is_internal_user=False, internal_user_id=None))
    internal_id = run(
        _create(agent_slug=allocation, is_internal_user=True, internal_user_id="employee-1")
    )
    assert external_id != internal_id

    external_row = run(_owned(agent_slug=allocation, is_internal_user=False, internal_user_id=None))
    internal_row = run(
        _owned(agent_slug=allocation, is_internal_user=True, internal_user_id="employee-1")
    )
    assert external_row.id == external_id
    assert internal_row.id == internal_id


def test_shared_internal_account_is_split_per_internal_user_id(agents):
    """ClickPost staff share one user_id; internal_user_id is the actual person."""
    allocation, _ = agents
    me_id = run(_create(agent_slug=allocation, is_internal_user=True, internal_user_id="me"))
    colleague_id = run(
        _create(agent_slug=allocation, is_internal_user=True, internal_user_id="colleague")
    )
    assert me_id != colleague_id


# --- special-case overrides: pure logic, no DB needed --------------------------


class TestExtraActive:
    """`_extra_active` / `_extra_tokens` gate a hand-picked list of enterprises
    and users to a much higher limit. Tested as pure functions, monkeypatching
    the module's own lists/dates rather than using real production ids."""

    def test_listed_enterprise_is_always_extra_active(self):
        import app.core.billing.user_token_repo as user_token_repo

        assert user_token_repo._extra_active(
            enterprise_id=user_token_repo.CUSTOM_CHECK_ENTERPRISE_IDS[0],
            user_id=0,
            is_internal_user=False,
        )

    def test_listed_external_user_is_extra_active(self):
        import app.core.billing.user_token_repo as user_token_repo

        assert user_token_repo._extra_active(
            enterprise_id=0,
            user_id=user_token_repo.TOKEN_EXTEND_AGAINST_USERID[0],
            is_internal_user=False,
        )

    def test_listed_user_id_does_not_apply_to_internal_users(self):
        """The user-id override is guarded by `not is_internal_user` — a shared
        internal account id colliding with a listed external user id must not
        silently grant the extra allowance."""
        import app.core.billing.user_token_repo as user_token_repo

        assert not user_token_repo._extra_active(
            enterprise_id=0,
            user_id=user_token_repo.TOKEN_EXTEND_AGAINST_USERID[0],
            is_internal_user=True,
        )

    def test_unlisted_enterprise_and_user_outside_the_date_window_is_not_extra_active(self):
        import app.core.billing.user_token_repo as user_token_repo

        assert not user_token_repo._extra_active(
            enterprise_id=999_999_999, user_id=999_999_999, is_internal_user=False
        )

    def test_extra_tokens_date_window_is_exclusive_of_the_end_date(self, monkeypatch):
        import app.core.billing.user_token_repo as user_token_repo

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return user_token_repo.EXTRA_TOKENS_END_DATE

        monkeypatch.setattr(user_token_repo, "datetime", _FixedDatetime)
        assert user_token_repo._extra_tokens() is False


# --- concurrency: no lost updates ----------------------------------------------


def test_concurrent_update_tokens_does_not_lose_updates(agents, identity):
    """Ten simultaneous turns, each adding 10 side-input tokens.

    `side_input_tokens` is charged in full every call, so with correct
    row-level locking the final total is exactly 10 x 10 = 100. A missing
    `for_update` lock would let concurrent sessions read-modify-write the same
    stale value and lose updates, landing under 100.
    """
    allocation, _ = agents
    run(_create(agent_slug=allocation, max_input_tokens=10_000, max_output_tokens=0, **identity))

    async def go():
        async def one():
            async with AsyncSessionLocal() as s:
                await UserTokenRepository(s).update_tokens(
                    user_id=USER_ID,
                    enterprise_id=ENTERPRISE_ID,
                    agent_slug=allocation,
                    side_input_tokens=10,
                    **identity,
                )
                await s.commit()

        await asyncio.gather(*(one() for _ in range(10)))

        async with AsyncSessionLocal() as s:
            row = await UserTokenRepository(s).get_owned(
                user_id=USER_ID,
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                **identity,
            )
            return row.used_input_tokens

    assert run(go()) == 100
