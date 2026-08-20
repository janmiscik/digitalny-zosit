"""add invoicing (company, invoices, invoice items) and customer IČO/DIČ

Revision ID: 83ec1c5957f2
Revises: ab1382e889f9
Create Date: 2026-08-19 07:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '83ec1c5957f2'
down_revision: Union[str, Sequence[str], None] = 'ab1382e889f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    existing_customer_columns = {
        col["name"]
        for col in inspector.get_columns("customers")
    }


    # =====================================
    # ZÁKAZNÍCI - fakturačné údaje
    # =====================================

    with op.batch_alter_table("customers") as batch_op:

        if "ico" not in existing_customer_columns:

            batch_op.add_column(
                sa.Column("ico", sa.String(), nullable=True)
            )

        if "dic" not in existing_customer_columns:

            batch_op.add_column(
                sa.Column("dic", sa.String(), nullable=True)
            )

        if "ic_dph" not in existing_customer_columns:

            batch_op.add_column(
                sa.Column("ic_dph", sa.String(), nullable=True)
            )


    # =====================================
    # FIRMA (predávajúci na faktúrach)
    # =====================================

    if "company" not in existing_tables:

        op.create_table(
            "company",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("ico", sa.String(), nullable=True),
            sa.Column("dic", sa.String(), nullable=True),
            sa.Column("ic_dph", sa.String(), nullable=True),
            sa.Column("address", sa.String(), nullable=True),
            sa.Column("city", sa.String(), nullable=True),
            sa.Column("zip_code", sa.String(), nullable=True),
            sa.Column("iban", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("phone", sa.String(), nullable=True),
        )


    # =====================================
    # FAKTÚRY
    # =====================================

    if "invoices" not in existing_tables:

        op.create_table(
            "invoices",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("invoice_number", sa.String(), nullable=False, unique=True, index=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
            sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="Návrh"),
            sa.Column("issue_date", sa.Date(), nullable=False),
            sa.Column("due_date", sa.Date(), nullable=False),
            sa.Column("delivery_date", sa.Date(), nullable=True),
            sa.Column("variable_symbol", sa.String(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
        )


    if "invoice_items" not in existing_tables:

        op.create_table(
            "invoice_items",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
            sa.Column("unit", sa.String(), nullable=False, server_default="ks"),
            sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
            sa.Column("vat_rate", sa.Integer(), nullable=False, server_default="23"),
        )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_table("invoice_items")
    op.drop_table("invoices")
    op.drop_table("company")

    with op.batch_alter_table("customers") as batch_op:

        batch_op.drop_column("ic_dph")
        batch_op.drop_column("dic")
        batch_op.drop_column("ico")
