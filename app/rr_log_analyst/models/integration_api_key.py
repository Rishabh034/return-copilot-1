from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

KEY_PREFIX = "cpai_"
# 32 random bytes, urlsafe-base64 encoded by token_urlsafe -> 43 chars.
_KEY_ENTROPY_BYTES = 32
# Enough of the key to recognise it in a log line or the management script,
# far too little to guess the remaining ~250 bits.
DISPLAY_PREFIX_CHARS = 12


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_api_key() -> str:
    """Mint a new plaintext key. This value is never stored and never recoverable."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(_KEY_ENTROPY_BYTES)}"


def hash_api_key(plaintext: str) -> str:
    """SHA-256 hex of a key, which is the only form that reaches the database.

    Unsalted and unstretched on purpose, unlike a password hash. This is 256
    bits of machine-generated entropy, so there is no dictionary to attack and
    nothing for a work factor to buy — while an unkeyed digest is what lets the
    auth path find the row with a single indexed equality lookup per request.
    """
    return hashlib.sha256(plaintext.strip().encode("utf-8")).hexdigest()


def display_prefix(plaintext: str) -> str:
    return plaintext.strip()[:DISPLAY_PREFIX_CHARS]


class IntegrationApiKey(Base):
    """One calling service, its spend cap, and the current month's counters.

    Identity and usage share a row deliberately. The counters are not a record
    of anything — `integration_request_log` holds every priced request with its
    timestamp, so month-by-month history is a SUM over that table. These columns
    exist only so admission control is one indexed row read instead of an
    aggregate, and a cache needs no history of its own.

    `monthly_limit_micros IS NULL` means unlimited, which is where every key
    starts. Provisioning a cap later is an UPDATE on this column — no migration,
    no redeploy, and it takes effect on the next request rather than at the next
    period roll, which is the other reason the limit lives here rather than on a
    per-period row.

    `spent_micros` is settled money; `reserved_micros` is the running total of
    holds for requests in flight right now. Without the second figure, twenty
    concurrent requests would each read the same `spent_micros`, all pass the
    check, and overshoot the cap twenty times over. See
    IntegrationKeyRepository.reserve for how both are moved in one statement.
    """

    __tablename__ = "integration_api_key"
    __table_args__ = (
        # Unique among *active* keys only, the same partial-index pattern as
        # user_token. A revoked key keeps its name for the audit trail, so a
        # plain UNIQUE would make the name unusable forever and force every
        # rotation to invent a new one — exactly when you are in a hurry, and
        # at the cost of the name in your logs drifting from the service it
        # identifies.
        Index(
            "uq_integration_api_key_active_name",
            "name",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Who is calling — a service name, e.g. "clickpost-support-bot".
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)

    # NULL = unlimited. Integer micro-dollars, as everywhere else.
    monthly_limit_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # The calendar month these counters describe. NULL until the key's first
    # request; a stale value resets the counters in the same statement that
    # takes the next hold, so no separate roll-over job exists.
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    spent_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reserved_micros: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Revocation is a flag, never a DELETE: request-log rows reference this key
    # and must stay attributable after it is turned off.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
