"""add status column to message

Revision ID: c1a2b3d4e5f6
Revises: 1affd55b088b
Create Date: 2026-06-27 19:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '1affd55b088b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'message',
        sa.Column(
            'status',
            sa.String(length=20),
            nullable=False,
            server_default='complete',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('message', 'status')
