"""add invoice constant_symbol and specific_symbol

Revision ID: e6f8a0b2c435
Revises: d5e7f9a1b324
Create Date: 2026-08-30

Pridáva konštantný symbol (KS) a špecifický symbol (ŠS) na faktúru -
voliteľné doplnkové platobné symboly popri variabilnom symbole, ktoré
appka vie zakódovať aj do PAY by square QR kódu.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e6f8a0b2c435'
down_revision: Union[str, Sequence[str], None] = 'd5e7f9a1b324'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.add_column(
        'invoices',
        sa.Column('constant_symbol', sa.String(), nullable=True)
    )

    op.add_column(
        'invoices',
        sa.Column('specific_symbol', sa.String(), nullable=True)
    )


def downgrade() -> None:

    op.drop_column('invoices', 'specific_symbol')
    op.drop_column('invoices', 'constant_symbol')
