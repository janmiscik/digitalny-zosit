"""add accounts.is_active (vypnutie konta majiteľom servera)

Revision ID: 6d9f1b2c4e35
Revises: 5c8e0a1b3d24
Create Date: 2026-10-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6d9f1b2c4e35"
down_revision: Union[str, Sequence[str], None] = "5c8e0a1b3d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    columns = [column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")]

    if "is_active" not in columns:
        op.add_column("accounts", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:

    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("is_active")
