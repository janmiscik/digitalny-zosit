"""add logo and signature filenames to company

Revision ID: a2b4c6d8e0f1
Revises: f1a9c3d5e7b2
Create Date: 2026-08-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b4c6d8e0f1'
down_revision: Union[str, Sequence[str], None] = 'f1a9c3d5e7b2'
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

        if "logo_filename" not in existing_company_columns:

            batch_op.add_column(
                sa.Column("logo_filename", sa.String(), nullable=True)
            )

        if "signature_filename" not in existing_company_columns:

            batch_op.add_column(
                sa.Column("signature_filename", sa.String(), nullable=True)
            )


def downgrade() -> None:
    """Downgrade schema."""

    with op.batch_alter_table("company") as batch_op:

        batch_op.drop_column("signature_filename")
        batch_op.drop_column("logo_filename")
