from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BudgetBlockEvent(Base):
    """A record of every request the limit layer turned away.
    Counters are snapshotted at block time rather than joined later, because the
    budget row keeps moving
    """

    __tablename__ = "budget_block_event"
    __table_args__ = (
        Index("ix_block_enterprise_created", "enterprise_id", "created_at"),
    )

    # Values for `limit_kind`, mirroring the 429 `code` returned to the client.
    ENTERPRISE_BUDGET = "budget_enterprise"
    USER_BUDGET = "budget_user"
    USER_TOKEN_WINDOW = "quota_user_window"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enterprise_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_internal_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    limit_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)

    # State at the moment of the decision. Null for the token-window limit,
    # which is counted in tokens rather than money.
    limit_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    spent_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reserved_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimate_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
