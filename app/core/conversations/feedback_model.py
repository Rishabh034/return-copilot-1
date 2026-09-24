from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Feedback(Base):
    """User feedback on a single assistant turn.

    A turn is identified by (conversation_id, seq) — the same natural key the
    stream hands back to the client in its `done` event. `seq` is stored as an
    opaque anchor: it is whatever the client received, not re-derived here.
    """

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    enterprise_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    internal_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_internal_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="true"
    )
    positive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
