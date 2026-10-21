"""add accounts.email (voliteľný e-mail na obnovu hesla)

Revision ID: 5c8e0a1b3d24
Revises: 4b7cf1298419
Create Date: 2026-10-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5c8e0a1b3d24"
down_revision: Union[str, Sequence[str], None] = "4b7cf1298419"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    columns = [column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")]

    if "email" not in columns:
        op.add_column("accounts", sa.Column("email", sa.String(), nullable=True))


def downgrade() -> None:

    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("email")
