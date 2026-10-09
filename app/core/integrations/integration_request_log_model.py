from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utc_now


class IntegrationRequestLog(Base):
    """One immutable row per /integrations/query request: what was asked,
    what was answered, and what it cost.

    Append-only, and written for refused and failed requests too — a request
    that was blocked by a cap or died in the agent is exactly the one someone
    will come looking for. This is also the money record: `integration_api_key`
    only carries the current month's running totals, so any question about a
    past period is answered by aggregating here.

    Unlike `/messages/`, nothing here is conversational. Each row is a complete
    question-and-answer, and there is no thread to continue.
    """

    __tablename__ = "integration_request_log"
    __table_args__ = (
        Index("ix_integration_log_key_created", "api_key_id", "created_at"),
        Index("ix_integration_log_license_created", "license_key_hash", "created_at"),
    )

    # Values for `status`.
    OK = "ok"
    ERROR = "error"
    BLOCKED = "blocked"
    INVALID_LICENSE_KEY = "invalid_license_key"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # RESTRICT rather than CASCADE: keys are revoked, never deleted, and the
    # audit trail must not be removable by deleting the key that produced it.
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integration_api_key.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Returned to the caller, so a line in their logs maps to a row here.
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # A hash, because the license key is a live Clickpost credential and this
    # table is not a place to keep one. Enough to group every request made about
    # the same enterprise.
    license_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Resolved by the pre-flight, which asks Clickpost who the license key
    # belongs to. Nullable because that lookup fails open: a request is never
    # refused for want of an enterprise, it is only recorded without one.
    # Audit-only — neither field ever reaches the prompt, so a caller cannot
    # assert an enterprise the key does not grant.
    enterprise_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enterprise_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools_used: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # --- what it used --------------------------------------------------------
    # `input_tokens` is the whole prompt and *includes* the two cache figures;
    # see pricing._uncached_input_tokens before doing arithmetic on it.
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    secondary_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    secondary_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    secondary_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # --- money ---------------------------------------------------------------
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Set when pricing failed (unknown model). The row still carries full token
    # counts, so it can be re-priced once a rate is added.
    pricing_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # --- outcome -------------------------------------------------------------
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
