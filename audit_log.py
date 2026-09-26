"""
Pomocná funkcia na zápis do audit logu (model AuditLog v models.py).

log_action() len PRIDÁ riadok do session (db.add) - ZÁMERNE necommituje
sám. Vďaka tomu je log záznam súčasťou TEJ ISTEJ DB transakcie ako
samotná zmena, ktorú zaznamenáva - ak commit zlyhá (napr. porušenie
nejakého iného obmedzenia), neuloží sa ani zmena, ani jej log záznam.
Výnimka je prihlásenie/odhlásenie (auth.py) - tam sa musí commitnúť
samostatne, keďže sa neviaže na žiadnu inú DB zmenu v tej istej
požiadavke.

Zoznam používaných hodnôt `action` (pre orientáciu, nie je to enum
vynucovaný databázou):

    auth.login_success       auth.login_failed        auth.logout
    invoice.create           invoice.edit              invoice.delete
    invoice.status_change    invoice.credit_note        invoice.duplicate
    quote.create              quote.edit                 quote.delete
    quote.status_change       quote.convert_to_invoice
    company.update             backup.restore
"""

from sqlalchemy.orm import Session

from models import AuditLog


def log_action(
    db: Session,
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    detail: str | None = None,
) -> None:

    db.add(
        AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail
        )
    )
