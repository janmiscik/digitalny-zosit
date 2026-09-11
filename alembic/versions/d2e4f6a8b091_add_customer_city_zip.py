"""add customer city and zip_code fields

Revision ID: d2e4f6a8b091
Revises: c1d3e5f7a980
Create Date: 2026-09-07

Peppol/UBL vyžaduje štruktúrovanú adresu (samostatné prvky ulica/mesto/
PSČ), nie jeden textový reťazec. Company tieto polia už mala, Customer
zatiaľ nie - toto bol reálny nedostatok v peppol_xml.py (chýbajúce
mesto/PSČ v AccountingCustomerParty).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd2e4f6a8b091'
down_revision: Union[str, Sequence[str], None] = 'c1d3e5f7a980'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.add_column('customers', sa.Column('city', sa.String(), nullable=True))
    op.add_column('customers', sa.Column('zip_code', sa.String(), nullable=True))


def downgrade() -> None:

    op.drop_column('customers', 'zip_code')
    op.drop_column('customers', 'city')
