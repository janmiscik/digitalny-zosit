"""add peppol_scheme_id to company

Revision ID: f1a9c3d5e7b2
Revises: 83ec1c5957f2
Create Date: 2026-08-21 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a9c3d5e7b2'
down_revision: Union[str, Sequence[str], None] = '83ec1c5957f2'
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

    if "peppol_scheme_id" not in existing_company_columns:

        with op.batch_alter_table("company") as batch_op:

            batch_op.add_column(
                sa.Column("peppol_scheme_id", sa.String(), nullable=True)
            )


def downgrade() -> None:
    """Downgrade schema."""

    with op.batch_alter_table("company") as batch_op:

        batch_op.drop_column("peppol_scheme_id")
