"""add company.is_vat_payer and invoice.reverse_charge

Revision ID: d5e7f9a1b324
Revises: c4d6e8f0a213
Create Date: 2026-08-30

Pridáva podporu pre DPH režim firmy (platca/neplatca DPH) a prenesenie
daňovej povinnosti (tuzemské samozdanenie, §69 ods. 12 zákona o DPH)
na úrovni faktúry.

Existujúce firmy sa pri migrácii nastavia ako is_vat_payer=False
(bezpečnejší default - ak firma v skutočnosti platiteľom DPH je,
používateľ si to jednoducho zapne v nastaveniach). Existujúce faktúry
majú reverse_charge=False (žiadna zmena správania pre už vystavené
faktúry).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd5e7f9a1b324'
down_revision: Union[str, Sequence[str], None] = 'c4d6e8f0a213'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.add_column(
        'company',
        sa.Column(
            'is_vat_payer',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false()
        )
    )

    op.add_column(
        'invoices',
        sa.Column(
            'reverse_charge',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false()
        )
    )


def downgrade() -> None:

    op.drop_column('invoices', 'reverse_charge')
    op.drop_column('company', 'is_vat_payer')
