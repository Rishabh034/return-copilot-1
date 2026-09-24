"""IntegrationKeyRepository against a real Postgres.

Same card-authorization shape as BudgetRepository (hold, then settle or
release) and, until now, zero test coverage — this mirrors the scenarios
already proven for BudgetRepository in test_budget_repo_integration.py,
against this repo's own table instead.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_integration_key_repo.py

Rows use a name prefix reserved for this file and are removed in the fixture,
so this is safe to run against a dev database.
"""

from __future__ import annotations

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
    from app.core.billing.budget_repo import period_bounds
    from app.core.integrations.integration_api_key_model import IntegrationApiKey
    from app.core.integrations.integration_key_repo import IntegrationKeyRepository

NAME_PREFIX = "zz-test-integration-key-repo-"
DOLLAR = 1_000_000


def run(coro):
    async def _scoped():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_scoped())


async def _wipe():
    async with AsyncSessionLocal() as s:
        await s.execute(delete(IntegrationApiKey).where(IntegrationApiKey.name.like(f"{NAME_PREFIX}%")))
        await s.commit()


@pytest.fixture(autouse=True)
def clean():
    run(_wipe())
    yield
    run(_wipe())


async def _create_key(*, name, monthly_limit_micros=None, spent=0, reserved=0, seed_current_period=False):
    """A fresh key row. `seed_current_period` pre-sets period_start/end to the
    current period (with the given spent/reserved) so `reserve`'s CASE
    expressions carry those numbers over instead of treating them as stale."""
    async with AsyncSessionLocal() as s:
        row = IntegrationApiKey(
            id=uuid.uuid4(),
            name=name,
            key_hash=uuid.uuid4().hex,
            key_prefix="cpai_test",
            monthly_limit_micros=monthly_limit_micros,
        )
        if seed_current_period:
            period_start, period_end = period_bounds()
            row.period_start = period_start
            row.period_end = period_end
            row.spent_micros = spent
            row.reserved_micros = reserved
        s.add(row)
        await s.commit()
        return row.id


async def _read(api_key_id):
    async with AsyncSessionLocal() as s:
        row = await s.get(IntegrationApiKey, api_key_id)
        return row.spent_micros, row.reserved_micros, row.period_start


# --- reserve: period rollover and the unlimited default ------------------------


def test_reserve_rolls_a_never_used_key_into_the_current_period():
    key_id = run(_create_key(name=f"{NAME_PREFIX}fresh"))

    async def go():
        async with AsyncSessionLocal() as s:
            state = await IntegrationKeyRepository(s).reserve(
                api_key_id=key_id, estimate_micros=500_000
            )
            await s.commit()
            return state

    state = run(go())
    assert state.allowed is True
    assert state.unlimited is True
    assert state.period_start == period_bounds()[0]

    spent, reserved, period_start = run(_read(key_id))
    assert (spent, reserved) == (0, 500_000)
    assert period_start == period_bounds()[0]


def test_reserve_refuses_when_the_hold_will_not_fit():
    # 20c left of a $1 cap, the hold wants 50c.
    key_id = run(
        _create_key(
            name=f"{NAME_PREFIX}tight",
            monthly_limit_micros=1 * DOLLAR,
            spent=1 * DOLLAR - 200_000,
            seed_current_period=True,
        )
    )

    async def go():
        async with AsyncSessionLocal() as s:
            state = await IntegrationKeyRepository(s).reserve(
                api_key_id=key_id, estimate_micros=500_000
            )
            await s.commit()
            return state

    state = run(go())
    assert state.allowed is False
    assert state.remaining_micros == 200_000


def test_monthly_limit_none_never_refuses_regardless_of_spend():
    key_id = run(
        _create_key(
            name=f"{NAME_PREFIX}unlimited",
            monthly_limit_micros=None,
            spent=999 * DOLLAR,
            reserved=999 * DOLLAR,
            seed_current_period=True,
        )
    )

    async def go():
        async with AsyncSessionLocal() as s:
            state = await IntegrationKeyRepository(s).reserve(
                api_key_id=key_id, estimate_micros=500_000
            )
            await s.commit()
            return state

    state = run(go())
    assert state.allowed is True
    assert state.unlimited is True
    assert state.remaining_micros is None


def test_concurrent_reserves_cannot_oversubscribe_the_monthly_cap():
    """Ten simultaneous requests, room for six — same guarantee as
    BudgetRepository.reserve, exercised against this repo's own conditional
    UPDATE instead."""
    key_id = run(
        _create_key(
            name=f"{NAME_PREFIX}concurrent",
            monthly_limit_micros=3 * DOLLAR,  # $3.00 / $0.50 = 6 holds
            seed_current_period=True,
        )
    )

    async def go():
        async def one():
            async with AsyncSessionLocal() as s:
                state = await IntegrationKeyRepository(s).reserve(
                    api_key_id=key_id, estimate_micros=500_000
                )
                await s.commit()
                return state.allowed

        outcomes = await asyncio.gather(*(one() for _ in range(10)))
        return outcomes, await _read(key_id)

    results, (spent, reserved, _) = run(go())
    assert sum(results) == 6, f"expected 6 admitted, got {sum(results)}"
    assert reserved == 6 * 500_000
    assert spent + reserved <= 3 * DOLLAR


# --- settle and release ---------------------------------------------------------


def test_settle_replaces_the_hold_with_the_measured_cost():
    key_id = run(_create_key(name=f"{NAME_PREFIX}settle", seed_current_period=True))

    async def go():
        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).reserve(api_key_id=key_id, estimate_micros=500_000)
            await s.commit()
        held_after_reserve = (await _read(key_id))[1]

        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).settle(
                api_key_id=key_id, estimate_micros=500_000, actual_micros=337_400
            )
            await s.commit()
        return held_after_reserve, await _read(key_id)

    held, (spent, reserved, _) = run(go())
    assert held == 500_000
    assert spent == 337_400, "charged the real cost, not the estimate"
    assert reserved == 0, "hold released"


def test_settling_twice_does_not_drive_the_hold_negative():
    key_id = run(_create_key(name=f"{NAME_PREFIX}double-settle", seed_current_period=True))

    async def go():
        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).reserve(api_key_id=key_id, estimate_micros=500_000)
            await s.commit()
        for _ in range(2):
            async with AsyncSessionLocal() as s:
                await IntegrationKeyRepository(s).settle(
                    api_key_id=key_id, estimate_micros=500_000, actual_micros=100_000
                )
                await s.commit()
        return await _read(key_id)

    _, reserved, _ = run(go())
    assert reserved == 0, "greatest(0, ...) must floor the release"


def test_release_after_a_settle_does_not_drive_the_hold_negative():
    """A caller that releases a hold it already settled (e.g. a retried
    best-effort cleanup) must not double-subtract."""
    key_id = run(_create_key(name=f"{NAME_PREFIX}release-after-settle", seed_current_period=True))

    async def go():
        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).reserve(api_key_id=key_id, estimate_micros=500_000)
            await s.commit()
        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).settle(
                api_key_id=key_id, estimate_micros=500_000, actual_micros=100_000
            )
            await s.commit()
        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).release(api_key_id=key_id, estimate_micros=500_000)
            await s.commit()
        return await _read(key_id)

    spent, reserved, _ = run(go())
    assert spent == 100_000, "the settle already charged; release must not touch spend"
    assert reserved == 0


def test_release_drops_the_hold_without_charging():
    key_id = run(_create_key(name=f"{NAME_PREFIX}release", seed_current_period=True))

    async def go():
        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).reserve(api_key_id=key_id, estimate_micros=500_000)
            await s.commit()
        async with AsyncSessionLocal() as s:
            await IntegrationKeyRepository(s).release(api_key_id=key_id, estimate_micros=500_000)
            await s.commit()
        return await _read(key_id)

    spent, reserved, _ = run(go())
    assert (spent, reserved) == (0, 0)


# --- authentication ---------------------------------------------------------


def test_get_active_by_hash_ignores_revoked_keys():
    async def go():
        async with AsyncSessionLocal() as s:
            row = IntegrationApiKey(
                id=uuid.uuid4(),
                name=f"{NAME_PREFIX}revoked",
                key_hash="zz-test-revoked-hash",
                key_prefix="cpai_test",
                is_active=False,
                revoked_at=datetime.now(timezone.utc),
            )
            s.add(row)
            await s.commit()

        async with AsyncSessionLocal() as s:
            return await IntegrationKeyRepository(s).get_active_by_hash("zz-test-revoked-hash")

    assert run(go()) is None
