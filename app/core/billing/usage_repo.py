"""Usage queries a customer is allowed to run about themselves.

Deliberately separate from BudgetAnalyticsRepository, which answers questions
across every enterprise for the internal dashboard. Everything here is scoped to
one enterprise, and the scope is applied in this layer rather than left to the
caller — a missing WHERE clause in an internal tool shows staff too much, the
same mistake here shows one customer another customer's spend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.billing.usage_ledger_model import LlmUsageLedger


def period_window(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """Half-open UTC bounds `[start, end)` for a billing period.

    Matches `period_bounds()`, which returns the *first day of the next month*
    as its end rather than the last day of this one. Treating that as an
    inclusive date instead would stretch September's window across the whole of
    1 October, counting those turns in both months.

    Callers must compare with `< end`, never `<= end`.
    """
    return (
        datetime.combine(period_start, time.min, tzinfo=timezone.utc),
        datetime.combine(period_end, time.min, tzinfo=timezone.utc),
    )


@dataclass(frozen=True)
class UsageTotals:
    """What was spent and what it was spent on."""

    turns: int
    cost_micros: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    last_active: datetime | None

    @property
    def total_tokens(self) -> int:
        # `input_tokens` already contains the cached counts (see the ledger
        # model), so adding them again here would double-count the prompt.
        return self.input_tokens + self.output_tokens


_EMPTY = UsageTotals(0, 0, 0, 0, 0, 0, None)


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_user(
        self,
        *,
        enterprise_id: int,
        user_id: int,
        is_internal_user: bool,
        internal_user_id: str | None,
        period_start: date,
        period_end: date,
    ) -> UsageTotals:
        """One person's own usage this period.

        Matched on the full identity quadruple rather than `user_id` alone:
        ClickPost staff share a single enterprise account, so filtering on
        user_id would hand every one of them the sum of all their colleagues'
        activity.
        """
        start, end = period_window(period_start, period_end)
        # Written out rather than relying on `== None` becoming IS NULL: a
        # customer has no internal_user_id, and a clause that quietly matched
        # nothing there would report their spend as zero.
        if internal_user_id is None:
            identity_clause = LlmUsageLedger.internal_user_id.is_(None)
        else:
            identity_clause = LlmUsageLedger.internal_user_id == internal_user_id

        stmt = self._totals_select().where(
            LlmUsageLedger.enterprise_id == enterprise_id,
            LlmUsageLedger.user_id == user_id,
            LlmUsageLedger.is_internal_user.is_(is_internal_user),
            identity_clause,
            LlmUsageLedger.created_at >= start,
            LlmUsageLedger.created_at < end,
        )
        return self._row_to_totals((await self._session.execute(stmt)).first())

    async def for_enterprise(
        self,
        *,
        enterprise_id: int,
        period_start: date,
        period_end: date,
        counted_only: bool = True,
    ) -> UsageTotals:
        """The enterprise's usage this period.

        `counted_only` defaults to True so the figure lines up with the number
        the cap is enforced against. ClickPost support turns are recorded
        against the enterprise but never charged to it, and including them here
        would show a customer spend they are not paying for and cannot explain.
        """
        start, end = period_window(period_start, period_end)
        stmt = self._totals_select().where(
            LlmUsageLedger.enterprise_id == enterprise_id,
            LlmUsageLedger.created_at >= start,
            LlmUsageLedger.created_at < end,
        )
        if counted_only:
            stmt = stmt.where(LlmUsageLedger.counted_against_pool.is_(True))
        return self._row_to_totals((await self._session.execute(stmt)).first())

    async def active_users(
        self, *, enterprise_id: int, period_start: date, period_end: date
    ) -> int:
        """Distinct people who used the agent this period.

        Counted on the (user_id, internal_user_id) pair for the same reason
        `for_user` matches on it: on a shared account the user_id alone
        collapses every ClickPost employee into one.
        """
        start, end = period_window(period_start, period_end)
        # A subquery rather than COUNT(DISTINCT a, b): counting distinct pairs
        # inline needs a row constructor that does not travel between dialects,
        # and the admin repo's COUNT(DISTINCT user_id) is wrong here for the
        # shared-account reason above.
        pairs = (
            select(LlmUsageLedger.user_id, LlmUsageLedger.internal_user_id)
            .where(
                LlmUsageLedger.enterprise_id == enterprise_id,
                LlmUsageLedger.created_at >= start,
                LlmUsageLedger.created_at < end,
            )
            .distinct()
            .subquery()
        )
        stmt = select(func.count()).select_from(pairs)
        return int((await self._session.execute(stmt)).scalar() or 0)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _totals_select():
        return select(
            func.count(),
            func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0),
            func.coalesce(
                func.sum(
                    LlmUsageLedger.input_tokens + LlmUsageLedger.secondary_input_tokens
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    LlmUsageLedger.output_tokens + LlmUsageLedger.secondary_output_tokens
                ),
                0,
            ),
            func.coalesce(func.sum(LlmUsageLedger.cache_read_tokens), 0),
            func.coalesce(func.sum(LlmUsageLedger.cache_creation_tokens), 0),
            func.max(LlmUsageLedger.created_at),
        )

    @staticmethod
    def _row_to_totals(row) -> UsageTotals:
        if row is None:
            return _EMPTY
        turns, cost, tok_in, tok_out, cache_read, cache_create, last = row
        return UsageTotals(
            turns=int(turns or 0),
            cost_micros=int(cost or 0),
            input_tokens=int(tok_in or 0),
            output_tokens=int(tok_out or 0),
            cache_read_tokens=int(cache_read or 0),
            cache_creation_tokens=int(cache_create or 0),
            last_active=last,
        )

    # -- D3-D7: the enterprise-admin views ---------------------------------

    async def users(
        self, *, enterprise_id: int, period_start: date, period_end: date
    ) -> list[dict]:
        """Per-member spend, ranked.

        Internal ClickPost rows are excluded outright rather than flagged. To a
        customer this is "your team", and a support engineer who opened their
        account for an hour is not a member of it — listing them invites the
        question of why a stranger is spending their budget, when in fact that
        spend never touched it.
        """
        start, end = period_window(period_start, period_end)
        rows = (
            await self._session.execute(
                select(
                    LlmUsageLedger.user_id,
                    func.count(),
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0),
                    func.coalesce(
                        func.sum(
                            LlmUsageLedger.input_tokens
                            + LlmUsageLedger.secondary_input_tokens
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            LlmUsageLedger.output_tokens
                            + LlmUsageLedger.secondary_output_tokens
                        ),
                        0,
                    ),
                    func.max(LlmUsageLedger.created_at),
                )
                .where(
                    LlmUsageLedger.enterprise_id == enterprise_id,
                    LlmUsageLedger.is_internal_user.is_(False),
                    LlmUsageLedger.created_at >= start,
                    LlmUsageLedger.created_at < end,
                )
                .group_by(LlmUsageLedger.user_id)
                .order_by(func.sum(LlmUsageLedger.cost_micros).desc())
            )
        ).all()
        return [
            {
                "user_id": uid,
                "turns": int(turns),
                "cost_micros": int(cost),
                "input_tokens": int(tin),
                "output_tokens": int(tout),
                "last_active": last,
            }
            for uid, turns, cost, tin, tout, last in rows
        ]

    async def series(
        self,
        *,
        enterprise_id: int,
        frm: datetime,
        to: datetime,
        granularity: str = "day",
    ) -> list[dict]:
        """Cost and volume over time, bucketed by day, week or month.

        One endpoint serves all three views because the shape is identical and
        only the truncation changes — three endpoints would be three places to
        fix the next time the money columns move.
        """
        if granularity not in ("day", "week", "month"):
            raise ValueError(f"granularity must be day, week or month, not {granularity!r}")

        bucket = func.date_trunc(granularity, LlmUsageLedger.created_at)
        rows = (
            await self._session.execute(
                select(
                    bucket.label("bucket"),
                    func.count(),
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0),
                    func.coalesce(
                        func.sum(
                            LlmUsageLedger.input_tokens
                            + LlmUsageLedger.secondary_input_tokens
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            LlmUsageLedger.output_tokens
                            + LlmUsageLedger.secondary_output_tokens
                        ),
                        0,
                    ),
                    func.count(func.distinct(LlmUsageLedger.user_id)),
                )
                .where(
                    LlmUsageLedger.enterprise_id == enterprise_id,
                    LlmUsageLedger.counted_against_pool.is_(True),
                    LlmUsageLedger.created_at >= frm,
                    LlmUsageLedger.created_at < to,
                )
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()
        return [
            {
                "bucket": b,
                "turns": int(turns),
                "cost_micros": int(cost),
                "input_tokens": int(tin),
                "output_tokens": int(tout),
                "active_users": int(users),
                # Integer division, matching how the admin trend reports it —
                # both round once per bucket rather than per turn.
                "cost_per_turn_micros": int(cost) // int(turns) if turns else 0,
            }
            for b, turns, cost, tin, tout, users in rows
        ]

    async def conversations(
        self,
        *,
        enterprise_id: int,
        period_start: date,
        period_end: date,
        limit: int = 20,
    ) -> list[dict]:
        """Conversations ranked by cost — where the money actually went."""
        start, end = period_window(period_start, period_end)
        rows = (
            await self._session.execute(
                select(
                    LlmUsageLedger.conversation_id,
                    LlmUsageLedger.user_id,
                    func.count(),
                    func.coalesce(func.sum(LlmUsageLedger.cost_micros), 0),
                    func.coalesce(
                        func.sum(
                            LlmUsageLedger.input_tokens
                            + LlmUsageLedger.secondary_input_tokens
                            + LlmUsageLedger.output_tokens
                            + LlmUsageLedger.secondary_output_tokens
                        ),
                        0,
                    ),
                    func.min(LlmUsageLedger.created_at),
                    func.max(LlmUsageLedger.created_at),
                )
                .where(
                    LlmUsageLedger.enterprise_id == enterprise_id,
                    LlmUsageLedger.is_internal_user.is_(False),
                    LlmUsageLedger.created_at >= start,
                    LlmUsageLedger.created_at < end,
                )
                .group_by(LlmUsageLedger.conversation_id, LlmUsageLedger.user_id)
                .order_by(func.sum(LlmUsageLedger.cost_micros).desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "conversation_id": cid,
                "user_id": uid,
                "turns": int(turns),
                "cost_micros": int(cost),
                "total_tokens": int(tok),
                "started_at": first,
                "last_active": last,
            }
            for cid, uid, turns, cost, tok, first, last in rows
        ]

    async def conversation_turns(
        self, *, enterprise_id: int, conversation_id
    ) -> list[dict]:
        """Turn-by-turn detail inside one conversation.

        Scoped by enterprise_id as well as conversation_id even though the id is
        a UUID: guessing one is impractical, but the scope is what makes that a
        property of the query rather than of the id's entropy.
        """
        rows = (
            await self._session.execute(
                select(LlmUsageLedger)
                .where(
                    LlmUsageLedger.enterprise_id == enterprise_id,
                    LlmUsageLedger.conversation_id == conversation_id,
                    LlmUsageLedger.is_internal_user.is_(False),
                )
                .order_by(LlmUsageLedger.created_at)
            )
        ).scalars().all()
        return [
            {
                "created_at": r.created_at,
                "user_id": r.user_id,
                "model": r.model,
                "cost_micros": r.cost_micros,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "cache_creation_tokens": r.cache_creation_tokens,
                "secondary_model": r.secondary_model,
                "secondary_input_tokens": r.secondary_input_tokens,
                "secondary_output_tokens": r.secondary_output_tokens,
                "pricing_failed": r.pricing_failed,
            }
            for r in rows
        ]

    async def export_rows(
        self, *, enterprise_id: int, period_start: date, period_end: date
    ):
        """Raw ledger rows for the period, oldest first, for CSV export.

        Streamed with `.stream_scalars()` rather than materialised: a busy month
        is tens of thousands of rows, and building the whole list in memory to
        serialise it straight back out is the easy way to make an export the
        thing that takes the service down.
        """
        start, end = period_window(period_start, period_end)
        result = await self._session.stream_scalars(
            select(LlmUsageLedger)
            .where(
                LlmUsageLedger.enterprise_id == enterprise_id,
                LlmUsageLedger.created_at >= start,
                LlmUsageLedger.created_at < end,
            )
            .order_by(LlmUsageLedger.created_at)
        )
        async for row in result:
            yield row
