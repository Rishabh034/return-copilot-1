"""add agent_admin allowlist

Gates the internal budget dashboard. Seeding is deliberately manual: the
endpoint that adds admins itself requires being one, so the first row has to be
inserted by hand. That is a feature, not an oversight - a bootstrap path in
config would be a permanent authentication bypass.

    INSERT INTO agent_admin (user_id, note, granted_by, created_at)
    VALUES (14, 'subodh', 14, now());

Revision ID: d4e2b7a91c58
Revises: b3c9d1e7f204
Create Date: 2026-08-31 16:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e2b7a91c58"
down_revision: Union[str, Sequence[str], None] = "b3c9d1e7f204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_admin",
        # The signed user_id claim. Never internal_user_id, which is
        # client-supplied and therefore forgeable.
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("granted_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Soft revoke: the audit trail of who could see customer spend outlives
        # the access itself.
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_agent_admin_active",
        "agent_admin",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_admin_active", table_name="agent_admin")
    op.drop_table("agent_admin")
