"""add user token quotas

Revision ID: d7220cee44d3
Revises: d2e3f4a5b6c7
Create Date: 2026-07-31 11:56:14.368842
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "d7220cee44d3"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_token",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("used_input_tokens", sa.Integer(), nullable=False),
        sa.Column("used_output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "last_message_token_usage",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("random_factor", sa.Float(), nullable=True),
        sa.Column(
            "extra_token",
            sa.Integer(),
            nullable=False,
            server_default="9000",
        ),
        sa.Column(
            "extra_token_count_request",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "extra_token_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_token_enterprise_id"),
        "user_token",
        ["enterprise_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_token_user_id"),
        "user_token",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "uq_user_token_active_user_enterprise",
        "user_token",
        ["user_id", "enterprise_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_token_active_user_enterprise",
        table_name="user_token",
    )
    op.drop_index(op.f("ix_user_token_user_id"), table_name="user_token")
    op.drop_index(op.f("ix_user_token_enterprise_id"), table_name="user_token")
    op.drop_table("user_token")
