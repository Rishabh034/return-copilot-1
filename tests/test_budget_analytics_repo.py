"""BudgetAnalyticsRepository — the internal budget dashboard's read queries.

Only `test_pressure_ranks_by_share_of_cap_not_absolute_spend` (in
tests/test_budget_repo_integration.py) touched this module before. This file
covers the rest, and specifically the multi-agent correctness fixes that
shipped alongside per-agent budgets (migration 3115dd2060f1):

`overview()`'s pool aggregate scans *every* enterprise's current-period rows
with no enterprise filter, and `breakdown()`/`trend()` scan *every* ledger row
in a time window with no enterprise filter either — both real, shared tables.
So instead of asserting exact totals (which real data would make flaky), this
file measures deltas (before vs. after inserting known synthetic rows) for
`overview()`, and pins ledger/block-event rows to a synthetic far-future
`created_at` window that no real row can ever land in for `breakdown()` and
`trend()`. `pressure()`, `enterprise_users()`, `top_conversations()`, `blocks()`
and `set_enterprise_config()` all take an `enterprise_id` (or the caller can
filter the returned list by one), so those use the usual synthetic-id-range
approach instead.

    docker compose up -d db
    export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db
    python -m alembic upgrade head
    RUN_DB_TESTS=1 pytest tests/test_budget_analytics_repo.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

RUN_DB_TESTS = os.environ.get("RUN_DB_TESTS") == "1"
pytestmark = pytest.mark.skipif(
    not RUN_DB_TESTS,
    reason="set RUN_DB_TESTS=1 with DATABASE_URL pointing at a migrated Postgres",
)

if RUN_DB_TESTS:
    from sqlalchemy import delete, select, update

    from app.core import registry
    from app.db import AsyncSessionLocal, engine
    from app.core.billing.budget_analytics_repo import BudgetAnalyticsRepository
    from app.core.billing.budget_block_event_model import BudgetBlockEvent
    from app.core.billing.budget_repo import BudgetRepository, period_bounds
    from app.core.billing.enterprise_budget_model import EnterpriseBudget
    from app.core.billing.usage_ledger_model import LlmUsageLedger
    from app.core.billing.usage_ledger_repo import UsageLedgerRepository

ENTERPRISE_ID = 999_500_001
OTHER_ENTERPRISE_ID = 999_500_002
DOLLAR = 1_000_000
# Far enough in the future that no real row's created_at can ever land here,
# so frm/to windows built around it are safe against real production data —
# breakdown() and trend() have no enterprise_id filter to scope by instead.
SYNTHETIC_WINDOW_START = datetime(2099, 1, 1, tzinfo=timezone.utc)
SYNTHETIC_WINDOW_END = datetime(2099, 2, 1, tzinfo=timezone.utc)


def run(coro):
    async def _scoped():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_scoped())


async def _wipe():
    async with AsyncSessionLocal() as s:
        for model in (LlmUsageLedger, BudgetBlockEvent, EnterpriseBudget):
            await s.execute(
                delete(model).where(model.enterprise_id.in_((ENTERPRISE_ID, OTHER_ENTERPRISE_ID)))
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


async def _budget_row(*, enterprise_id, agent_slug, limit, spent=0):
    """Always the current period — every test here only needs one period, and
    `get_or_create` is what keeps the (period_start, period_end) pair correct
    without duplicating `period_bounds`'s calendar-month arithmetic here."""
    async with AsyncSessionLocal() as s:
        row = await BudgetRepository(s).get_or_create(enterprise_id=enterprise_id, agent_slug=agent_slug)
        row.limit_micros = limit
        row.spent_micros = spent
        await s.commit()
        return row.period_start, row.period_end


async def _ledger_row(*, enterprise_id, agent_slug, model, input_tokens, output_tokens,
                       counted_against_pool=True, created_at):
    async with AsyncSessionLocal() as s:
        row = await UsageLedgerRepository(s).record_turn(
            enterprise_id=enterprise_id,
            user_id=1,
            is_internal_user=not counted_against_pool,
            internal_user_id="support" if not counted_against_pool else None,
            conversation_id=uuid.uuid4(),
            message_id=None,
            agent_slug=agent_slug,
            model=model,
            usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
            counted_against_pool=counted_against_pool,
        )
        await s.execute(
            update(LlmUsageLedger).where(LlmUsageLedger.id == row.id).values(created_at=created_at)
        )
        await s.commit()
        return row.id


# --- overview(): the distinct-enterprise fix -----------------------------------


def test_enterprises_with_budget_counts_distinct_enterprises_not_rows(agents):
    """An enterprise with two agents' budget rows must count once, not twice.

    Measured as a delta rather than an absolute, because the pool query in
    `overview()` scans every enterprise's current-period row, real data
    included.
    """
    allocation, other_agent = agents

    async def snapshot():
        async with AsyncSessionLocal() as s:
            frm, to = period_bounds()
            return await BudgetAnalyticsRepository(s).overview(frm=frm, to=to)

    before_count = run(snapshot())["enterprises_with_budget"]

    run(_budget_row(enterprise_id=ENTERPRISE_ID, agent_slug=allocation, limit=25 * DOLLAR))
    run(_budget_row(enterprise_id=ENTERPRISE_ID, agent_slug=other_agent, limit=25 * DOLLAR))

    after_count = run(snapshot())["enterprises_with_budget"]

    assert after_count - before_count == 1, (
        "one enterprise with two agent rows must add exactly 1, not 2, to the count"
    )


# --- pressure(): agent_slug field + filter --------------------------------------


def test_pressure_returns_one_row_per_agent_each_carrying_its_own_slug(agents):
    allocation, other_agent = agents
    period_start, _ = run(_budget_row(enterprise_id=ENTERPRISE_ID, agent_slug=allocation, limit=25 * DOLLAR))
    run(_budget_row(enterprise_id=ENTERPRISE_ID, agent_slug=other_agent, limit=10 * DOLLAR))

    async def go(agent_slug=None):
        async with AsyncSessionLocal() as s:
            rows = await BudgetAnalyticsRepository(s).pressure(
                limit=500, period_start=period_start, agent_slug=agent_slug
            )
            return [r for r in rows if r["enterprise_id"] == ENTERPRISE_ID]

    unfiltered = run(go())
    assert {r["agent_slug"] for r in unfiltered} == {allocation, other_agent}

    only_other_agent = run(go(agent_slug=other_agent))
    assert [r["agent_slug"] for r in only_other_agent] == [other_agent]
    assert only_other_agent[0]["limit_micros"] == 10 * DOLLAR


# --- set_enterprise_config(): agent-scoped write, the "matches[0]" fix ---------


def test_set_enterprise_config_only_changes_its_own_agents_row(agents):
    allocation, other_agent = agents
    period_start, _ = run(_budget_row(enterprise_id=ENTERPRISE_ID, agent_slug=allocation, limit=25 * DOLLAR))
    run(_budget_row(enterprise_id=ENTERPRISE_ID, agent_slug=other_agent, limit=25 * DOLLAR))

    async def go():
        async with AsyncSessionLocal() as s:
            row = await BudgetAnalyticsRepository(s).set_enterprise_config(
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                period_start=period_start,
                limit_micros=50 * DOLLAR,
            )
            await s.commit()
            return row.limit_micros

    assert run(go()) == 50 * DOLLAR

    async def read(agent_slug):
        async with AsyncSessionLocal() as s:
            result = await s.execute(
                select(EnterpriseBudget).where(
                    EnterpriseBudget.enterprise_id == ENTERPRISE_ID,
                    EnterpriseBudget.agent_slug == agent_slug,
                    EnterpriseBudget.period_start == period_start,
                )
            )
            return result.scalar_one().limit_micros

    assert run(read(allocation)) == 50 * DOLLAR
    assert run(read(other_agent)) == 25 * DOLLAR, "other_agent's row must be untouched by allocation's config change"


def test_set_enterprise_config_returns_none_for_an_agent_with_no_row(agents):
    allocation, other_agent = agents
    period_start, _ = run(_budget_row(enterprise_id=ENTERPRISE_ID, agent_slug=allocation, limit=25 * DOLLAR))

    async def go():
        async with AsyncSessionLocal() as s:
            row = await BudgetAnalyticsRepository(s).set_enterprise_config(
                enterprise_id=ENTERPRISE_ID,
                agent_slug=other_agent,  # never created for this enterprise/period
                period_start=period_start,
                limit_micros=50 * DOLLAR,
            )
            await s.commit()
            return row

    assert run(go()) is None


# --- breakdown() / trend(): synthetic time window, no enterprise filter -------


def test_breakdown_splits_cost_by_class_and_flags_an_unpriced_model(agents):
    allocation, _ = agents
    mid = SYNTHETIC_WINDOW_START + timedelta(days=1)
    run(
        _ledger_row(
            enterprise_id=ENTERPRISE_ID,
            agent_slug=allocation,
            model="claude-sonnet-4-6",
            input_tokens=1_000,
            output_tokens=100,
            created_at=mid,
        )
    )
    run(
        _ledger_row(
            enterprise_id=ENTERPRISE_ID,
            agent_slug=allocation,
            model="claude-does-not-exist",
            input_tokens=50,
            output_tokens=5,
            created_at=mid,
        )
    )

    async def go():
        async with AsyncSessionLocal() as s:
            return await BudgetAnalyticsRepository(s).breakdown(
                frm=SYNTHETIC_WINDOW_START, to=SYNTHETIC_WINDOW_END
            )

    result = run(go())
    assert result["total_micros"] > 0
    by_model = {m["model"]: m for m in result["by_model"]}
    assert by_model["claude-sonnet-4-6"]["unpriced"] is False
    assert by_model["claude-does-not-exist"]["unpriced"] is True
    assert by_model["claude-does-not-exist"]["cost_micros"] == 0
    classes = {c["cls"]: c["cost_micros"] for c in result["by_class"]}
    assert classes["uncached_input"] > 0 and classes["output"] > 0


def test_trend_reports_cost_per_turn_per_day(agents):
    allocation, _ = agents
    day = SYNTHETIC_WINDOW_START + timedelta(days=2)
    for _ in range(2):
        run(
            _ledger_row(
                enterprise_id=ENTERPRISE_ID,
                agent_slug=allocation,
                model="claude-sonnet-4-6",
                input_tokens=1_000,
                output_tokens=100,
                created_at=day,
            )
        )

    async def go():
        async with AsyncSessionLocal() as s:
            return await BudgetAnalyticsRepository(s).trend(
                frm=SYNTHETIC_WINDOW_START,
                to=SYNTHETIC_WINDOW_END,
                enterprise_id=ENTERPRISE_ID,
            )

    days = run(go())
    assert len(days) == 1
    assert days[0]["turns"] == 2
    assert days[0]["cost_per_turn_micros"] == days[0]["cost_micros"] // 2


def test_breakdown_and_trend_split_support_from_customer_spend(agents):
    """`counted_against_pool=False` marks ClickPost's own support activity."""
    allocation, _ = agents
    day = SYNTHETIC_WINDOW_START + timedelta(days=3)
    run(
        _ledger_row(
            enterprise_id=ENTERPRISE_ID,
            agent_slug=allocation,
            model="claude-sonnet-4-6",
            input_tokens=1_000,
            output_tokens=100,
            counted_against_pool=False,
            created_at=day,
        )
    )

    async def go(include_internal):
        async with AsyncSessionLocal() as s:
            return await BudgetAnalyticsRepository(s).trend(
                frm=SYNTHETIC_WINDOW_START,
                to=SYNTHETIC_WINDOW_END,
                enterprise_id=ENTERPRISE_ID,
                include_internal=include_internal,
            )

    assert run(go(include_internal=False)) == []
    with_support = run(go(include_internal=True))
    assert len(with_support) == 1
    assert with_support[0]["turns"] == 1


# --- blocks() / enterprise_users() / top_conversations() ----------------------


def test_blocks_filters_by_enterprise_and_orders_newest_first(agents):
    allocation, _ = agents

    async def record(enterprise_id, limit_kind):
        async with AsyncSessionLocal() as s:
            await BudgetRepository(s).record_block(
                enterprise_id=enterprise_id,
                user_id=1,
                is_internal_user=False,
                limit_kind=limit_kind,
                agent_slug=allocation,
            )
            await s.commit()

    run(record(ENTERPRISE_ID, "quota_user_window"))
    run(record(OTHER_ENTERPRISE_ID, "budget_enterprise"))

    async def go():
        async with AsyncSessionLocal() as s:
            now = datetime.now(timezone.utc)
            return await BudgetAnalyticsRepository(s).blocks(
                frm=now - timedelta(minutes=5), to=now + timedelta(minutes=5),
                enterprise_id=ENTERPRISE_ID,
            )

    events = run(go())
    assert len(events) == 1
    assert events[0]["enterprise_id"] == ENTERPRISE_ID
    assert events[0]["limit_kind"] == "quota_user_window"


def test_enterprise_users_splits_shared_internal_accounts_by_internal_user_id(agents):
    allocation, _ = agents
    now = datetime.now(timezone.utc)

    async def turn(user_id, is_internal_user, internal_user_id):
        async with AsyncSessionLocal() as s:
            await UsageLedgerRepository(s).record_turn(
                enterprise_id=ENTERPRISE_ID,
                user_id=user_id,
                is_internal_user=is_internal_user,
                internal_user_id=internal_user_id,
                conversation_id=uuid.uuid4(),
                message_id=None,
                agent_slug=allocation,
                model="claude-sonnet-4-6",
                usage={"input_tokens": 100, "output_tokens": 10},
                counted_against_pool=not is_internal_user,
            )
            await s.commit()

    # One shared internal account id, two different people.
    run(turn(7, True, "me"))
    run(turn(7, True, "colleague"))
    run(turn(9, False, None))

    async def go():
        async with AsyncSessionLocal() as s:
            return await BudgetAnalyticsRepository(s).enterprise_users(
                enterprise_id=ENTERPRISE_ID,
                frm=now - timedelta(minutes=5),
                to=now + timedelta(minutes=5),
            )

    users = run(go())
    keys = {(u["user_id"], u["internal_user_id"]) for u in users}
    assert keys == {(7, "me"), (7, "colleague"), (9, None)}, (
        "a shared account (same user_id) must not merge distinct people into one row"
    )


def test_top_conversations_ranks_by_cost_descending(agents):
    allocation, _ = agents
    now = datetime.now(timezone.utc)
    cheap, expensive = uuid.uuid4(), uuid.uuid4()

    async def turn(conversation_id, output_tokens):
        async with AsyncSessionLocal() as s:
            await UsageLedgerRepository(s).record_turn(
                enterprise_id=ENTERPRISE_ID,
                user_id=1,
                is_internal_user=False,
                internal_user_id=None,
                conversation_id=conversation_id,
                message_id=None,
                agent_slug=allocation,
                model="claude-sonnet-4-6",
                usage={"input_tokens": 100, "output_tokens": output_tokens},
            )
            await s.commit()

    run(turn(cheap, 10))
    run(turn(expensive, 100_000))

    async def go():
        async with AsyncSessionLocal() as s:
            return await BudgetAnalyticsRepository(s).top_conversations(
                frm=now - timedelta(minutes=5),
                to=now + timedelta(minutes=5),
                enterprise_id=ENTERPRISE_ID,
            )

    rows = run(go())
    assert [r["conversation_id"] for r in rows] == [expensive, cheap]
