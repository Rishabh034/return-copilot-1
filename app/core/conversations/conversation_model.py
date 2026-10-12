from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utc_now


class Conversation(Base):
    __tablename__ = "conversation"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    internal_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_internal_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="true"
    )
    user_id : Mapped[int] = mapped_column(Integer, nullable=False, index=True,default=0)
    enterprise_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Which agent owns this thread. Not routing — the URL decides that — but
    # scoping: an agent lists only its own conversations, and the usage ledger
    # can be attributed per agent. Backfilled to 'allocation' because every
    # row that existed when this column landed was one.
    agent_slug: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, server_default="allocation"
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.seq",
    )
