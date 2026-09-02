"""split admin access into viewer and manager

Two levels, because they carry very different risk.

Reading cross-enterprise cost is low-risk for ClickPost staff - they can already
see customer data through the Django admin. Changing a cap, flipping enforcement
or granting admin access is not, and should stay with a small named group.

  viewer   - read the budget dashboard
  manager  - all of the above, plus PATCH and managing the allowlist

Existing rows become managers: they were granted under a model where the only
level was full access, and silently demoting them would lock people out.

Revision ID: a91c4f7e2db6
Revises: f7a3c81d5e29
Create Date: 2026-09-01 12:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a91c4f7e2db6"
down_revision: Union[str, Sequence[str], None] = "f7a3c81d5e29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_admin",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="manager"),
    )
    op.create_check_constraint(
        "ck_agent_admin_role", "agent_admin", "role IN ('viewer', 'manager')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_admin_role", "agent_admin", type_="check")
    op.drop_column("agent_admin", "role")
