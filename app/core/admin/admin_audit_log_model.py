from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utc_now


class AdminAuditLog(Base):
    """Who changed what, on the admin surface.

    Append-only. Every write that alters an enterprise's budget or the admin
    allowlist lands here before the response is returned.

    The actor is stored as a pair. `actor_user_id` is the signed claim and is
    trustworthy but not distinguishing - all ClickPost internal staff share one
    Django account. `actor_internal_user_id` is the individual, and is only as
    trustworthy as its source: today the caller supplies it in a header, so it
    identifies rather than proves. Once clickpost signs that claim (ticket F3)
    this becomes a genuine non-repudiable record with no change here.
    """

    __tablename__ = "admin_audit_log"

    # Actions, kept as constants so a typo is a NameError rather than a row
    # nobody can find later.
    BUDGET_LIMIT_CHANGED = "budget.limit_changed"
    BUDGET_ENFORCEMENT_CHANGED = "budget.enforcement_changed"
    ADMIN_GRANTED = "admin.granted"
    ADMIN_REVOKED = "admin.revoked"

    TARGET_ENTERPRISE = "enterprise"
    TARGET_ADMIN = "admin"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_internal_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    action: Mapped[str] = mapped_column(String(48), nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)

    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
