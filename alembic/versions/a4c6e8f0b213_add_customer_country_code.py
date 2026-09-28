"""add customer country_code

Revision ID: a4c6e8f0b213
Revises: f2a4b6c8d0e1
Create Date: 2026-09-27

Pridáva Customer.country_code (ISO 3166-1 alpha-2, napr. "SK", "CZ").

Predtým appka pri generovaní Peppol XML (peppol_xml.py) natvrdo
zapisovala krajinu odberateľa ako "SK" pre KAŽDÉHO zákazníka
(DEFAULT_COUNTRY_CODE) - pre zahraničných odberateľov to bolo
nesprávne a navyše to porušovalo Peppol BIS 3.0 pravidlo BR-11
(krajina odberateľa musí zodpovedať skutočnosti, nie byť len
vyplnená hocičím).

Existujúci zákazníci dostanú pri tejto migrácii "SK" ako rozumný
predvolený štart (väčšina zákazníkov appky sú slovenskí odberatelia)
- kto má zahraničného zákazníka, si to po migrácii vie v appke opraviť.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a4c6e8f0b213'
down_revision: Union[str, Sequence[str], None] = 'f2a4b6c8d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.add_column(
        'customers',
        sa.Column(
            'country_code',
            sa.String(),
            nullable=True,
            server_default='SK'
        )
    )


def downgrade() -> None:

    op.drop_column('customers', 'country_code')
