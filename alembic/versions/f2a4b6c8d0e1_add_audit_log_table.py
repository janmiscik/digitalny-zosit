"""add audit_log table

Revision ID: f2a4b6c8d0e1
Revises: e1f3a5c7b9d0
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a4b6c8d0e1'
down_revision: Union[str, Sequence[str], None] = 'e1f3a5c7b9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('entity_type', sa.String(), nullable=True),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_index(
        op.f('ix_audit_log_id'),
        'audit_log',
        ['id'],
        unique=False
    )

    op.create_index(
        op.f('ix_audit_log_created_at'),
        'audit_log',
        ['created_at'],
        unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(op.f('ix_audit_log_created_at'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_id'), table_name='audit_log')
    op.drop_table('audit_log')
