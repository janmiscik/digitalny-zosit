"""ochrany v databáze: vystavené faktúry a ich položky (triggery)

Revision ID: b2c3d4e5f607
Revises: a1b2c3d4e5f6
Create Date: 2026-10-24
"""

from typing import Sequence, Union

from alembic import op

import db_guards

revision: str = "b2c3d4e5f607"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for statement in db_guards.install_statements({"invoices", "invoice_items"}):
        op.execute(statement)


def downgrade() -> None:
    for name in ("invoices_no_delete_issued", "invoices_no_revert_to_draft",
                 "invoice_items_no_update_issued", "invoice_items_no_delete_issued"):
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
