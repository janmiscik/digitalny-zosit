from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session, joinedload

from auth import require_login_page
from database import get_db
from delivery_note_pdf import generate_delivery_note_pdf
from form_utils import parse_items_from_form, parse_optional_date, parse_required_date
from invoice_utils import (
    allowed_next_quote_statuses,
    calculate_invoice_totals,
    is_quote_expired,
    is_valid_quote_status_transition,
    next_invoice_number,
    next_proforma_number,
    next_quote_number,
    validate_invoice_vat,
)
from models import Customer, Invoice, InvoiceItem, Job, Quote, QuoteItem
from quote_pdf import generate_quote_pdf
from routers.company import get_or_create_company
from schemas import InvoiceStatus, QuoteItemCreate, QuoteStatus
from templates_config import templates


router = APIRouter()


def require_draft_quote(quote) -> None:
    """
    Úprava a zmazanie ponuky sú povolené len v stave Návrh - rovnaký
    princíp ako pri faktúrach (viď require_draft_invoice)."""

    if quote.status != QuoteStatus.DRAFT.value:

        raise HTTPException(
            status_code=409,
            detail=(
                "Túto ponuku už nie je možné upraviť ani zmazať, "
                "pretože nie je v stave Návrh."
            )
        )


def validate_quote_dates(issue_date: date, valid_until: date | None) -> None:

    if valid_until is not None and valid_until < issue_date:

        raise HTTPException(
            status_code=422,
            detail=(
                "Dátum platnosti ponuky nemôže byť skôr než dátum "
                f"vystavenia ({valid_until.isoformat()} < {issue_date.isoformat()})"
            )
        )


# =========================================
# ZOZNAM CENOVÝCH PONÚK (STRÁNKA)
# =========================================

@router.get("/ponuky")
def quotes_list_page(

    request: Request,

    status: str | None = None,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    query = db.query(Quote).options(
        joinedload(Quote.customer),
        joinedload(Quote.items)
    )

    today = date.today()

    if status is not None and status != QuoteStatus.EXPIRED.value:

        query = query.filter(Quote.status == status)


    quotes = query.order_by(
        Quote.issue_date.desc(),
        Quote.id.desc()
    ).all()


    if status == QuoteStatus.EXPIRED.value:

        quotes = [
            quote
            for quote in quotes
            if is_quote_expired(quote, today)
        ]


    quote_totals = {

        quote.id: calculate_invoice_totals(quote.items)["total_gross"]

        for quote in quotes

    }

    expired_quote_ids = {
        quote.id
        for quote in quotes
        if is_quote_expired(quote, today)
    }


    return templates.TemplateResponse(

        request=request,

        name="quotes_list.html",

        context={

            "quotes": quotes,

            "quote_totals": quote_totals,

            "expired_quote_ids": expired_quote_ids,

            "statuses": list(QuoteStatus),

            "selected_status": status,

            "today": today

        }

    )


# =========================================
# NOVÁ PONUKA (FORMULÁR)
# =========================================

@router.get("/customers/{customer_id}/quotes/new")
def new_quote_form(

    customer_id: int,

    request: Request,

    job_id: int | None = None,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if customer is None:

        raise HTTPException(
            status_code=404,
            detail="Zákazník neexistuje"
        )


    job = None

    if job_id is not None:

        job = (
            db
            .query(Job)
            .filter(Job.id == job_id, Job.customer_id == customer_id)
            .first()
        )

        if job is None:

            raise HTTPException(
                status_code=404,
                detail="Zákazka neexistuje alebo nepatrí tomuto zákazníkovi"
            )


    return templates.TemplateResponse(

        request=request,

        name="quote_form.html",

        context={

            "customer": customer,

            "job": job,

            "today": date.today(),

            "company_is_vat_payer": get_or_create_company(db).is_vat_payer

        }

    )


@router.post("/customers/{customer_id}/quotes")
async def create_quote(

    customer_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if customer is None:

        raise HTTPException(
            status_code=404,
            detail="Zákazník neexistuje"
        )


    form = await request.form()

    job_id_raw = form.get("job_id", "").strip()
    job_id = int(job_id_raw) if job_id_raw else None

    if job_id is not None:

        job = (
            db
            .query(Job)
            .filter(Job.id == job_id, Job.customer_id == customer_id)
            .first()
        )

        if job is None:

            raise HTTPException(
                status_code=404,
                detail="Zákazka neexistuje alebo nepatrí tomuto zákazníkovi"
            )


    items_data = parse_items_from_form(
        form,
        QuoteItemCreate,
        "Ponuka musí obsahovať aspoň jednu položku"
    )

    issue_date = parse_required_date(
        form.get("issue_date", ""), "Dátum vystavenia"
    )

    valid_until = parse_optional_date(
        form.get("valid_until", "")
    )

    validate_quote_dates(issue_date, valid_until)

    note = form.get("note", "").strip() or None


    quote_number = next_quote_number(db, issue_date.year)


    new_quote = Quote(
        quote_number=quote_number,
        customer_id=customer_id,
        job_id=job_id,
        status=QuoteStatus.DRAFT.value,
        issue_date=issue_date,
        valid_until=valid_until,
        note=note
    )

    for item in items_data:

        new_quote.items.append(
            QuoteItem(
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate
            )
        )

    db.add(new_quote)
    db.commit()
    db.refresh(new_quote)


    return RedirectResponse(
        url=f"/quotes/{new_quote.id}",
        status_code=303
    )


# =========================================
# DETAIL PONUKY
# =========================================

@router.get("/quotes/{quote_id}")
def quote_detail(

    quote_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = (

        db
        .query(Quote)
        .options(joinedload(Quote.customer), joinedload(Quote.items))
        .filter(Quote.id == quote_id)
        .first()

    )

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )


    totals = calculate_invoice_totals(quote.items)

    next_statuses = allowed_next_quote_statuses(quote.status)

    selectable_statuses = [quote.status] + [
        status.value
        for status in QuoteStatus
        if status.value in next_statuses
        and status != QuoteStatus.CONVERTED
    ]

    can_change_status = len(
        [s for s in next_statuses if s != QuoteStatus.CONVERTED.value]
    ) > 0

    can_convert_to_invoice = quote.status == QuoteStatus.ACCEPTED.value


    return templates.TemplateResponse(

        request=request,

        name="quote_detail.html",

        context={

            "quote": quote,

            "totals": totals,

            "statuses": selectable_statuses,

            "can_change_status": can_change_status,

            "can_convert_to_invoice": can_convert_to_invoice,

            "is_expired": is_quote_expired(quote)

        }

    )


# =========================================
# ÚPRAVA PONUKY
# =========================================

@router.get("/quotes/{quote_id}/edit")
def edit_quote_form(

    quote_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = (
        db
        .query(Quote)
        .options(joinedload(Quote.customer), joinedload(Quote.items))
        .filter(Quote.id == quote_id)
        .first()
    )

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )

    require_draft_quote(quote)


    return templates.TemplateResponse(

        request=request,

        name="quote_form.html",

        context={

            "customer": quote.customer,

            "job": quote.job,

            "today": date.today(),

            "quote": quote,

            "company_is_vat_payer": get_or_create_company(db).is_vat_payer

        }

    )


@router.post("/quotes/{quote_id}/edit")
async def update_quote(

    quote_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = db.query(Quote).filter(Quote.id == quote_id).first()

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )

    require_draft_quote(quote)


    form = await request.form()

    items_data = parse_items_from_form(
        form,
        QuoteItemCreate,
        "Ponuka musí obsahovať aspoň jednu položku"
    )

    issue_date = parse_required_date(
        form.get("issue_date", ""), "Dátum vystavenia"
    )

    valid_until = parse_optional_date(
        form.get("valid_until", "")
    )

    validate_quote_dates(issue_date, valid_until)

    note = form.get("note", "").strip() or None


    quote.issue_date = issue_date
    quote.valid_until = valid_until
    quote.note = note

    quote.items.clear()

    for item in items_data:

        quote.items.append(
            QuoteItem(
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate
            )
        )

    db.commit()


    return RedirectResponse(
        url=f"/quotes/{quote_id}",
        status_code=303
    )


@router.post("/quotes/{quote_id}/delete")
def delete_quote(

    quote_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = db.query(Quote).filter(Quote.id == quote_id).first()

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )

    require_draft_quote(quote)

    db.delete(quote)
    db.commit()


    return RedirectResponse(
        url="/ponuky",
        status_code=303
    )


# =========================================
# ZMENA STAVU PONUKY
# =========================================

@router.post("/quotes/{quote_id}/status")
def update_quote_status(

    quote_id: int,

    status: str = Form(...),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = db.query(Quote).filter(Quote.id == quote_id).first()

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )


    try:

        new_status = QuoteStatus(status).value

    except ValueError:

        raise HTTPException(
            status_code=422,
            detail=f"Neplatný stav ponuky: {status}"
        )


    # "Prevedená na faktúru" sa nastavuje VÝHRADNE cez
    # /quotes/{id}/convert-to-invoice (atomicky spolu s vytvorením
    # faktúry) - nikdy priamo cez tento všeobecný endpoint, inak by
    # ponuka tvrdila, že bola prevedená, aj keď žiadna faktúra nevznikla.
    if new_status == QuoteStatus.CONVERTED.value:

        raise HTTPException(
            status_code=422,
            detail=(
                "Stav 'Prevedená na faktúru' sa nastavuje len "
                "automaticky pri vygenerovaní faktúry z ponuky."
            )
        )

    if new_status == QuoteStatus.EXPIRED.value:

        raise HTTPException(
            status_code=422,
            detail=(
                "Stav 'Po platnosti' sa počíta automaticky podľa dátumu "
                "platnosti - nedá sa nastaviť ručne."
            )
        )


    if not is_valid_quote_status_transition(quote.status, new_status):

        raise HTTPException(
            status_code=409,
            detail=(
                f"Nepovolený prechod stavu z '{quote.status}' "
                f"na '{new_status}'."
            )
        )


    quote.status = new_status

    db.commit()


    return RedirectResponse(
        url=f"/quotes/{quote_id}",
        status_code=303
    )


# =========================================
# PDF PONUKY
# =========================================

@router.get("/quotes/{quote_id}/pdf")
def quote_pdf(

    quote_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = (
        db
        .query(Quote)
        .options(joinedload(Quote.items), joinedload(Quote.customer))
        .filter(Quote.id == quote_id)
        .first()
    )

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )

    company = get_or_create_company(db)

    pdf_bytes = generate_quote_pdf(quote, company)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="ponuka-{quote.quote_number}.pdf"'
        }
    )


# =========================================
# DODACÍ LIST (z ponuky)
# =========================================

@router.get("/quotes/{quote_id}/delivery-note")
def quote_delivery_note(

    quote_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = (
        db
        .query(Quote)
        .options(joinedload(Quote.items), joinedload(Quote.customer))
        .filter(Quote.id == quote_id)
        .first()
    )

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )

    company = get_or_create_company(db)

    pdf_bytes = generate_delivery_note_pdf(
        customer=quote.customer,
        items=quote.items,
        document_number=quote.quote_number,
        document_label=f"Ponuka č. {quote.quote_number}",
        issue_date=quote.issue_date,
        company=company
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="dodaci-list-{quote.quote_number}.pdf"'
        }
    )


# =========================================
# JEDNÝM KLIKOM: PONUKA -> FAKTÚRA (ostrá alebo zálohová)
# =========================================

@router.post("/quotes/{quote_id}/convert-to-invoice")
def convert_quote_to_invoice(

    quote_id: int,

    is_proforma: str = Form(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    quote = (
        db
        .query(Quote)
        .options(joinedload(Quote.items), joinedload(Quote.customer))
        .filter(Quote.id == quote_id)
        .first()
    )

    if quote is None:

        raise HTTPException(
            status_code=404,
            detail="Ponuka neexistuje"
        )

    if quote.status != QuoteStatus.ACCEPTED.value:

        raise HTTPException(
            status_code=409,
            detail=(
                "Faktúru je možné vygenerovať len z akceptovanej ponuky "
                f"(aktuálny stav: '{quote.status}')."
            )
        )


    make_proforma = is_proforma == "on"

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    if make_proforma:

        invoice_number = next_proforma_number(db, issue_date.year)

    else:

        invoice_number = next_invoice_number(db, issue_date.year)

    status_value = InvoiceStatus.DRAFT.value

    company = get_or_create_company(db)

    try:

        validate_invoice_vat(
            company_is_vat_payer=company.is_vat_payer,
            customer_ic_dph=quote.customer.ic_dph,
            reverse_charge=False,
            item_vat_rates=[item.vat_rate for item in quote.items]
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=422,
            detail=(
                f"{exc} Uprav DPH sadzby na položkách faktúry po jej "
                "vygenerovaní (kým je v stave Návrh), alebo najprv "
                "uprav nastavenia DPH režimu firmy."
            )
        )


    new_invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=quote.customer_id,
        job_id=quote.job_id,
        status=status_value,
        issue_date=issue_date,
        due_date=due_date,
        is_proforma=make_proforma,
        quote_id=quote.id,
        note=f"Vygenerované z ponuky č. {quote.quote_number}"
    )

    for item in quote.items:

        new_invoice.items.append(
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate
            )
        )

    quote.status = QuoteStatus.CONVERTED.value

    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)


    return RedirectResponse(
        url=f"/invoices/{new_invoice.id}",
        status_code=303
    )
