"""Enterprise spend caps: hold money before a turn, settle it afterwards.

A turn's cost is not known until it finishes , one question can cost half a
cent, another fifty , so a balance check before the work is not enough. This
uses the same shape as a card authorization: place a hold up front, replace it
with the real amount at the end.

The hold is what makes the cap correct under concurrency. Twenty requests
arriving in the same second would each read the same `spent_micros`, each
conclude there was room, and collectively spend the pool twenty times over.
Because `reserve` is a single conditional UPDATE, Postgres serializes them on
the row and the twentieth sees the nineteenth's write.

The reservation is admission control only. It never determines what is charged:
`settle` releases the estimate and adds the measured cost, so a badly chosen
estimate makes a 429 arrive early or late but can never produce a wrong figure
in the ledger.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.billing.budget_block_event_model import BudgetBlockEvent
from app.core.billing.enterprise_budget_model import EnterpriseBudget

logger = logging.getLogger(__name__)


def period_bounds(now: datetime | None = None) -> tuple[date, date]:
    """The calendar month containing `now`, as [start, end).

    Calendar rather than rolling: "how much did we spend in October" needs one
    answer, and a single-row read is what the admission check can afford.
    """
    now = now or datetime.now(timezone.utc)
    start = date(now.year, now.month, 1)
    end = date(now.year + 1, 1, 1) if now.month == 12 else date(now.year, now.month + 1, 1)
    return start, end


@dataclass(frozen=True)
class BudgetState:
    """Enough to answer the request and to explain a refusal to the caller."""

    allowed: bool
    enforcement_enabled: bool
    period_start: date
    period_end: date
    limit_micros: int
    spent_micros: int
    reserved_micros: int
    estimate_micros: int

    @property
    def remaining_micros(self) -> int:
        return max(0, self.limit_micros - self.spent_micros - self.reserved_micros)


class BudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- lifecycle -----------------------------------------------------------

    async def get(
        self, *, enterprise_id: int, period_start: date
    ) -> EnterpriseBudget | None:
        result = await self._session.execute(
            select(EnterpriseBudget).where(
                EnterpriseBudget.enterprise_id == enterprise_id,
                EnterpriseBudget.period_start == period_start,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, *, enterprise_id: int, period_start: date, period_end: date
    ) -> EnterpriseBudget:
        row = EnterpriseBudget(
            id=uuid.uuid4(),
            enterprise_id=enterprise_id,
            period_start=period_start,
            period_end=period_end,
            limit_micros=settings.budget_default_limit_micros,
            spent_micros=0,
            reserved_micros=0,
            enforcement_enabled=settings.budget_enforcement_default,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_or_create(
        self, *, enterprise_id: int, now: datetime | None = None
    ) -> EnterpriseBudget:
        """Fetch this period's row, creating it on first use.

        Two requests can reach this at the same instant on the first turn of a
        month. The unique index on (enterprise_id, period_start) settles it: the
        loser takes an IntegrityError inside a savepoint, rolls back only that,
        and re-reads the winner's row.
        """
        period_start, period_end = period_bounds(now)
        row = await self.get(enterprise_id=enterprise_id, period_start=period_start)
        if row is not None:
            return row

        try:
            async with self._session.begin_nested():
                return await self.create(
                    enterprise_id=enterprise_id,
                    period_start=period_start,
                    period_end=period_end,
                )
        except IntegrityError:
            row = await self.get(enterprise_id=enterprise_id, period_start=period_start)
            if row is None:
                raise
            return row

    # --- the hold ------------------------------------------------------------

    async def reserve(
        self,
        *,
        enterprise_id: int,
        estimate_micros: int | None = None,
        now: datetime | None = None,
    ) -> BudgetState:
        """Hold `estimate_micros` against this period, if there is room.

        One statement, so the read and the write cannot be separated by another
        request. Rows with enforcement off always match, which is what makes
        deploying this a no-op until a flag is flipped.
        """
        estimate = (
            settings.budget_reservation_estimate_micros
            if estimate_micros is None
            else estimate_micros
        )
        row = await self.get_or_create(enterprise_id=enterprise_id, now=now)

        result = await self._session.execute(
            update(EnterpriseBudget)
            .where(
                EnterpriseBudget.enterprise_id == enterprise_id,
                EnterpriseBudget.period_start == row.period_start,
                or_(
                    EnterpriseBudget.enforcement_enabled.is_(False),
                    EnterpriseBudget.spent_micros
                    + EnterpriseBudget.reserved_micros
                    + estimate
                    <= EnterpriseBudget.limit_micros,
                ),
            )
            .values(
                reserved_micros=EnterpriseBudget.reserved_micros + estimate,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(
                EnterpriseBudget.limit_micros,
                EnterpriseBudget.spent_micros,
                EnterpriseBudget.reserved_micros,
                EnterpriseBudget.enforcement_enabled,
            )
        )
        updated = result.first()

        if updated is not None:
            limit_micros, spent, reserved, enforcement = updated
            return BudgetState(
                allowed=True,
                enforcement_enabled=enforcement,
                period_start=row.period_start,
                period_end=row.period_end,
                limit_micros=limit_micros,
                spent_micros=spent,
                # `reserved` already includes this hold; report the figure the
                # caller is responsible for releasing separately.
                reserved_micros=reserved,
                estimate_micros=estimate,
            )

        # No room. Re-read for a payload that reflects the state that refused us.
        await self._session.refresh(row)
        return BudgetState(
            allowed=False,
            enforcement_enabled=row.enforcement_enabled,
            period_start=row.period_start,
            period_end=row.period_end,
            limit_micros=row.limit_micros,
            spent_micros=row.spent_micros,
            reserved_micros=row.reserved_micros,
            estimate_micros=estimate,
        )

    async def settle(
        self,
        *,
        enterprise_id: int,
        period_start: date,
        estimate_micros: int,
        actual_micros: int,
    ) -> None:
        """Replace the hold with the measured cost.

        `greatest(0, ...)` on the release guards against a double settle leaving
        the counter negative cheaper than making every caller prove it only
        settles once.
        """
        await self._session.execute(
            update(EnterpriseBudget)
            .where(
                EnterpriseBudget.enterprise_id == enterprise_id,
                EnterpriseBudget.period_start == period_start,
            )
            .values(
                reserved_micros=func.greatest(
                    0, EnterpriseBudget.reserved_micros - estimate_micros
                ),
                spent_micros=EnterpriseBudget.spent_micros + max(0, actual_micros),
                updated_at=datetime.now(timezone.utc),
            )
        )

    async def release(
        self, *, enterprise_id: int, period_start: date, estimate_micros: int
    ) -> None:
        """Drop the hold without charging — the turn produced nothing."""
        await self._session.execute(
            update(EnterpriseBudget)
            .where(
                EnterpriseBudget.enterprise_id == enterprise_id,
                EnterpriseBudget.period_start == period_start,
            )
            .values(
                reserved_micros=func.greatest(
                    0, EnterpriseBudget.reserved_micros - estimate_micros
                ),
                updated_at=datetime.now(timezone.utc),
            )
        )

    # --- observability -------------------------------------------------------

    async def record_block(
        self,
        *,
        enterprise_id: int,
        user_id: int,
        is_internal_user: bool,
        limit_kind: str,
        state: BudgetState | None = None,
    ) -> None:
        """Log a refusal so "how often are we blocking people" has an answer.

        Counters are snapshotted rather than joined later: the budget row keeps
        moving, and what matters is the state at the moment of the decision.
        """
        self._session.add(
            BudgetBlockEvent(
                id=uuid.uuid4(),
                enterprise_id=enterprise_id,
                user_id=user_id,
                is_internal_user=is_internal_user,
                limit_kind=limit_kind,
                period_start=state.period_start if state else None,
                limit_micros=state.limit_micros if state else None,
                spent_micros=state.spent_micros if state else None,
                reserved_micros=state.reserved_micros if state else None,
                estimate_micros=state.estimate_micros if state else None,
            )
        )
        await self._session.flush()
