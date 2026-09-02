"""allow admin access by (user_id, internal_user_id)

ClickPost internal staff share a single Django user, so the signed `user_id`
claim identifies the *path* a request came through, not the person. The person
is `internal_user_id`, which the client sends in the request body.

That field is not signed, so it cannot stand alone as an authorization key. The
guard therefore requires both: the signed `user_id` must match the row AND the
supplied `internal_user_id` must match. A caller who is not already inside the
internal-staff path cannot reach the dashboard at all; the residual risk is one
internal employee presenting another's id, which is a far smaller blast radius
than an unsigned field on its own.

Closing that gap properly means adding `internal_user_id` to the claims in
clickpost's selector/views/ai_agent_token_view.py - a one-line change next to
`user_id` - after which the guard can read it from the verified token and the
header below becomes redundant.

Rows with a NULL internal_user_id keep the old behaviour: signed user_id alone,
which is the stronger check and the right one for a genuinely distinct user.

Revision ID: f7a3c81d5e29
Revises: d4e2b7a91c58
Create Date: 2026-09-01 11:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "f7a3c81d5e29"
down_revision: Union[str, Sequence[str], None] = "d4e2b7a91c58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_admin",
        sa.Column("id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_admin",
        sa.Column("internal_user_id", sa.Text(), nullable=True),
    )
    # Existing rows predate the column and are user_id-only grants.
    op.execute("UPDATE agent_admin SET id = gen_random_uuid() WHERE id IS NULL")
    op.alter_column("agent_admin", "id", nullable=False)

    op.drop_constraint("agent_admin_pkey", "agent_admin", type_="primary")
    op.create_primary_key("agent_admin_pkey", "agent_admin", ["id"])

    # One grant per (user_id, internal_user_id). COALESCE so the NULL case - a
    # plain user_id grant - is still covered by the constraint; NULLs are
    # otherwise distinct from each other in a unique index.
    op.create_index(
        "uq_agent_admin_identity",
        "agent_admin",
        [sa.text("user_id"), sa.text("COALESCE(internal_user_id, '')")],
        unique=True,
    )
    op.drop_index("ix_agent_admin_active", table_name="agent_admin")
    op.create_index(
        "ix_agent_admin_active",
        "agent_admin",
        ["user_id", "internal_user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_admin_active", table_name="agent_admin")
    op.drop_index("uq_agent_admin_identity", table_name="agent_admin")
    # Only user_id-only grants can survive a return to a user_id primary key.
    op.execute("DELETE FROM agent_admin WHERE internal_user_id IS NOT NULL")
    op.drop_constraint("agent_admin_pkey", "agent_admin", type_="primary")
    op.drop_column("agent_admin", "internal_user_id")
    op.drop_column("agent_admin", "id")
    op.create_primary_key("agent_admin_pkey", "agent_admin", ["user_id"])
    op.create_index(
        "ix_agent_admin_active",
        "agent_admin",
        ["user_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
