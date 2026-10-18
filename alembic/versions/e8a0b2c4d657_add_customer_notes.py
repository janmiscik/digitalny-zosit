"""add customer_notes table

Revision ID: e8a0b2c4d657
Revises: d7f9b1c3e546
Create Date: 2026-10-18

Poznámky v histórii zákazníka (záznamy s časom).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e8a0b2c4d657'
down_revision: Union[str, Sequence[str], None] = 'd7f9b1c3e546'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    if "customer_notes" in sa.inspect(op.get_bind()).get_table_names():
        return

    op.create_table(
        "customer_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_index("ix_customer_notes_id", "customer_notes", ["id"])
    op.create_index("ix_customer_notes_customer_id", "customer_notes", ["customer_id"])


def downgrade() -> None:

    op.drop_table("customer_notes")
