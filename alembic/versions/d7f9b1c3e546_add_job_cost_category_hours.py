"""add job_costs.category and job_costs.hours

Revision ID: d7f9b1c3e546
Revises: c6e8a0b2d435
Create Date: 2026-10-18

Druh nákladu (materiál / práca / iné) a odpracované hodiny pre výpočet
zisku zo zákazky. Existujúce náklady dostanú druh "ine".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd7f9b1c3e546'
down_revision: Union[str, Sequence[str], None] = 'c6e8a0b2d435'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    inspector = sa.inspect(op.get_bind())

    existing = {col["name"] for col in inspector.get_columns("job_costs")}

    with op.batch_alter_table("job_costs") as batch_op:

        if "category" not in existing:

            batch_op.add_column(
                sa.Column("category", sa.String(), nullable=False, server_default="ine")
            )

        if "hours" not in existing:

            batch_op.add_column(
                sa.Column("hours", sa.Numeric(8, 2), nullable=True)
            )


def downgrade() -> None:

    with op.batch_alter_table("job_costs") as batch_op:

        batch_op.drop_column("hours")
        batch_op.drop_column("category")
