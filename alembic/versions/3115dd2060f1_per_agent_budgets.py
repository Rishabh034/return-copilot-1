"""per-agent rate-limit windows and spend caps

Adds agent_slug to user_token and enterprise_budget, and widens each table's
uniqueness to include it.

Different agents live behind different UIs with independent traffic. Sharing
one 24h token window or one monthly spend cap across all of them means one
agent's usage can throttle a completely different product for the same user
or enterprise. Splitting the row per agent, the same way agent_slug already
splits llm_usage_ledger and budget_block_event (7c1e9b04a2d5), fixes that.

Backfilled to 'allocation' because every row that exists when this lands was
served by the allocation agent — behaviour for that agent is unchanged, a new
agent simply gets its own rows from its first request.

Revision ID: 3115dd2060f1
Revises: 7c1e9b04a2d5
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "3115dd2060f1"
down_revision: Union[str, Sequence[str], None] = "7c1e9b04a2d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_token",
        sa.Column(
            "agent_slug",
            sa.String(length=64),
            server_default="allocation",
            nullable=False,
        ),
    )
    op.create_index("ix_user_token_agent_slug", "user_token", ["agent_slug"], unique=False)

    op.drop_index("uq_user_token_active_external", table_name="user_token")
    op.drop_index("uq_user_token_active_internal", table_name="user_token")
    op.create_index(
        "uq_user_token_active_external",
        "user_token",
        ["user_id", "enterprise_id", "agent_slug"],
        unique=True,
        postgresql_where=sa.text("is_active AND NOT is_internal_user"),
    )
    op.create_index(
        "uq_user_token_active_internal",
        "user_token",
        ["user_id", "enterprise_id", "internal_user_id", "agent_slug"],
        unique=True,
        postgresql_where=sa.text("is_active AND is_internal_user"),
    )

    op.add_column(
        "enterprise_budget",
        sa.Column(
            "agent_slug",
            sa.String(length=64),
            server_default="allocation",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_enterprise_budget_agent_slug", "enterprise_budget", ["agent_slug"], unique=False
    )

    op.drop_index("uq_enterprise_budget_period", table_name="enterprise_budget")
    op.create_index(
        "uq_enterprise_budget_period",
        "enterprise_budget",
        ["enterprise_id", "period_start", "agent_slug"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_enterprise_budget_period", table_name="enterprise_budget")
    op.create_index(
        "uq_enterprise_budget_period",
        "enterprise_budget",
        ["enterprise_id", "period_start"],
        unique=True,
    )
    op.drop_index("ix_enterprise_budget_agent_slug", table_name="enterprise_budget")
    op.drop_column("enterprise_budget", "agent_slug")

    op.drop_index("uq_user_token_active_internal", table_name="user_token")
    op.drop_index("uq_user_token_active_external", table_name="user_token")
    op.create_index(
        "uq_user_token_active_external",
        "user_token",
        ["user_id", "enterprise_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND NOT is_internal_user"),
    )
    op.create_index(
        "uq_user_token_active_internal",
        "user_token",
        ["user_id", "enterprise_id", "internal_user_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND is_internal_user"),
    )
    op.drop_index("ix_user_token_agent_slug", table_name="user_token")
    op.drop_column("user_token", "agent_slug")
