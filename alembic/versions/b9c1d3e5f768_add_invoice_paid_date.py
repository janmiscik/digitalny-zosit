"""add invoice.paid_date

Revision ID: b9c1d3e5f768
Revises: a8b0c2d4e657
Create Date: 2026-09-02

Dátum SKUTOČNEJ úhrady faktúry - odlišný od dátumu vystavenia. Doteraz
appka vedela len "uhradená áno/nie" (status), nie KEDY presne platba
prišla. Dashboard tržby sa touto migráciou prepínajú z dátumu vystavenia
na dátum úhrady (presnejšie pre účtovníctvo).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b9c1d3e5f768'
down_revision: Union[str, Sequence[str], None] = 'a8b0c2d4e657'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.add_column(
        'invoices',
        sa.Column('paid_date', sa.Date(), nullable=True)
    )

    # Existujúce už uhradené faktúry nemajú skutočný dátum úhrady
    # zaznamenaný - ako rozumný fallback použijeme dátum vystavenia,
    # nech dashboard (ktorý bude filtrovať podľa paid_date) nestratí
    # historické tržby. Nie je to dokonalé, ale je to lepšie než NULL.
    invoices_table = sa.table(
        'invoices',
        sa.column('status', sa.String()),
        sa.column('paid_date', sa.Date()),
        sa.column('issue_date', sa.Date()),
    )

    op.execute(
        invoices_table
        .update()
        .where(invoices_table.c.status == 'Uhradená')
        .values(paid_date=invoices_table.c.issue_date)
    )


def downgrade() -> None:

    op.drop_column('invoices', 'paid_date')
