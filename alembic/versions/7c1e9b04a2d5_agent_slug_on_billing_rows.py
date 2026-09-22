"""record which agent a billed turn and a refused turn belong to

Adds agent_slug to llm_usage_ledger and budget_block_event.

Denormalised rather than joined through `conversation`. Every ledger row does
join to a conversation today — verified against production: 558 ledger rows, 0
orphans — so this is queryability, not correctness. But cost-per-agent is the
first question anyone asks once a second agent exists, and a join that happens
to work is one schema change away from silently dropping rows from a spend
report. The same applies to block events: "which agent is getting throttled"
should not require a join to answer.

Backfilled to 'allocation' because every row that exists when this lands was
served by the allocation agent.

Revision ID: 7c1e9b04a2d5
Revises: 052808089fd8
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "7c1e9b04a2d5"
down_revision: Union[str, Sequence[str], None] = "052808089fd8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("llm_usage_ledger", "budget_block_event")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "agent_slug",
                sa.String(length=64),
                server_default="allocation",
                nullable=False,
            ),
        )
        op.create_index(f"ix_{table}_agent_slug", table, ["agent_slug"], unique=False)


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_agent_slug", table_name=table)
        op.drop_column(table, "agent_slug")
