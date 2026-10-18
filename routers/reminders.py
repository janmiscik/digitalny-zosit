"""
Pripomienky platieb: prehľad neuhradených faktúr (po splatnosti a
čoskoro splatné), hotový text pripomienky a evidencia, že sa
zákazníkovi platba pripomenula.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session, joinedload

from audit_log import log_action
from auth import require_login_page
from csrf import verify_csrf
from database import get_db
from models import Invoice
from reminder_utils import (
    build_reminder_message,
    is_remindable,
    mailto_url,
    reminder_rows,
)
from routers.company import get_or_create_company
from templates_config import templates


router = APIRouter(dependencies=[Depends(verify_csrf)])


def _get_remindable_invoice(db: Session, invoice_id: int) -> Invoice:

    invoice = (
        db
        .query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.customer))
        .filter(Invoice.id == invoice_id)
        .first()
    )

    if invoice is None:

        raise HTTPException(status_code=404, detail="Faktúra neexistuje")

    if not is_remindable(invoice):

        raise HTTPException(
            status_code=409,
            detail="Pripomienku platby je možné pripraviť len k odoslanej, neuhradenej faktúre."
        )

    return invoice


@router.get("/pripomienky")
def reminders_page(

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    today = date.today()

    invoices = (
        db
        .query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.customer))
        .filter(
            Invoice.status == "Odoslaná",
            Invoice.is_proforma.is_(False),
            Invoice.is_credit_note.is_(False)
        )
        .all()
    )

    rows = reminder_rows(invoices, today)

    return templates.TemplateResponse(
        request=request,
        name="reminders.html",
        context={
            "overdue": rows["overdue"],
            "upcoming": rows["upcoming"],
            "overdue_total": rows["overdue_total"],
            "today": today,
        }
    )


@router.get("/invoices/{invoice_id}/reminder")
def reminder_detail(

    invoice_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = _get_remindable_invoice(db, invoice_id)

    company = get_or_create_company(db)

    message = build_reminder_message(invoice, company, date.today())

    customer_email = invoice.customer.email if invoice.customer else None

    return templates.TemplateResponse(
        request=request,
        name="reminder_detail.html",
        context={
            "invoice": invoice,
            "message": message,
            "customer_email": customer_email,
            "mailto": mailto_url(customer_email, message["subject"], message["body"]),
        }
    )


@router.post("/invoices/{invoice_id}/reminder/mark")
def mark_reminded(

    invoice_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):
    """Zapíše, že zákazníkovi bola platba pripomenutá (dnes)."""

    invoice = _get_remindable_invoice(db, invoice_id)

    invoice.reminder_count = (invoice.reminder_count or 0) + 1
    invoice.last_reminded_at = date.today()

    log_action(
        db,
        "invoice.reminder_sent",
        entity_type="invoice",
        entity_id=invoice.id,
        detail=(
            f"Pripomienka platby faktúry {invoice.invoice_number} "
            f"({invoice.reminder_count}.)"
        )
    )

    db.commit()

    return RedirectResponse(url="/pripomienky?oznacene=1", status_code=303)
