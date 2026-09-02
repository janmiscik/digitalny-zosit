"""add job_photos and job_costs tables

Revision ID: a8b0c2d4e657
Revises: f7a9b1c3d546
Create Date: 2026-09-01

Podpora pre fotodokumentáciu zákazky (pred/po) a evidenciu nákladov
(materiál, subdodávky) potrebnú na výpočet reálnej marže zo zákazky.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a8b0c2d4e657'
down_revision: Union[str, Sequence[str], None] = 'f7a9b1c3d546'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.create_table(
        'job_photos',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('photo_type', sa.String(), nullable=False, server_default='pred'),
        sa.Column('uploaded_at', sa.Date(), nullable=False),
    )

    op.create_table(
        'job_costs',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('amount', sa.Numeric(10, 2), nullable=False),
        sa.Column('cost_date', sa.Date(), nullable=False),
    )


def downgrade() -> None:

    op.drop_table('job_costs')
    op.drop_table('job_photos')
