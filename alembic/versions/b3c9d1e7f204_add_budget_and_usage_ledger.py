"""add usage ledger, enterprise budget and block events

Introduces per-enterprise spend caps. enforcement_enabled defaults to false,
so the ledger records spend while nothing is ever blocked. Enforcement is
switched on one enterprise at a time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "b3c9d1e7f204"
down_revision: Union[str, Sequence[str], None] = "767203832054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- llm_usage_ledger ---------------------------------------------------
    # Append-only. conversation_id and message_id are intentionally NOT foreign
    # keys: `conversation` cascades on delete, and a financial record must not
    # disappear with the chat it describes.
    op.create_table(
        "llm_usage_ledger",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_internal_user", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("internal_user_id", sa.Text(), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
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
        sa.Column("billable_micros", sa.BigInteger(), nullable=True),
        sa.Column(
            "counted_against_pool", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "pricing_failed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_enterprise_created",
        "llm_usage_ledger",
        ["enterprise_id", "created_at"],
    )
    op.create_index(
        "ix_ledger_user_created", "llm_usage_ledger", ["user_id", "created_at"]
    )
    op.create_index("ix_ledger_conversation", "llm_usage_ledger", ["conversation_id"])

    # --- enterprise_budget --------------------------------------------------
    op.create_table(
        "enterprise_budget",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("limit_micros", sa.BigInteger(), nullable=False),
        sa.Column("spent_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("seat_count", sa.Integer(), nullable=True),
        sa.Column(
            "enforcement_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_enterprise_budget_enterprise_id"),
        "enterprise_budget",
        ["enterprise_id"],
    )
    # One row per enterprise per period. This is what makes the lazy
    # get-or-create safe under concurrency: the loser of the race takes an
    # IntegrityError and re-reads.
    op.create_index(
        "uq_enterprise_budget_period",
        "enterprise_budget",
        ["enterprise_id", "period_start"],
        unique=True,
    )

    # --- budget_block_event -------------------------------------------------
    op.create_table(
        "budget_block_event",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_internal_user", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("limit_kind", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("limit_micros", sa.BigInteger(), nullable=True),
        sa.Column("spent_micros", sa.BigInteger(), nullable=True),
        sa.Column("reserved_micros", sa.BigInteger(), nullable=True),
        sa.Column("estimate_micros", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_block_enterprise_created",
        "budget_block_event",
        ["enterprise_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_block_enterprise_created", table_name="budget_block_event")
    op.drop_table("budget_block_event")

    op.drop_index("uq_enterprise_budget_period", table_name="enterprise_budget")
    op.drop_index(
        op.f("ix_enterprise_budget_enterprise_id"), table_name="enterprise_budget"
    )
    op.drop_table("enterprise_budget")

    op.drop_index("ix_ledger_conversation", table_name="llm_usage_ledger")
    op.drop_index("ix_ledger_user_created", table_name="llm_usage_ledger")
    op.drop_index("ix_ledger_enterprise_created", table_name="llm_usage_ledger")
    op.drop_table("llm_usage_ledger")
