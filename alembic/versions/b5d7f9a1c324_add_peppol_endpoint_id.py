"""add peppol_endpoint_id to customers and company

Revision ID: b5d7f9a1c324
Revises: a4c6e8f0b213
Create Date: 2026-09-29

Doteraz appka do Peppol EndpointID posielala vždy IČO, bez ohľadu na
zvolenú schému. Pri schéme "9950" (SK:VAT) je to nesprávne - EndpointID
tam má byť IČ DPH, nie IČO. Táto migrácia pridáva samostatný stĺpec
peppol_endpoint_id (samotná hodnota adresy), oddelený od
peppol_scheme_id (kód schémy) - viď models.py.

Existujúci peppol_scheme_id sa NEMAZE ani neprepočítava - appka od
tejto migrácie prestane EndpointID generovať, kým si používateľ
hodnotu peppol_endpoint_id sám nedoplní (radšej nič, než ďalej posielať
nesprávnu hodnotu).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b5d7f9a1c324'
down_revision: Union[str, Sequence[str], None] = 'a4c6e8f0b213'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # Kontrola existujúcich stĺpcov (nie len "pridaj") - táto migrácia
    # v jednej z predošlých verzií najprv nesprávne skúšala tabuľku
    # "companies" (namiesto "company"), čo zlyhalo AŽ PO úspešnom
    # pridaní stĺpca do customers. Bez tejto kontroly by opätovné
    # spustenie na takto "napoly" upgradnutej databáze spadlo na
    # "duplicate column name: peppol_endpoint_id".

    conn = op.get_bind()
    inspector = sa.inspect(conn)

    existing_customers_columns = {
        col["name"]
        for col in inspector.get_columns("customers")
    }

    if "peppol_endpoint_id" not in existing_customers_columns:

        op.add_column(
            'customers',
            sa.Column('peppol_endpoint_id', sa.String(), nullable=True)
        )

    existing_company_columns = {
        col["name"]
        for col in inspector.get_columns("company")
    }

    if "peppol_endpoint_id" not in existing_company_columns:

        op.add_column(
            'company',
            sa.Column('peppol_endpoint_id', sa.String(), nullable=True)
        )


def downgrade() -> None:

    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "peppol_endpoint_id" in {c["name"] for c in inspector.get_columns("company")}:
        op.drop_column('company', 'peppol_endpoint_id')

    if "peppol_endpoint_id" in {c["name"] for c in inspector.get_columns("customers")}:
        op.drop_column('customers', 'peppol_endpoint_id')
