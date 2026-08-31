from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EnterpriseBudget(Base):
    """One enterprise's spend cap for one calendar month.

    Created lazily on the enterprise's first turn of a period, the same
    get-or-create-with-IntegrityError-retry pattern used by
    app/rr_log_analyst/repositories/user_token_repo.py.

    Two counters, and the difference matters:

    `spent_micros` is settled money, turns that finished and were priced.
    `reserved_micros` is the running total of holds for turns *in flight right
    now*. It rises when a turn starts and falls when it settles, and sits at
    zero when the system is idle. Without it, twenty concurrent requests would
    each read the same `spent_micros`, all pass the check, and blow through the
    cap twenty times over.
    """

    __tablename__ = "enterprise_budget"
    __table_args__ = (
        Index(
            "uq_enterprise_budget_period",
            "enterprise_id",
            "period_start",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enterprise_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    # Calendar month: period_start is the 1st, period_end the 1st of the next
    # month (exclusive). Stored rather than derived so a future change to the
    # period length cannot silently reinterpret existing rows.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    limit_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spent_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reserved_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    # Users on this enterprise, supplied by Clickpost. Divisor for the derived
    # per-user share in track E; unused until then.
    seat_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The rollout switch. False means spend is recorded but never blocked, so
    # deploying this feature changes nothing until it is flipped per enterprise.
    enforcement_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )
