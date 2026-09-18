from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_WINDOW_HOURS = 24


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_reset_at() -> datetime:
    return _utc_now() + timedelta(hours=_WINDOW_HOURS)


class UserToken(Base):
    __tablename__ = "user_token"
    __table_args__ = (
        Index(
            "uq_user_token_active_external",
            "user_id",
            "enterprise_id",
            unique=True,
            postgresql_where=text("is_active AND NOT is_internal_user"),
        ),
        Index(
            "uq_user_token_active_internal",
            "user_id",
            "enterprise_id",
            "internal_user_id",
            unique=True,
            postgresql_where=text("is_active AND is_internal_user"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    enterprise_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    internal_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_internal_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="true"
    )
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=50_000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=8_096)
    used_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_message_token_usage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    random_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra_token: Mapped[int] = mapped_column(Integer, nullable=False, default=9_000)
    extra_token_count_request: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    extra_token_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_default_reset_at,
    )
