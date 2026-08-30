"""add customer peppol_scheme_id, clean up legacy 'Po splatnosti' status

Revision ID: c4d6e8f0a213
Revises: b3c5d7e9f102
Create Date: 2026-08-29

Táto migrácia rieši dve nezávislé veci naraz (obe malé, súvisiace s
opravou faktúr/Peppol v tejto fáze):

1. Pridáva Customer.peppol_scheme_id - identifikačnú schému ODBERATEĽA
   v Peppol sieti. Predtým appka pri generovaní Peppol XML omylom
   používala schému DODÁVATEĽA aj pre odberateľa (viď peppol_xml.py) -
   tento stĺpec je súčasť opravy tohto bugu.

2. Prevádza akékoľvek existujúce faktúry so stavom "Po splatnosti" na
   "Odoslaná" - "Po splatnosti" už nie je (a od tejto verzie appky nikdy
   nebude) hodnota, ktorá sa reálne ukladá do stĺpca `status`. Počíta sa
   vždy za behu z dátumu splatnosti (viď invoice_utils.is_invoice_overdue).
   "Odoslaná" je najbližší zmysluplný reálny stav pre faktúru, ktorá bola
   (podľa toho, že mala nastavené "Po splatnosti") už niekomu poslaná a
   ešte nebola uhradená ani stornovaná.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c4d6e8f0a213'
down_revision: Union[str, Sequence[str], None] = 'b3c5d7e9f102'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    op.add_column(
        'customers',
        sa.Column('peppol_scheme_id', sa.String(), nullable=True)
    )

    invoices_table = sa.table(
        'invoices',
        sa.column('status', sa.String())
    )

    op.execute(
        invoices_table
        .update()
        .where(invoices_table.c.status == 'Po splatnosti')
        .values(status='Odoslaná')
    )


def downgrade() -> None:

    # Dátová migrácia (legacy "Po splatnosti" -> "Odoslaná") je zámerne
    # nevratná - pôvodná hodnota by vôbec nemala byť súčasťou platných
    # dát, takže niet kam sa "vracať".

    op.drop_column('customers', 'peppol_scheme_id')
