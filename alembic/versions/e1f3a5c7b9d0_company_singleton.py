"""enforce company table as a singleton (id=1 + check constraint)

Revision ID: e1f3a5c7b9d0
Revises: d2e4f6a8b091
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f3a5c7b9d0'
down_revision: Union[str, Sequence[str], None] = 'd2e4f6a8b091'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    conn = op.get_bind()

    # Appka sama nikdy nevytvorí viac než jeden riadok (viď
    # get_or_create_company), takže v praxi tu je najviac jeden riadok
    # a takmer vždy s id=1 (prvý a jediný INSERT). Táto migrácia to ale
    # nepredpokladá naslepo - normalizuje existujúce dáta, aby nová
    # CHECK(id = 1) constraint nezlyhala na existujúcej inštalácii:
    #   - ak existuje viac riadkov (nemalo by nastať), ponechá prvý
    #     (podľa id) a ostatné zmaže,
    #   - ak jediný existujúci riadok nemá id=1, prečísluje ho na 1.

    rows = conn.execute(
        sa.text("SELECT id FROM company ORDER BY id")
    ).fetchall()

    if len(rows) > 1:

        extra_ids = [row[0] for row in rows[1:]]

        conn.execute(
            sa.text(
                "DELETE FROM company WHERE id IN :ids"
            ).bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": extra_ids}
        )

        rows = rows[:1]

    if len(rows) == 1 and rows[0][0] != 1:

        conn.execute(
            sa.text("UPDATE company SET id = 1 WHERE id = :old_id"),
            {"old_id": rows[0][0]}
        )

    with op.batch_alter_table("company") as batch_op:

        batch_op.create_check_constraint(
            "company_singleton_id",
            "id = 1"
        )


def downgrade() -> None:
    """Downgrade schema."""

    with op.batch_alter_table("company") as batch_op:

        batch_op.drop_constraint(
            "company_singleton_id",
            type_="check"
        )
