"""UsageRepository — the customer-facing spend queries.

Integration tests: these are aggregation queries, and the bugs they can carry
(a missing scope clause, a double-counted token class, a shared account
collapsing several people into one) are all invisible against a mock and
obvious against real rows.

Opt in with RUN_DB_TESTS=1 — see tests/test_budget_repo_integration.py for why
this is explicit rather than derived from DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db import AsyncSessionLocal, engine
from app.core.billing.usage_ledger_model import LlmUsageLedger
from app.core.billing.usage_repo import UsageRepository, period_window

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="needs a reachable database; set RUN_DB_TESTS=1",
)

# Reserved range, same convention as scripts/seed_budget_demo.py: nothing here
# can collide with, or delete, real data.
ENT = 999_800_001
OTHER_ENT = 999_800_002
PERIOD_START = date(2026, 3, 1)
# Exclusive: period_bounds() returns the first day of the NEXT month.
PERIOD_END = date(2026, 4, 1)


def run(coro):
    """Each test gets its own loop and disposes the engine.

    asyncpg binds connections to the loop that opened them, and the module-level
    engine would otherwise hand a second test connections from a closed loop.
    """

    async def _scoped():
        try:
            return await coro()
        finally:
            await engine.dispose()

    return asyncio.run(_scoped())


async def _wipe():
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(LlmUsageLedger).where(
                LlmUsageLedger.enterprise_id.in_([ENT, OTHER_ENT])
            )
        )
        await session.commit()


async def _add(
    *,
    enterprise_id=ENT,
    user_id=500,
    internal_user_id=None,
    is_internal_user=False,
    counted=True,
    cost=1_000,
    tok_in=1_000,
    tok_out=100,
    cache_read=0,
    cache_creation=0,
    when=None,
):
    async with AsyncSessionLocal() as session:
        session.add(
            LlmUsageLedger(
                id=uuid.uuid4(),
                enterprise_id=enterprise_id,
                user_id=user_id,
                is_internal_user=is_internal_user,
                internal_user_id=internal_user_id,
                conversation_id=uuid.uuid4(),
                message_id=uuid.uuid4(),
                model="claude-sonnet-4-6",
                input_tokens=tok_in,
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
                output_tokens=tok_out,
                secondary_input_tokens=0,
                secondary_output_tokens=0,
                cost_micros=cost,
                counted_against_pool=counted,
                created_at=when
                or datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
            )
        )
        await session.commit()


class TestPeriodWindow:
    def test_window_is_half_open(self):
        """period_bounds() gives the 1st of the next month as its end. Treating
        that as inclusive would stretch March across the whole of 1 April and
        count those turns in both months."""
        start, end = period_window(PERIOD_START, PERIOD_END)
        assert start == datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)

    def test_bounds_are_utc(self):
        start, end = period_window(PERIOD_START, PERIOD_END)
        assert start.tzinfo == timezone.utc and end.tzinfo == timezone.utc


class TestForUser:
    def test_sums_only_this_users_rows(self):
        async def go():
            await _wipe()
            await _add(user_id=500, cost=1_000)
            await _add(user_id=500, cost=2_000)
            await _add(user_id=501, cost=9_000)  # someone else
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_user(
                    enterprise_id=ENT,
                    user_id=500,
                    is_internal_user=False,
                    internal_user_id=None,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        totals = run(go)
        assert totals.turns == 2
        assert totals.cost_micros == 3_000

    def test_shared_account_is_split_by_internal_user_id(self):
        """The bug this guards: ClickPost staff share one user_id, so matching
        on user_id alone hands each of them the sum of all their colleagues."""

        async def go():
            await _wipe()
            await _add(user_id=14, internal_user_id="304875", is_internal_user=True, cost=5_000)
            await _add(user_id=14, internal_user_id="301602", is_internal_user=True, cost=7_000)
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_user(
                    enterprise_id=ENT,
                    user_id=14,
                    is_internal_user=True,
                    internal_user_id="304875",
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        totals = run(go)
        assert totals.turns == 1
        assert totals.cost_micros == 5_000

    def test_does_not_cross_enterprises(self):
        async def go():
            await _wipe()
            await _add(enterprise_id=ENT, user_id=500, cost=1_000)
            await _add(enterprise_id=OTHER_ENT, user_id=500, cost=8_000)
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_user(
                    enterprise_id=ENT,
                    user_id=500,
                    is_internal_user=False,
                    internal_user_id=None,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        assert run(go).cost_micros == 1_000

    def test_excludes_rows_outside_the_period(self):
        """The 1 April row is the regression guard: an inclusive upper bound
        would count it in March as well as in April."""

        async def go():
            await _wipe()
            await _add(cost=1_000)
            await _add(cost=4_000, when=datetime(2026, 2, 28, 23, 0, tzinfo=timezone.utc))
            await _add(cost=4_000, when=datetime(2026, 4, 1, 0, 30, tzinfo=timezone.utc))
            # Last instant that still belongs to March.
            await _add(cost=500, when=datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc))
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_user(
                    enterprise_id=ENT,
                    user_id=500,
                    is_internal_user=False,
                    internal_user_id=None,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        assert run(go).cost_micros == 1_500

    def test_no_rows_reports_zero_not_none(self):
        async def go():
            await _wipe()
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_user(
                    enterprise_id=ENT,
                    user_id=500,
                    is_internal_user=False,
                    internal_user_id=None,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        totals = run(go)
        assert (totals.turns, totals.cost_micros, totals.last_active) == (0, 0, None)

    def test_total_tokens_does_not_double_count_the_cached_prompt(self):
        """input_tokens already contains the cache figures — see the ledger model."""

        async def go():
            await _wipe()
            await _add(tok_in=1_000, tok_out=100, cache_read=800, cache_creation=150)
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_user(
                    enterprise_id=ENT,
                    user_id=500,
                    is_internal_user=False,
                    internal_user_id=None,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        totals = run(go)
        assert totals.total_tokens == 1_100
        assert (totals.cache_read_tokens, totals.cache_creation_tokens) == (800, 150)


class TestForEnterprise:
    def test_excludes_support_activity_from_the_pool_figure(self):
        """Support turns are recorded against the customer but never charged to
        them; including them shows spend they cannot explain and did not incur."""

        async def go():
            await _wipe()
            await _add(user_id=500, cost=3_000, counted=True)
            await _add(
                user_id=14,
                internal_user_id="304875",
                is_internal_user=True,
                counted=False,
                cost=9_000,
            )
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_enterprise(
                    enterprise_id=ENT,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        assert run(go).cost_micros == 3_000

    def test_counted_only_false_includes_everything(self):
        async def go():
            await _wipe()
            await _add(cost=3_000, counted=True)
            await _add(cost=9_000, counted=False, is_internal_user=True,
                       user_id=14, internal_user_id="304875")
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).for_enterprise(
                    enterprise_id=ENT,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                    counted_only=False,
                )

        assert run(go).cost_micros == 12_000


class TestActiveUsers:
    def test_counts_distinct_people_not_rows(self):
        async def go():
            await _wipe()
            await _add(user_id=500)
            await _add(user_id=500)
            await _add(user_id=501)
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).active_users(
                    enterprise_id=ENT,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        assert run(go) == 2

    def test_shared_account_counts_each_person_separately(self):
        """Two employees on user_id 14 are two active users, not one."""

        async def go():
            await _wipe()
            await _add(user_id=14, internal_user_id="304875", is_internal_user=True)
            await _add(user_id=14, internal_user_id="301602", is_internal_user=True)
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).active_users(
                    enterprise_id=ENT,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

        assert run(go) == 2

    def test_support_is_counted_here_but_excluded_from_the_spend_beside_it(self):
        """PINNED AS OBSERVED, not asserted as correct.

        `GET /enterprise` returns both of these numbers in one payload, and
        they disagree about ClickPost support. `for_enterprise` defaults to
        `counted_only=True` and leaves support out, because — in its own words
        — including it "would show a customer spend they are not paying for
        and cannot explain". `active_users` has no such filter, so the same
        response can tell a four-seat customer that five people were active
        while showing spend for four.

        Whether that is a bug is a product question nobody has answered yet,
        so this records today's behaviour rather than blessing it. If you are
        reading this because you just made it fail, you have probably fixed
        the inconsistency on purpose: update this test, don't work around it.
        """

        async def go():
            await _wipe()
            await _add(user_id=500, cost=1_000)
            await _add(user_id=501, cost=1_000)
            await _add(
                user_id=14,
                internal_user_id="support",
                is_internal_user=True,
                counted=False,
                cost=9_000,
            )
            async with AsyncSessionLocal() as s:
                repo = UsageRepository(s)
                active = await repo.active_users(
                    enterprise_id=ENT,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )
                totals = await repo.for_enterprise(
                    enterprise_id=ENT,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )
                return active, totals.cost_micros

        active, cost = run(go)
        assert active == 3, "support counts as an active user"
        assert cost == 2_000, "but its spend is excluded from the figure beside it"


class TestMembers:
    def test_excludes_clickpost_support_from_the_team_list(self):
        """A support engineer is not a member of the customer's team, and their
        spend never touched the customer's pool."""

        async def go():
            await _wipe()
            await _add(user_id=8801, cost=4_000)
            await _add(user_id=14, internal_user_id="304875", is_internal_user=True,
                       counted=False, cost=9_000)
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).users(
                    enterprise_id=ENT, period_start=PERIOD_START, period_end=PERIOD_END
                )

        members = run(go)
        assert [m["user_id"] for m in members] == [8801]

    def test_ranked_by_spend_descending(self):
        async def go():
            await _wipe()
            await _add(user_id=1, cost=1_000)
            await _add(user_id=2, cost=9_000)
            await _add(user_id=3, cost=5_000)
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).users(
                    enterprise_id=ENT, period_start=PERIOD_START, period_end=PERIOD_END
                )

        assert [m["user_id"] for m in run(go)] == [2, 3, 1]


class TestSeries:
    def test_rejects_an_unknown_granularity(self):
        """Guarded in the repo as well as the router: date_trunc would otherwise
        take an arbitrary caller string straight into SQL."""

        async def go():
            async with AsyncSessionLocal() as s:
                with pytest.raises(ValueError):
                    await UsageRepository(s).series(
                        enterprise_id=ENT,
                        frm=datetime(2026, 3, 1, tzinfo=timezone.utc),
                        to=datetime(2026, 4, 1, tzinfo=timezone.utc),
                        granularity="century",
                    )

        run(go)

    def test_buckets_by_day_and_excludes_support(self):
        async def go():
            await _wipe()
            await _add(cost=1_000, when=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc))
            await _add(cost=3_000, when=datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc))
            await _add(cost=2_000, when=datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc))
            await _add(cost=9_000, counted=False, is_internal_user=True, user_id=14,
                       internal_user_id="304875",
                       when=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc))
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).series(
                    enterprise_id=ENT,
                    frm=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    to=datetime(2026, 4, 1, tzinfo=timezone.utc),
                    granularity="day",
                )

        points = run(go)
        assert len(points) == 2
        assert points[0]["cost_micros"] == 4_000
        assert points[0]["cost_per_turn_micros"] == 2_000
        assert points[1]["cost_micros"] == 2_000

    def test_month_granularity_collapses_to_one_bucket(self):
        async def go():
            await _wipe()
            await _add(cost=1_000, when=datetime(2026, 3, 2, tzinfo=timezone.utc))
            await _add(cost=2_000, when=datetime(2026, 3, 28, tzinfo=timezone.utc))
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).series(
                    enterprise_id=ENT,
                    frm=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    to=datetime(2026, 4, 1, tzinfo=timezone.utc),
                    granularity="month",
                )

        points = run(go)
        assert len(points) == 1 and points[0]["cost_micros"] == 3_000


class TestConversationViews:
    def test_conversations_ranked_and_scoped(self):
        async def go():
            await _wipe()
            cheap, dear = uuid.uuid4(), uuid.uuid4()
            async with AsyncSessionLocal() as session:
                for conv, cost in ((cheap, 1_000), (dear, 8_000), (dear, 2_000)):
                    session.add(
                        LlmUsageLedger(
                            id=uuid.uuid4(), enterprise_id=ENT, user_id=500,
                            is_internal_user=False, conversation_id=conv,
                            message_id=uuid.uuid4(), model="claude-sonnet-4-6",
                            input_tokens=100, cache_read_tokens=0,
                            cache_creation_tokens=0, output_tokens=10,
                            secondary_input_tokens=0, secondary_output_tokens=0,
                            cost_micros=cost, counted_against_pool=True,
                            created_at=datetime(2026, 3, 15, 12, tzinfo=timezone.utc),
                        )
                    )
                await session.commit()
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).conversations(
                    enterprise_id=ENT, period_start=PERIOD_START, period_end=PERIOD_END
                ), dear

        rows, dear = run(go)
        assert rows[0]["conversation_id"] == dear
        assert rows[0]["turns"] == 2 and rows[0]["cost_micros"] == 10_000

    def test_conversation_turns_do_not_cross_enterprises(self):
        """The scope is what protects this, not the UUID's entropy."""

        async def go():
            await _wipe()
            conv = uuid.uuid4()
            async with AsyncSessionLocal() as session:
                session.add(
                    LlmUsageLedger(
                        id=uuid.uuid4(), enterprise_id=OTHER_ENT, user_id=500,
                        is_internal_user=False, conversation_id=conv,
                        message_id=uuid.uuid4(), model="claude-sonnet-4-6",
                        input_tokens=100, cache_read_tokens=0, cache_creation_tokens=0,
                        output_tokens=10, secondary_input_tokens=0,
                        secondary_output_tokens=0, cost_micros=5_000,
                        counted_against_pool=True,
                        created_at=datetime(2026, 3, 15, 12, tzinfo=timezone.utc),
                    )
                )
                await session.commit()
            async with AsyncSessionLocal() as s:
                return await UsageRepository(s).conversation_turns(
                    enterprise_id=ENT, conversation_id=conv
                )

        assert run(go) == []


class TestResolvePeriod:
    """`period_start` accepts any date inside the month and snaps to it."""

    def test_snaps_any_date_in_the_month_to_its_start(self):
        from app.core.routers.usage import _resolve_period

        assert _resolve_period(date(2026, 8, 14)) == (date(2026, 8, 1), date(2026, 9, 1))
        assert _resolve_period(date(2026, 8, 1)) == (date(2026, 8, 1), date(2026, 9, 1))
        assert _resolve_period(date(2026, 8, 31)) == (date(2026, 8, 1), date(2026, 9, 1))

    def test_december_rolls_into_the_next_year(self):
        from app.core.routers.usage import _resolve_period

        assert _resolve_period(date(2026, 12, 9)) == (date(2026, 12, 1), date(2027, 1, 1))

    def test_none_means_the_current_period(self):
        from app.core.billing.budget_repo import period_bounds
        from app.core.routers.usage import _resolve_period

        assert _resolve_period(None) == period_bounds()


class TestAdminAttributesEachStaffMember:
    """Regression: the admin drill-down grouped on (user_id, is_internal_user)
    only, so every ClickPost employee on the shared account collapsed into one
    anonymous row and the attribution internal_user_id exists for was lost."""

    def test_two_staff_on_one_account_are_two_rows(self):
        from app.core.billing.budget_analytics_repo import (
            BudgetAnalyticsRepository,
        )

        async def go():
            await _wipe()
            await _add(user_id=14, internal_user_id="304875", is_internal_user=True,
                       counted=False, cost=5_000)
            await _add(user_id=14, internal_user_id="1013124", is_internal_user=True,
                       counted=False, cost=7_000)
            await _add(user_id=500, cost=3_000)  # a customer
            async with AsyncSessionLocal() as s:
                return await BudgetAnalyticsRepository(s).enterprise_users(
                    enterprise_id=ENT,
                    frm=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    to=datetime(2026, 4, 1, tzinfo=timezone.utc),
                )

        rows = run(go)
        staff = {r["internal_user_id"]: r["cost_micros"]
                 for r in rows if r["is_internal_user"]}
        assert staff == {"304875": 5_000, "1013124": 7_000}, staff

    def test_a_customer_row_carries_no_internal_user_id(self):
        from app.core.billing.budget_analytics_repo import (
            BudgetAnalyticsRepository,
        )

        async def go():
            await _wipe()
            await _add(user_id=500, cost=3_000)
            async with AsyncSessionLocal() as s:
                return await BudgetAnalyticsRepository(s).enterprise_users(
                    enterprise_id=ENT,
                    frm=datetime(2026, 3, 1, tzinfo=timezone.utc),
                    to=datetime(2026, 4, 1, tzinfo=timezone.utc),
                )

        rows = run(go)
        assert len(rows) == 1
        assert rows[0]["internal_user_id"] is None
        assert rows[0]["is_internal_user"] is False


class TestExport:
    """`export_rows` streams the CSV behind GET /enterprise/export.csv.

    Deliberately the one read here with no `is_internal_user` filter. It is a
    reconciliation dump rather than a display: the CSV carries
    `is_internal_user` and `counted_against_pool` as columns precisely so the
    reader can tell charged rows from uncharged ones, and dropping rows would
    stop the file reconciling against the ledger. The aggregates elsewhere in
    this module filter because they are summaries a customer reads directly.
    """

    @staticmethod
    async def _export(*, enterprise_id=ENT, start=PERIOD_START, end=PERIOD_END):
        async with AsyncSessionLocal() as s:
            return [
                row
                async for row in UsageRepository(s).export_rows(
                    enterprise_id=enterprise_id,
                    period_start=start,
                    period_end=end,
                )
            ]

    def test_the_export_is_complete_where_the_summaries_are_filtered(self):
        """Support rows belong in the file, flagged rather than omitted."""

        async def go():
            await _wipe()
            await _add(user_id=500, cost=1_000)
            await _add(
                user_id=14,
                internal_user_id="support",
                is_internal_user=True,
                counted=False,
                cost=9_000,
            )
            return await TestExport._export()

        rows = run(go)
        assert len(rows) == 2, "a filtered export would not reconcile with the ledger"
        flags = {(r.is_internal_user, r.counted_against_pool) for r in rows}
        assert flags == {(False, True), (True, False)}, (
            "the flag columns are what make including the support row safe"
        )

    def test_the_export_never_crosses_enterprises(self):
        async def go():
            await _wipe()
            await _add(enterprise_id=ENT, cost=1_000)
            await _add(enterprise_id=OTHER_ENT, cost=5_000)
            return await TestExport._export()

        rows = run(go)
        assert [r.cost_micros for r in rows] == [1_000]

    def test_the_export_window_is_half_open(self):
        """A row created exactly at `period_end` belongs to the next file.

        Getting this wrong bills the same turn in two months' exports.
        """

        async def go():
            await _wipe()
            await _add(when=datetime(2026, 2, 28, 23, 59, tzinfo=timezone.utc), cost=1)
            await _add(when=datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc), cost=2)
            await _add(when=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc), cost=3)
            return await TestExport._export()

        assert {r.cost_micros for r in run(go)} == {2}

    def test_the_export_is_ordered_oldest_first(self):
        """The file is streamed, so its order is whatever the query gives.

        A CSV that reconciles has to read chronologically.
        """

        async def go():
            await _wipe()
            for day, cost in ((20, 3), (5, 1), (12, 2)):
                await _add(
                    when=datetime(2026, 3, day, 12, 0, tzinfo=timezone.utc), cost=cost
                )
            return await TestExport._export()

        assert [r.cost_micros for r in run(go)] == [1, 2, 3]
