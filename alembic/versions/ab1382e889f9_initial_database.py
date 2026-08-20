"""initial database

Revision ID: ab1382e889f9
Revises: 
Create Date: 2026-08-16 20:02:07.126333

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab1382e889f9'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # Táto migrácia bola pôvodne prázdna - tabuľky sa vytvárali len cez
    # Base.metadata.create_all() pri štarte appky (main.py), nie cez alembic.
    # Aby `alembic upgrade head` fungoval aj na úplne čerstvej databáze
    # (kde ešte appka nikdy nebežala), vytvoríme tu customers/jobs manuálne -
    # ale len ak ešte neexistujú (existujúce inštalácie ich už majú).

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()


    if "customers" not in existing_tables:

        op.create_table(
            "customers",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("phone", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("address", sa.String(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
        )


    if "jobs" not in existing_tables:

        op.create_table(
            "jobs",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="Nová"),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_table("jobs")
    op.drop_table("customers")
