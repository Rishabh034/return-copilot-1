from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LlmUsageLedger(Base):
    """One immutable row per settled turn: what it used, and what it cost.

    Append-only. Rows are never updated or deleted, which is what makes this
    usable as an audit trail and what lets history be re-priced if a rate in
    app/core/billing/pricing.py turns out to be wrong — the token
    counts and the model id are both preserved for exactly that reason.

    "One turn" means one user message, not one API call. A single turn may run
    up to `rr_analyst_max_iterations` agent iterations plus several scratchpad
    calls; their usage is summed into this row.

    Distinct from `user_token`, which counts tokens for rate limiting and
    deliberately under-counts (it charges only the growth over the largest
    prompt seen in a window). That number is not money and must never be used
    as though it were.
    """

    __tablename__ = "llm_usage_ledger"
    __table_args__ = (
        Index("ix_ledger_enterprise_created", "enterprise_id", "created_at"),
        Index("ix_ledger_user_created", "user_id", "created_at"),
        Index("ix_ledger_conversation", "conversation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- who -----------------------------------------------------------------
    enterprise_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_internal_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    internal_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- what ----------------------------------------------------------------
    # Not FKs: a ledger row is a financial record and must survive the
    # conversation being deleted, which cascades from `conversation`.
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Which agent served this turn. Denormalised rather than joined through
    # `conversation`: cost-per-agent is the first question anyone asks once a
    # second agent exists, and a join that happens to work today is one schema
    # change away from silently dropping rows from a spend report.
    agent_slug: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, server_default="allocation"
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    model: Mapped[str] = mapped_column(Text, nullable=False)
    # `input_tokens` is the whole prompt and *includes* the two cache figures
    # below — see pricing._uncached_input_tokens before doing arithmetic on it.
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    secondary_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    secondary_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    secondary_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # --- money ---------------------------------------------------------------
    # Integer micro-dollars. Never a float, never cents.
    cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # What the customer would be charged, once billing exists. Null until then;
    # the column is here now because back-filling it across months of history
    # later is considerably more expensive than reserving it today.
    billable_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # False for ClickPost support activity: recorded against the enterprise so
    # support cost per customer stays reportable, but excluded from their cap.
    counted_against_pool: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Set when pricing failed (unknown model). The row still carries full token
    # counts, so it can be re-priced once a rate is added.
    pricing_failed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
