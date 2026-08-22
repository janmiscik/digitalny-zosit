"""add company website/swift_bic and invoice payment_method

Revision ID: b3c5d7e9f102
Revises: a2b4c6d8e0f1
Create Date: 2026-08-22 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c5d7e9f102'
down_revision: Union[str, Sequence[str], None] = 'a2b4c6d8e0f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_company_columns = {
        col["name"]
        for col in inspector.get_columns("company")
    }

    with op.batch_alter_table("company") as batch_op:

        if "website" not in existing_company_columns:

            batch_op.add_column(
                sa.Column("website", sa.String(), nullable=True)
            )

        if "swift_bic" not in existing_company_columns:

            batch_op.add_column(
                sa.Column("swift_bic", sa.String(), nullable=True)
            )


    existing_invoice_columns = {
        col["name"]
        for col in inspector.get_columns("invoices")
    }

    with op.batch_alter_table("invoices") as batch_op:

        if "payment_method" not in existing_invoice_columns:

            batch_op.add_column(
                sa.Column(
                    "payment_method",
                    sa.String(),
                    nullable=False,
                    server_default="Prevodom"
                )
            )


def downgrade() -> None:
    """Downgrade schema."""

    with op.batch_alter_table("invoices") as batch_op:

        batch_op.drop_column("payment_method")

    with op.batch_alter_table("company") as batch_op:

        batch_op.drop_column("swift_bic")
        batch_op.drop_column("website")
