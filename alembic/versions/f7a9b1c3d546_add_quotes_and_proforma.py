"""add quotes, quote_items and invoice proforma/quote_id fields

Revision ID: f7a9b1c3d546
Revises: e6f8a0b2c435
Create Date: 2026-08-30

Cenová ponuka je samostatný koncept od faktúry (vlastné číslovanie,
vlastný životný cyklus Návrh -> Odoslaná -> Akceptovaná/Zamietnutá ->
Prevedená na faktúru). Po akceptovaní sa dá jedným klikom vygenerovať
ostrá alebo zálohová (proforma) faktúra.

Zálohová faktúra nie je nový koncept - je to Invoice s is_proforma=True
a číslom z vlastného radu (prefix "ZF"), aby nenarúšala súvislosť
číselného radu ostrých faktúr.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f7a9b1c3d546'
down_revision: Union[str, Sequence[str], None] = 'e6f8a0b2c435'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.create_table(
        'quotes',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('quote_number', sa.String(), nullable=False, unique=True, index=True),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('job_id', sa.Integer(), sa.ForeignKey('jobs.id'), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='Návrh'),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('valid_until', sa.Date(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
    )

    op.create_table(
        'quote_items',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('quote_id', sa.Integer(), sa.ForeignKey('quotes.id'), nullable=False),
        sa.Column('description', sa.String(), nullable=False),
        sa.Column('quantity', sa.Numeric(10, 2), nullable=False, server_default='1'),
        sa.Column('unit', sa.String(), nullable=False, server_default='ks'),
        sa.Column('unit_price', sa.Numeric(10, 2), nullable=False),
        sa.Column('vat_rate', sa.Integer(), nullable=False, server_default='23'),
    )

    op.add_column(
        'invoices',
        sa.Column('is_proforma', sa.Boolean(), nullable=False, server_default=sa.false())
    )

    with op.batch_alter_table('invoices') as batch_op:

        batch_op.add_column(
            sa.Column('quote_id', sa.Integer(), nullable=True)
        )

        batch_op.create_foreign_key(
            'fk_invoices_quote_id',
            'quotes',
            ['quote_id'],
            ['id']
        )


def downgrade() -> None:

    with op.batch_alter_table('invoices') as batch_op:

        batch_op.drop_constraint('fk_invoices_quote_id', type_='foreignkey')
        batch_op.drop_column('quote_id')

    op.drop_column('invoices', 'is_proforma')
    op.drop_table('quote_items')
    op.drop_table('quotes')
