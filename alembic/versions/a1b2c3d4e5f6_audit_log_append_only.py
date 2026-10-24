"""audit_log append-only (triggery proti úprave a mazaniu)

Revision ID: a1b2c3d4e5f6
Revises: e8a0b2c4d657
Create Date: 2026-10-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "e8a0b2c4d657"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    op.execute(
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log je len na pridávanie'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log je len na pridávanie'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
