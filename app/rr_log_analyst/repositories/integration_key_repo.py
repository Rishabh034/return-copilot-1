"""Integration API keys: authenticate the caller, hold spend, settle it.

Same card-authorization shape as app/rr_log_analyst/repositories/budget_repo.py,
and for the same reason: a request's cost is unknown until it finishes, so a
balance check before the work cannot bound it. Place a hold up front, replace it
with the measured amount at the end.

The difference is that a key's counters live on the key row itself rather than
in a per-period table, which lets the month roll and the hold be taken in one
statement. Two consequences worth knowing:

- Every request for a key writes that key's row, so the reserve must be
  committed before the agent runs. Holding the transaction open across a
  30-second model call would serialize every caller behind the row lock.
- A request that starts on the 31st and settles on the 1st adds its cost to the
  new period. That is one request's worth of drift in a cache; the request log
  is dated correctly either way, and a period guard on settle would instead
  drop the cost entirely.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.rr_log_analyst.models.integration_api_key import IntegrationApiKey
from app.rr_log_analyst.repositories.budget_repo import period_bounds


@dataclass(frozen=True)
class KeyBudgetState:
    """Enough to answer the request and to explain a refusal to the caller."""

    allowed: bool
    period_start: date
    period_end: date
    limit_micros: int | None
    spent_micros: int
    reserved_micros: int
    estimate_micros: int

    @property
    def unlimited(self) -> bool:
        return self.limit_micros is None

    @property
    def remaining_micros(self) -> int | None:
        if self.limit_micros is None:
            return None
        return max(0, self.limit_micros - self.spent_micros - self.reserved_micros)


class IntegrationKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- authentication ------------------------------------------------------

    async def get_active_by_hash(self, key_hash: str) -> IntegrationApiKey | None:
        result = await self._session.execute(
            select(IntegrationApiKey).where(
                IntegrationApiKey.key_hash == key_hash,
                IntegrationApiKey.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get(self, api_key_id: uuid.UUID) -> IntegrationApiKey | None:
        return await self._session.get(IntegrationApiKey, api_key_id)

    # --- the hold ------------------------------------------------------------

    async def reserve(
        self,
        *,
        api_key_id: uuid.UUID,
        estimate_micros: int,
        now: datetime | None = None,
    ) -> KeyBudgetState:
        """Hold `estimate_micros` against this month, if there is room.

        One statement, so the read and the write cannot be separated by another
        request: Postgres serializes concurrent updates on the row, and the
        twentieth request sees the nineteenth's write.

        The same statement rolls the period. Postgres evaluates every SET
        expression against the row's *old* values, so the CASEs below still see
        the stored `period_start` while it is being overwritten — a key whose
        counters describe a previous month starts the new one at zero without a
        separate roll-over job, and without a first-request-of-the-month race.

        A key with no limit always matches, which is what makes every key
        unlimited until someone provisions a cap.
        """
        now = now or datetime.now(timezone.utc)
        period_start, period_end = period_bounds(now)

        current_period = IntegrationApiKey.period_start == period_start
        spent = case((current_period, IntegrationApiKey.spent_micros), else_=0)
        reserved = case((current_period, IntegrationApiKey.reserved_micros), else_=0)
        count = case((current_period, IntegrationApiKey.request_count), else_=0)

        result = await self._session.execute(
            update(IntegrationApiKey)
            .where(
                IntegrationApiKey.id == api_key_id,
                IntegrationApiKey.is_active.is_(True),
                or_(
                    IntegrationApiKey.monthly_limit_micros.is_(None),
                    spent + reserved + estimate_micros
                    <= IntegrationApiKey.monthly_limit_micros,
                ),
            )
            .values(
                period_start=period_start,
                period_end=period_end,
                spent_micros=spent,
                reserved_micros=reserved + estimate_micros,
                request_count=count + 1,
                last_used_at=now,
            )
            .returning(
                IntegrationApiKey.monthly_limit_micros,
                IntegrationApiKey.spent_micros,
                IntegrationApiKey.reserved_micros,
            )
        )
        updated = result.first()

        if updated is not None:
            limit_micros, spent_micros, reserved_micros = updated
            return KeyBudgetState(
                allowed=True,
                period_start=period_start,
                period_end=period_end,
                limit_micros=limit_micros,
                spent_micros=spent_micros,
                # Already includes this hold, which the caller is separately
                # responsible for settling or releasing.
                reserved_micros=reserved_micros,
                estimate_micros=estimate_micros,
            )

        # No room. Re-read for a payload describing the state that refused us.
        row = await self.get(api_key_id)
        if row is not None:
            await self._session.refresh(row)
        return KeyBudgetState(
            allowed=False,
            period_start=period_start,
            period_end=period_end,
            limit_micros=row.monthly_limit_micros if row else 0,
            spent_micros=row.spent_micros if row else 0,
            reserved_micros=row.reserved_micros if row else 0,
            estimate_micros=estimate_micros,
        )

    async def settle(
        self,
        *,
        api_key_id: uuid.UUID,
        estimate_micros: int,
        actual_micros: int,
        now: datetime | None = None,
    ) -> None:
        """Replace the hold with the measured cost.

        `greatest(0, ...)` on the release is cheaper than making every caller
        prove it settles exactly once, and keeps a double settle from driving
        the counter negative.
        """
        await self._session.execute(
            update(IntegrationApiKey)
            .where(IntegrationApiKey.id == api_key_id)
            .values(
                reserved_micros=func.greatest(
                    0, IntegrationApiKey.reserved_micros - estimate_micros
                ),
                spent_micros=IntegrationApiKey.spent_micros + max(0, actual_micros),
                last_used_at=now or datetime.now(timezone.utc),
            )
        )

    async def release(
        self, *, api_key_id: uuid.UUID, estimate_micros: int
    ) -> None:
        """Drop the hold without charging — the request produced nothing."""
        await self._session.execute(
            update(IntegrationApiKey)
            .where(IntegrationApiKey.id == api_key_id)
            .values(
                reserved_micros=func.greatest(
                    0, IntegrationApiKey.reserved_micros - estimate_micros
                ),
            )
        )
