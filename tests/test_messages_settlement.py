"""The budget hold lifecycle in messages.py — where real money can leak.

`tests/test_budget_repo_integration.py` proves the repository's `reserve`,
`settle` and `release` each behave. This file proves the *caller* uses them
correctly, which is a different and largely untested question: the repo can be
flawless while the router places a hold and never drops it.

The invariant every test here defends is:

    for every hold that `reserve` actually placed, exactly one of `settle` or
    `release` runs, and the reservation ends at zero.

Break it in one direction and an abandoned hold sits on the enterprise cap
until the month rolls over, 429-ing a paying customer for capacity nobody is
using. Break it in the other — settling a hold that was never placed — and the
enterprise is credited free capacity it never paid for.

`send_message` itself is a streaming SSE endpoint wrapped around the whole
agent runtime, so driving it end-to-end would test LangChain more than it
tests settlement. The two helpers it funnels all three of its exit paths
through (`_settle_turn` and `_release_budget_hold`) are where the money
actually moves, so they are what this file exercises, against a real Postgres.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_messages_settlement.py

Rows use a synthetic enterprise id well outside the real range and are removed
in the fixture, so this is safe to run against a dev database.
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

    from app.db import AsyncSessionLocal, engine
    from app.core.billing.budget_block_event_model import BudgetBlockEvent
    from app.core.billing.budget_repo import BudgetRepository
    from app.core.billing.enterprise_budget_model import EnterpriseBudget
    from app.core.billing.usage_ledger_model import LlmUsageLedger
    from app.core.billing.user_token_model import UserToken
    from app.core.routers.messages import _release_budget_hold, _settle_turn

ENTERPRISE_ID = 999_500_001
USER_ID = 1
AGENT = "allocation"
OTHER_AGENT = "other-agent"
DOLLAR = 1_000_000

# The primary leg of the worked example used across tests/test_pricing.py and
# tests/test_budget_repo_integration.py. That example's headline 337_400 also
# carries a 35_000 scratchpad leg on Haiku; these turns make no scratchpad
# call, so the figure to expect here is the Sonnet leg alone.
MODEL = "claude-sonnet-4-6"
SECONDARY_MODEL = "claude-haiku-4-5"
WORKED_EXAMPLE_USAGE = {
    "input_tokens": 158_000,
    "output_tokens": 2_200,
    "cache_read_input_tokens": 88_000,
    "cache_creation_input_tokens": 44_000,
}
WORKED_EXAMPLE_COST = 302_400


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
        for model in (LlmUsageLedger, BudgetBlockEvent, EnterpriseBudget, UserToken):
            await s.execute(delete(model).where(model.enterprise_id == ENTERPRISE_ID))
        await s.commit()


@pytest.fixture(autouse=True)
def clean():
    run(_wipe())
    yield
    run(_wipe())


async def _reserve(*, agent_slug=AGENT, limit=25 * DOLLAR, enforcement=True):
    """Place a real hold the way send_message does, and return its BudgetState.

    Built by calling `reserve` rather than hand-constructing a BudgetState, so
    the estimate and period the helpers settle against are the ones the
    repository actually issued.
    """
    async with AsyncSessionLocal() as s:
        repo = BudgetRepository(s)
        row = await repo.get_or_create(enterprise_id=ENTERPRISE_ID, agent_slug=agent_slug)
        row.limit_micros = limit
        row.enforcement_enabled = enforcement
        await s.commit()

    async with AsyncSessionLocal() as s:
        state = await BudgetRepository(s).reserve(
            enterprise_id=ENTERPRISE_ID, agent_slug=agent_slug
        )
        await s.commit()
        return state


async def _read(*, agent_slug=AGENT):
    """(spent, reserved) for one agent's row this period."""
    async with AsyncSessionLocal() as s:
        row = (
            await s.execute(
                select(EnterpriseBudget).where(
                    EnterpriseBudget.enterprise_id == ENTERPRISE_ID,
                    EnterpriseBudget.agent_slug == agent_slug,
                )
            )
        ).scalar_one()
        return row.spent_micros, row.reserved_micros


async def _settle(
    *,
    budget_state,
    agent_slug=AGENT,
    usage=None,
    is_internal_user=False,
    internal_user_id=None,
    input_tokens=1_000,
    output_tokens=200,
):
    await _settle_turn(
        user_id=USER_ID,
        enterprise_id=ENTERPRISE_ID,
        is_internal_user=is_internal_user,
        internal_user_id=internal_user_id,
        conversation_id=uuid.uuid4(),
        human_message_id=uuid.uuid4(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage=usage,
        budget_state=budget_state,
        model=MODEL,
        secondary_model=SECONDARY_MODEL,
        agent_slug=agent_slug,
    )


# --- the happy path ----------------------------------------------------------


def test_a_settled_turn_drops_the_hold_and_charges_the_measured_cost():
    """The estimate is admission control only; it must never reach the ledger."""
    from app.config import settings

    async def go():
        state = await _reserve()
        held = (await _read())[1]
        await _settle(budget_state=state, usage=WORKED_EXAMPLE_USAGE)
        return held, await _read()

    held, (spent, reserved) = run(go())
    assert held == settings.budget_reservation_estimate_micros, "a hold was placed"
    assert spent == WORKED_EXAMPLE_COST, "charged the measured cost, not the estimate"
    assert reserved == 0, "the hold is gone"


def test_a_turn_that_produced_nothing_still_drops_its_hold():
    """An empty turn is the easiest hold to leak.

    Nothing was spent, so there is no ledger row and no cost — but a hold was
    still placed, and `_settle_turn` settles it at zero rather than walking
    away and leaving it on the cap.
    """

    async def go():
        state = await _reserve()
        await _settle(budget_state=state, usage=None, input_tokens=0, output_tokens=0)
        return await _read()

    assert run(go()) == (0, 0)


def test_a_released_hold_charges_nothing():
    async def go():
        state = await _reserve()
        await _release_budget_hold(
            enterprise_id=ENTERPRISE_ID, agent_slug=AGENT, budget_state=state
        )
        return await _read()

    assert run(go()) == (0, 0)


# --- holds that were never placed --------------------------------------------


def test_a_refused_request_is_never_settled():
    """Settling a refusal would credit the enterprise capacity it never bought.

    `reserve` returns `allowed=False` without taking anything, so both helpers
    have to treat that state as "there is nothing here to give back".
    """

    async def go():
        # A cap with no room: the hold cannot fit, so none is taken.
        state = await _reserve(limit=1)
        before = await _read()
        assert state.allowed is False
        await _settle(budget_state=state, usage=WORKED_EXAMPLE_USAGE)
        return before, await _read()

    before, after = run(go())
    assert after[1] == before[1], "a refusal holds nothing, so nothing is released"


def test_a_refused_request_is_never_released():
    async def go():
        state = await _reserve(limit=1)
        before = await _read()
        await _release_budget_hold(
            enterprise_id=ENTERPRISE_ID, agent_slug=AGENT, budget_state=state
        )
        return before, await _read()

    before, after = run(go())
    assert after == before


def test_internal_support_activity_never_touches_the_cap():
    """Staff take no hold (messages.py guards `reserve` with `not is_internal_user`),
    so settlement arrives with `budget_state=None` and must leave the cap alone
    while still writing the ledger row that attributes the work.
    """

    async def go():
        await _reserve()  # the enterprise has a row and an unrelated live hold
        before = await _read()
        await _settle(
            budget_state=None,
            usage=WORKED_EXAMPLE_USAGE,
            is_internal_user=True,
            internal_user_id="support",
        )
        async with AsyncSessionLocal() as s:
            rows = (
                await s.execute(
                    select(LlmUsageLedger).where(
                        LlmUsageLedger.enterprise_id == ENTERPRISE_ID
                    )
                )
            ).scalars().all()
        return before, await _read(), rows

    before, after, rows = run(go())
    assert after == before, "no hold was placed, so none may be settled"
    assert len(rows) == 1, "the work is still attributed to the enterprise"
    assert rows[0].counted_against_pool is False, "but never charged to their pool"


# --- per-agent scoping -------------------------------------------------------
#
# `agent_slug` only became part of these signatures in a9f097e. A mismatched
# slug matches zero rows and fails silently: the hold is never released and
# that agent's cap shrinks permanently.


def test_settling_only_moves_the_agent_that_placed_the_hold():
    async def go():
        mine = await _reserve(agent_slug=AGENT)
        await _reserve(agent_slug=OTHER_AGENT)
        await _settle(budget_state=mine, agent_slug=AGENT, usage=WORKED_EXAMPLE_USAGE)
        return await _read(agent_slug=AGENT), await _read(agent_slug=OTHER_AGENT)

    mine, other = run(go())
    assert mine == (WORKED_EXAMPLE_COST, 0), "this agent settled"
    assert other[1] > 0, "the other agent's hold is untouched, not collaterally released"
    assert other[0] == 0, "and its spend is not charged with this agent's cost"


def test_releasing_only_moves_the_agent_that_placed_the_hold():
    async def go():
        mine = await _reserve(agent_slug=AGENT)
        await _reserve(agent_slug=OTHER_AGENT)
        await _release_budget_hold(
            enterprise_id=ENTERPRISE_ID, agent_slug=AGENT, budget_state=mine
        )
        return await _read(agent_slug=AGENT), await _read(agent_slug=OTHER_AGENT)

    mine, other = run(go())
    assert mine == (0, 0)
    assert other[1] > 0, "the other agent's hold survives"


# --- the recovery path doubling up -------------------------------------------


def test_a_settle_followed_by_a_release_cannot_drive_the_hold_negative():
    """The `finally` block is belt-and-braces over the success path.

    If `usage_settled` were ever mis-set, both would run for one turn. The
    floor in `release` has to absorb that rather than handing the enterprise
    negative reserved capacity, which would read as free room on the cap.
    """

    async def go():
        state = await _reserve()
        await _settle(budget_state=state, usage=WORKED_EXAMPLE_USAGE)
        await _release_budget_hold(
            enterprise_id=ENTERPRISE_ID, agent_slug=AGENT, budget_state=state
        )
        return await _read()

    spent, reserved = run(go())
    assert reserved == 0, "greatest(0, ...) must floor the second drop"
    assert spent == WORKED_EXAMPLE_COST, "and the charge is not doubled or undone"
