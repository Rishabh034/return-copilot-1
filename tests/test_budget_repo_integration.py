"""BudgetRepository against a real Postgres.

The concurrency guarantee is the whole reason `reserve` is written as one
conditional UPDATE rather than a read followed by a write

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_budget_repo_integration.py

`test_concurrent_reserves_cannot_oversubscribe_the_cap` is the one to keep. Ten
requests race for room that fits six; if the conditional UPDATE were ever
refactored into SELECT-then-UPDATE, all ten would pass and the cap would be
silently meaningless.

Rows use a synthetic enterprise id well outside the real range and are removed
in the fixture, so this is safe to run against a dev database.
"""

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set RUN_DB_TESTS=1 with DATABASE_URL pointing at a migrated Postgres",
)

if RUN_DB_TESTS:
    from sqlalchemy import delete, select

    from app.db import AsyncSessionLocal, engine
    from app.rr_log_analyst.models.budget_block_event import BudgetBlockEvent
    from app.rr_log_analyst.models.enterprise_budget import EnterpriseBudget
    from app.rr_log_analyst.models.usage_ledger import LlmUsageLedger
    from app.rr_log_analyst.repositories.budget_repo import BudgetRepository
    from app.rr_log_analyst.repositories.usage_ledger_repo import UsageLedgerRepository

ENTERPRISE_ID = 999_000_042
DOLLAR = 1_000_000


def run(coro):
    """Run one coroutine in a fresh loop, then drop the connection pool.

    app/db.py builds a module-level engine at import, and asyncpg binds pooled
    connections to the loop that opened them. Each asyncio.run() creates a new
    loop, so without disposing here the second test in a session inherits
    connections belonging to a closed loop and fails with "Event loop is
    closed". Disposing costs a reconnect per test and buys isolation.
    """

    async def _scoped():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_scoped())


async def _wipe():
    async with AsyncSessionLocal() as s:
        for model in (LlmUsageLedger, BudgetBlockEvent, EnterpriseBudget):
            await s.execute(delete(model).where(model.enterprise_id == ENTERPRISE_ID))
        await s.commit()


async def _configure(*, limit, spent=0, reserved=0, enforcement=True):
    """Create this period's row and force it into a known state."""
    async with AsyncSessionLocal() as s:
        row = await BudgetRepository(s).get_or_create(enterprise_id=ENTERPRISE_ID)
        row.limit_micros = limit
        row.spent_micros = spent
        row.reserved_micros = reserved
        row.enforcement_enabled = enforcement
        await s.commit()
        return row.period_start


async def _read():
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(EnterpriseBudget).where(
                EnterpriseBudget.enterprise_id == ENTERPRISE_ID
            )
        )
        row = result.scalar_one()
        return row.spent_micros, row.reserved_micros


@pytest.fixture(autouse=True)
def clean():
    """Synthetic enterprise id, removed either side, so this is safe on a dev DB."""
    run(_wipe())
    yield
    run(_wipe())


# --- lifecycle --------------------------------------------------------------

def test_get_or_create_is_idempotent_within_a_period():
    async def go():
        async with AsyncSessionLocal() as s:
            repo = BudgetRepository(s)
            first = await repo.get_or_create(enterprise_id=ENTERPRISE_ID)
            second = await repo.get_or_create(enterprise_id=ENTERPRISE_ID)
            await s.commit()
            return first.id, second.id

    first, second = run(go())
    assert first == second


def test_first_turn_of_a_period_seeds_the_configured_cap():
    async def go():
        async with AsyncSessionLocal() as s:
            row = await BudgetRepository(s).get_or_create(enterprise_id=ENTERPRISE_ID)
            await s.commit()
            return row.limit_micros, row.enforcement_enabled, row.spent_micros

    limit, enforcement, spent = run(go())
    assert limit == 25 * DOLLAR
    # Deploying must not start blocking anyone.
    assert enforcement is False
    assert spent == 0


# --- the hold ---------------------------------------------------------------

def test_enforcement_off_always_allows_even_when_over_the_cap():
    async def go():
        await _configure(limit=1 * DOLLAR, spent=999 * DOLLAR, enforcement=False)
        async with AsyncSessionLocal() as s:
            state = await BudgetRepository(s).reserve(enterprise_id=ENTERPRISE_ID)
            await s.commit()
            return state.allowed

    assert run(go()) is True


def test_reserve_refuses_when_the_hold_will_not_fit():
    async def go():
        # 20c left, the hold wants 50c
        await _configure(limit=25 * DOLLAR, spent=25 * DOLLAR - 200_000)
        async with AsyncSessionLocal() as s:
            state = await BudgetRepository(s).reserve(enterprise_id=ENTERPRISE_ID)
            await s.commit()
            return state

    state = run(go())
    assert state.allowed is False
    assert state.remaining_micros == 200_000


def test_concurrent_reserves_cannot_oversubscribe_the_cap():
    """Ten simultaneous requests, room for six. Exactly six may pass.

    Each task uses its own session, so this exercises real row-level
    serialization rather than one session's identity map.
    """
    async def go():
        await _configure(limit=3 * DOLLAR, spent=0)  # $3.00 / $0.50 = 6 holds

        async def one():
            async with AsyncSessionLocal() as s:
                state = await BudgetRepository(s).reserve(enterprise_id=ENTERPRISE_ID)
                await s.commit()
                return state.allowed

        outcomes = await asyncio.gather(*(one() for _ in range(10)))
        return outcomes, await _read()

    results, (spent, reserved) = run(go())
    assert sum(results) == 6, f"expected 6 admitted, got {sum(results)}"
    assert reserved == 6 * 500_000
    assert spent + reserved <= 3 * DOLLAR


# --- settle and release -----------------------------------------------------

def test_settle_replaces_the_hold_with_the_measured_cost():
    async def go():
        period = await _configure(limit=25 * DOLLAR)
        async with AsyncSessionLocal() as s:
            repo = BudgetRepository(s)
            await repo.reserve(enterprise_id=ENTERPRISE_ID)
            await s.commit()
        held_after_reserve = (await _read())[1]

        async with AsyncSessionLocal() as s:
            await BudgetRepository(s).settle(
                enterprise_id=ENTERPRISE_ID,
                period_start=period,
                estimate_micros=500_000,
                actual_micros=337_400,  # the worked example
            )
            await s.commit()
        return held_after_reserve, await _read()

    held, (spent, reserved) = run(go())
    assert held == 500_000
    assert spent == 337_400, "charged the real cost, not the estimate"
    assert reserved == 0, "hold released"


def test_release_drops_the_hold_without_charging():
    async def go():
        period = await _configure(limit=25 * DOLLAR)
        async with AsyncSessionLocal() as s:
            await BudgetRepository(s).reserve(enterprise_id=ENTERPRISE_ID)
            await s.commit()
        async with AsyncSessionLocal() as s:
            await BudgetRepository(s).release(
                enterprise_id=ENTERPRISE_ID,
                period_start=period,
                estimate_micros=500_000,
            )
            await s.commit()
        return await _read()

    assert run(go()) == (0, 0)


def test_settling_twice_does_not_drive_the_hold_negative():
    """A leaked-then-recovered turn must not credit the enterprise free capacity."""
    async def go():
        period = await _configure(limit=25 * DOLLAR)
        async with AsyncSessionLocal() as s:
            await BudgetRepository(s).reserve(enterprise_id=ENTERPRISE_ID)
            await s.commit()
        for _ in range(2):
            async with AsyncSessionLocal() as s:
                await BudgetRepository(s).settle(
                    enterprise_id=ENTERPRISE_ID,
                    period_start=period,
                    estimate_micros=500_000,
                    actual_micros=100_000,
                )
                await s.commit()
        return await _read()

    _, reserved = run(go())
    assert reserved == 0, "greatest(0, ...) must floor the release"


# --- ledger -----------------------------------------------------------------

def test_ledger_prices_a_turn_and_flags_an_unknown_model():
    async def go():
        async with AsyncSessionLocal() as s:
            repo = UsageLedgerRepository(s)
            priced = await repo.record_turn(
                enterprise_id=ENTERPRISE_ID,
                user_id=1,
                is_internal_user=False,
                internal_user_id=None,
                conversation_id=uuid.uuid4(),
                message_id=uuid.uuid4(),
                model="claude-sonnet-4-6",
                usage={
                    "input_tokens": 158_000,
                    "output_tokens": 2_200,
                    "cache_read_input_tokens": 88_000,
                    "cache_creation_input_tokens": 44_000,
                },
                secondary_model="claude-haiku-4-5",
                secondary_usage={"input_tokens": 20_000, "output_tokens": 3_000},
            )
            unpriced = await repo.record_turn(
                enterprise_id=ENTERPRISE_ID,
                user_id=1,
                is_internal_user=True,
                internal_user_id="support",
                conversation_id=uuid.uuid4(),
                message_id=None,
                model="claude-does-not-exist",
                usage={"input_tokens": 500, "output_tokens": 10},
                counted_against_pool=False,
            )
            await s.commit()
            return priced, unpriced

    priced, unpriced = run(go())
    assert priced.cost_micros == 337_400
    assert priced.pricing_failed is False
    assert priced.counted_against_pool is True

    # An unpriceable turn is still recorded, with its tokens, so it can be
    # re-priced once a rate is added.
    assert unpriced.cost_micros == 0
    assert unpriced.pricing_failed is True
    assert unpriced.input_tokens == 500
    assert unpriced.counted_against_pool is False
