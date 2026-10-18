"""add invoice reminder_count and last_reminded_at

Revision ID: c6e8a0b2d435
Revises: b5d7f9a1c324
Create Date: 2026-10-18

Evidencia pripomienok platieb: koľkokrát a kedy naposledy sa
zákazníkovi pripomenula neuhradená faktúra. Existujúce faktúry dostanú
reminder_count = 0.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c6e8a0b2d435'
down_revision: Union[str, Sequence[str], None] = 'b5d7f9a1c324'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    inspector = sa.inspect(op.get_bind())

    existing = {col["name"] for col in inspector.get_columns("invoices")}

    with op.batch_alter_table("invoices") as batch_op:

        if "reminder_count" not in existing:

            batch_op.add_column(
                sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="0")
            )

        if "last_reminded_at" not in existing:

            batch_op.add_column(
                sa.Column("last_reminded_at", sa.Date(), nullable=True)
            )


def downgrade() -> None:

    with op.batch_alter_table("invoices") as batch_op:

        batch_op.drop_column("last_reminded_at")
        batch_op.drop_column("reminder_count")
