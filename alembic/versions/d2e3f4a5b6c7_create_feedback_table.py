"""create feedback table

Revision ID: d2e3f4a5b6c7
Revises: c1a2b3d4e5f6
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1a2b3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'feedback',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', UUID(as_uuid=True), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('enterprise_id', sa.Integer(), nullable=False),
        sa.Column('positive', sa.Boolean(), nullable=False),
        sa.Column('comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['conversation_id'], ['conversation.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_feedback_conversation_id'), 'feedback', ['conversation_id']
    )
    op.create_index(op.f('ix_feedback_user_id'), 'feedback', ['user_id'])
    op.create_index(
        op.f('ix_feedback_enterprise_id'), 'feedback', ['enterprise_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_feedback_enterprise_id'), table_name='feedback')
    op.drop_index(op.f('ix_feedback_user_id'), table_name='feedback')
    op.drop_index(op.f('ix_feedback_conversation_id'), table_name='feedback')
    op.drop_table('feedback')
