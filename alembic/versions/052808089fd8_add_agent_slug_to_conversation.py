"""add agent_slug to conversation

Records which agent owns a conversation. This is not routing — the URL decides
that — it is scoping and attribution: an agent lists only its own threads, and
spend can be broken down per agent.

Backfilled to 'allocation' via the server default, because every row that
existed when this column landed was an allocation conversation. The default
stays on the column so an insert that predates the application change cannot
fail.

Hand-edited. `--autogenerate` additionally proposed dropping five indexes on
admin_audit_log and agent_admin (ix_audit_actor, ix_audit_created,
ix_audit_target, ix_agent_admin_active, uq_agent_admin_identity). Those are
false positives from pre-existing model/DB drift: the indexes were created in
earlier migrations but never declared on the models, so autogenerate cannot see
them in the metadata. They are deliberately not dropped here, and the drift is
worth closing separately — every future autogenerate run will propose the same
destructive change.

Revision ID: 052808089fd8
Revises: e4f5a6b7c8d9
Create Date: 2026-09-18 08:42:24.262371
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "052808089fd8"
down_revision: Union[str, Sequence[str], None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation",
        sa.Column(
            "agent_slug",
            sa.String(length=64),
            server_default="allocation",
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_conversation_agent_slug"), "conversation", ["agent_slug"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_conversation_agent_slug"), table_name="conversation")
    op.drop_column("conversation", "agent_slug")
