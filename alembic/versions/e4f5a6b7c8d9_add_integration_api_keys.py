"""add integration api keys and request log

Authentication, usage and auditing for /integrations/query. Callers are internal
ClickPost services identified by an API key we issue; the key carries the cap
and the bill, while the Clickpost license key in each request body decides which
enterprise's data that request may reach.

Keys are created uncapped (monthly_limit_micros NULL), so this migration blocks
nothing. A cap is provisioned later with an UPDATE on that column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, Sequence[str], None] = "b3c9d1e7f204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- integration_api_key ------------------------------------------------
    # Identity and the current month's counters share a row on purpose: the
    # counters are a cache for admission control, and the history they would
    # otherwise keep is derivable from integration_request_log.
    op.create_table(
        "integration_api_key",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        # NULL = unlimited, which is how every key starts.
        sa.Column("monthly_limit_micros", sa.BigInteger(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column(
            "spent_micros", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "reserved_micros", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash", name="uq_integration_api_key_hash"),
    )
    # The auth path's only lookup: one indexed equality per request.
    op.create_index(
        "ix_integration_api_key_hash", "integration_api_key", ["key_hash"]
    )
    op.create_index("ix_integration_api_key_name", "integration_api_key", ["name"])
    # Names are unique among *active* keys only. A revoked key keeps its name so
    # its request-log rows stay readable, and the name is immediately reusable by
    # the key that replaces it — rotation must not force a rename.
    op.create_index(
        "uq_integration_api_key_active_name",
        "integration_api_key",
        ["name"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    # --- integration_request_log --------------------------------------------
    # Append-only, written for refused and failed requests too. Also the money
    # record for any period other than the current one.
    op.create_table(
        "integration_request_log",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("api_key_id", UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("license_key_hash", sa.String(length=64), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=True),
        sa.Column("enterprise_name", sa.Text(), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("tools_used", JSONB(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cache_read_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("secondary_model", sa.Text(), nullable=True),
        sa.Column(
            "secondary_input_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "secondary_output_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "pricing_failed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        # RESTRICT, not CASCADE: keys are revoked rather than deleted, and the
        # audit trail must not be removable by deleting the key behind it.
        sa.ForeignKeyConstraint(
            ["api_key_id"], ["integration_api_key.id"], ondelete="RESTRICT"
        ),
    )
    op.create_index(
        "ix_integration_log_key_created",
        "integration_request_log",
        ["api_key_id", "created_at"],
    )
    op.create_index(
        "ix_integration_log_license_created",
        "integration_request_log",
        ["license_key_hash", "created_at"],
    )
    op.create_index(
        "ix_integration_request_log_request_id",
        "integration_request_log",
        ["request_id"],
    )
    op.create_index(
        "ix_integration_request_log_created_at",
        "integration_request_log",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_request_log_created_at", table_name="integration_request_log"
    )
    op.drop_index(
        "ix_integration_request_log_request_id", table_name="integration_request_log"
    )
    op.drop_index(
        "ix_integration_log_license_created", table_name="integration_request_log"
    )
    op.drop_index(
        "ix_integration_log_key_created", table_name="integration_request_log"
    )
    op.drop_table("integration_request_log")
    op.drop_index(
        "uq_integration_api_key_active_name", table_name="integration_api_key"
    )
    op.drop_index("ix_integration_api_key_name", table_name="integration_api_key")
    op.drop_index("ix_integration_api_key_hash", table_name="integration_api_key")
    op.drop_table("integration_api_key")
