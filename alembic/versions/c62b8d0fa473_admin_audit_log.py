"""record who changed what on the admin surface

Application logs already carry a line per write, but they answer the wrong
question: the actor is recorded as the signed `user_id`, which is a single
shared Django account for all ClickPost internal staff. Every entry reads
"changed by 14". They are also not queryable from the dashboard and they rotate.

This stores the actor as the pair (user_id, internal_user_id) - the second being
the individual - plus the before and after state, so "who raised this
enterprise's cap, when, and from what" has an answer months later.

Revision ID: c62b8d0fa473
Revises: a91c4f7e2db6
Create Date: 2026-09-01 13:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "c62b8d0fa473"
down_revision: Union[str, Sequence[str], None] = "a91c4f7e2db6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        # Both halves of the actor. user_id alone is the shared account.
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("actor_internal_user_id", sa.Text(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("target_type", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        # Full before/after, so a change is legible without replaying history.
        sa.Column("before", JSONB(), nullable=True),
        sa.Column("after", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_created", "admin_audit_log", ["created_at"])
    op.create_index(
        "ix_audit_target", "admin_audit_log", ["target_type", "target_id", "created_at"]
    )
    op.create_index(
        "ix_audit_actor",
        "admin_audit_log",
        ["actor_user_id", "actor_internal_user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_actor", table_name="admin_audit_log")
    op.drop_index("ix_audit_target", table_name="admin_audit_log")
    op.drop_index("ix_audit_created", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
