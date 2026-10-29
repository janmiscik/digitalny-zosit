"""add invoice_payments table

Revision ID: c3d5e7f9a1b2
Revises: b2c3d4e5f607
Create Date: 2026-10-28

Čiastočné úhrady faktúr (zostatok = suma - súčet úhrad).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f607"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    if "invoice_payments" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "invoice_payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_index("ix_invoice_payments_id", "invoice_payments", ["id"])
    op.create_index("ix_invoice_payments_invoice_id", "invoice_payments", ["invoice_id"])


def downgrade() -> None:

    op.drop_table("invoice_payments")
