"""add invoice credit note fields (is_credit_note, original_invoice_id)

Revision ID: c1d3e5f7a980
Revises: b9c1d3e5f768
Create Date: 2026-09-04

Dobropis (opravný daňový doklad) je Invoice s is_credit_note=True,
odkazujúca cez original_invoice_id na pôvodnú faktúru, ktorú opravuje/
storuje. Vlastné číslovanie (prefix DP) - viď invoice_utils.
next_credit_note_number.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c1d3e5f7a980'
down_revision: Union[str, Sequence[str], None] = 'b9c1d3e5f768'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.add_column(
        'invoices',
        sa.Column('is_credit_note', sa.Boolean(), nullable=False, server_default=sa.false())
    )

    with op.batch_alter_table('invoices') as batch_op:

        batch_op.add_column(
            sa.Column('original_invoice_id', sa.Integer(), nullable=True)
        )

        batch_op.create_foreign_key(
            'fk_invoices_original_invoice_id',
            'invoices',
            ['original_invoice_id'],
            ['id']
        )


def downgrade() -> None:

    with op.batch_alter_table('invoices') as batch_op:

        batch_op.drop_constraint('fk_invoices_original_invoice_id', type_='foreignkey')
        batch_op.drop_column('original_invoice_id')

    op.drop_column('invoices', 'is_credit_note')
