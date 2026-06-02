import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.rr_log_analyst.models.base import Base


class Conversation(Base):
    __tablename__ = "rr_log_analyst_conversation"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    enterprise_id = Column(Integer, nullable=False)
    title = Column(Text, nullable=True)
    system_prompt = Column(Text, nullable=False)
    model = Column(String(100), nullable=False, default="claude-sonnet-4-20250514")
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_conversation_enterprise", "enterprise_id", "updated_at"),
    )
