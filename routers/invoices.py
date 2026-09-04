from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from auth import require_login_api, require_login_page
from database import get_db
from delivery_note_pdf import generate_delivery_note_pdf
from form_utils import parse_items_from_form, parse_optional_date, parse_required_date
from invoice_pdf import generate_invoice_pdf
from invoice_utils import (
    allowed_next_invoice_statuses,
    calculate_invoice_totals,
    is_invoice_overdue,
    is_valid_invoice_status_transition,
    next_credit_note_number,
    next_invoice_number,
    next_proforma_number,
    signed_invoice_total,
    validate_invoice_vat,
)
from models import Company, Customer, Invoice, InvoiceItem, Job
from routers.company import get_or_create_company
from peppol_xml import generate_peppol_xml
from schemas import InvoiceItemCreate, InvoiceRead, InvoiceStatus
from templates_config import templates


router = APIRouter()


def validate_invoice_dates(issue_date: date, due_date: date) -> None:
    """
    Základná logická kontrola dátumov faktúry - splatnosť nemôže byť
    skôr než vystavenie (bežný preklep pri ručnom zadávaní). Splatnosť
    v ten istý deň ako vystavenie je v poriadku (napr. platba v hotovosti
    na mieste).
    """

    if due_date < issue_date:

        raise HTTPException(
            status_code=422,
            detail=(
                "Dátum splatnosti nemôže byť skôr než dátum vystavenia "
                f"({due_date.isoformat()} < {issue_date.isoformat()})"
            )
        )


def require_draft_invoice(invoice) -> None:
    """
    Úprava a zmazanie faktúry sú povolené len pre faktúry v stave Návrh -
    odoslané/uhradené faktúry sa už potichu nemenia (účtovná integrita).
    """

    if invoice.status != InvoiceStatus.DRAFT.value:

        raise HTTPException(
            status_code=409,
            detail=(
                "Túto faktúru už nie je možné upraviť ani zmazať, "
                "pretože nie je v stave Návrh."
            )
        )


# =========================================
# ZOZNAM FAKTÚR (STRÁNKA)
# =========================================

@router.get("/faktury")
def invoices_list_page(

    request: Request,

    status: str | None = None,

    q: str | None = None,

    date_from: str | None = None,

    date_to: str | None = None,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    query = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items),
            joinedload(Invoice.customer)
        )

    )

    if status is not None and status != InvoiceStatus.OVERDUE.value:

        query = query.filter(
            Invoice.status == status
        )


    # Vyhľadávanie - číslo faktúry, meno zákazníka alebo poznámka.
    # Jednoduché LIKE vyhľadávanie (nie fulltext) - appke stačí pre
    # bežný objem faktúr jedného remeselníka/živnostníka.
    search_term = (q or "").strip()

    if search_term:

        query = query.join(Invoice.customer).filter(
            or_(
                Invoice.invoice_number.ilike(f"%{search_term}%"),
                Customer.name.ilike(f"%{search_term}%"),
                Invoice.note.ilike(f"%{search_term}%")
            )
        )


    parsed_date_from = parse_optional_date(date_from or "")
    parsed_date_to = parse_optional_date(date_to or "")

    if parsed_date_from is not None:

        query = query.filter(Invoice.issue_date >= parsed_date_from)

    if parsed_date_to is not None:

        query = query.filter(Invoice.issue_date <= parsed_date_to)


    invoices = query.order_by(
        Invoice.issue_date.desc(),
        Invoice.id.desc()
    ).all()


    today = date.today()

    # Filter "Po splatnosti" nemôže byť obyčajné porovnanie stĺpca status -
    # tento stav sa nikdy neukladá (viď invoice_utils.is_invoice_overdue),
    # takže sa musí vyhodnotiť až po natiahnutí faktúr z DB.
    if status == InvoiceStatus.OVERDUE.value:

        invoices = [
            invoice
            for invoice in invoices
            if is_invoice_overdue(invoice, today)
        ]


    invoice_totals = {

        invoice.id: signed_invoice_total(invoice)

        for invoice in invoices

    }

    # Presne tá istá definícia "po splatnosti" ako všade inde v appke
    # (viď invoice_utils.is_invoice_overdue) - šablóna len kontroluje
    # členstvo v tejto množine, nepočíta si podmienku znova sama.
    overdue_invoice_ids = {
        invoice.id
        for invoice in invoices
        if is_invoice_overdue(invoice, today)
    }


    return templates.TemplateResponse(

        request=request,

        name="invoices_list.html",

        context={

            "invoices": invoices,

            "invoice_totals": invoice_totals,

            "overdue_invoice_ids": overdue_invoice_ids,

            "statuses": list(InvoiceStatus),

            "selected_status": status,

            "search_query": search_term,

            "date_from": date_from or "",

            "date_to": date_to or "",

            "today": today

        }

    )


# =========================================
# NOVÁ FAKTÚRA - FORM
# =========================================

@router.get("/customers/{customer_id}/invoices/new")
def new_invoice_form(

    customer_id: int,

    request: Request,

    job_id: int | None = None,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    customer = (

        db
        .query(Customer)
        .filter(
            Customer.id == customer_id
        )
        .first()

    )


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
            .filter(
                Job.id == job_id,
                Job.customer_id == customer_id
            )
            .first()

        )


    today = date.today()

    company = get_or_create_company(db)

    reverse_charge_available = bool(
        company.is_vat_payer and customer.ic_dph
    )


    return templates.TemplateResponse(

        request=request,

        name="invoice_form.html",

        context={

            "customer": customer,

            "job": job,

            "today": today,

            "company_is_vat_payer": company.is_vat_payer,

            "reverse_charge_available": reverse_charge_available

        }

    )


# =========================================
# NOVÁ FAKTÚRA - ULOŽENIE
# =========================================

@router.post("/customers/{customer_id}/invoices")
async def create_invoice(

    customer_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    customer = (

        db
        .query(Customer)
        .filter(
            Customer.id == customer_id
        )
        .first()

    )


    if customer is None:

        raise HTTPException(
            status_code=404,
            detail="Zákazník neexistuje"
        )


    form = await request.form()

    items_data = parse_items_from_form(form, InvoiceItemCreate, "Faktúra musí obsahovať aspoň jednu položku")


    job_id_raw = form.get("job_id", "")

    job_id = int(job_id_raw) if job_id_raw else None


    if job_id is not None:

        job = (

            db
            .query(Job)
            .filter(
                Job.id == job_id,
                Job.customer_id == customer_id
            )
            .first()

        )

        if job is None:

            raise HTTPException(
                status_code=404,
                detail="Zákazka neexistuje"
            )


    issue_date = parse_required_date(
        form.get("issue_date", ""),
        "Dátum vystavenia"
    )

    due_date = parse_required_date(
        form.get("due_date", ""),
        "Dátum splatnosti"
    )

    delivery_date = parse_optional_date(
        form.get("delivery_date", "")
    )

    validate_invoice_dates(issue_date, due_date)

    variable_symbol = form.get("variable_symbol", "").strip() or None
    constant_symbol = form.get("constant_symbol", "").strip() or None
    specific_symbol = form.get("specific_symbol", "").strip() or None
    payment_method = form.get("payment_method", "").strip() or "Prevodom"
    note = form.get("note", "").strip() or None
    reverse_charge = form.get("reverse_charge") == "on"

    company = get_or_create_company(db)

    try:

        validate_invoice_vat(
            company_is_vat_payer=company.is_vat_payer,
            customer_ic_dph=customer.ic_dph,
            reverse_charge=reverse_charge,
            item_vat_rates=[item.vat_rate for item in items_data]
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=422,
            detail=str(exc)
        )


    invoice_number = next_invoice_number(db, issue_date.year)


    new_invoice = Invoice(
        invoice_number=invoice_number,
        customer_id=customer_id,
        job_id=job_id,
        status=InvoiceStatus.DRAFT.value,
        issue_date=issue_date,
        due_date=due_date,
        delivery_date=delivery_date,
        variable_symbol=variable_symbol,
        constant_symbol=constant_symbol,
        specific_symbol=specific_symbol,
        payment_method=payment_method,
        reverse_charge=reverse_charge,
        note=note
    )


    for item in items_data:

        new_invoice.items.append(
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate
            )
        )


    db.add(new_invoice)

    db.commit()

    db.refresh(new_invoice)


    return RedirectResponse(

        url=f"/invoices/{new_invoice.id}",

        status_code=303

    )


# =========================================
# DETAIL FAKTÚRY
# =========================================

@router.get("/invoices/{invoice_id}")
def invoice_detail(

    invoice_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items),
            joinedload(Invoice.customer)
        )
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )


    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    totals = calculate_invoice_totals(invoice.items)


    # Dropdown na zmenu stavu ponúka len stavy, do ktorých sa dá z
    # aktuálneho stavu skutočne legálne prejsť (viď
    # invoice_utils.ALLOWED_INVOICE_STATUS_TRANSITIONS) - nie plný zoznam
    # všetkých stavov. "Po splatnosti" sa medzi nimi nikdy neobjaví, lebo
    # sa nikdy neukladá ako skutočný stav (počíta sa za behu).
    next_statuses = allowed_next_invoice_statuses(invoice.status)

    selectable_statuses = [invoice.status] + [
        status.value
        for status in InvoiceStatus
        if status.value in next_statuses
    ]

    can_change_status = len(next_statuses) > 0


    return templates.TemplateResponse(

        request=request,

        name="invoice_detail.html",

        context={

            "invoice": invoice,

            "totals": totals,

            "statuses": selectable_statuses,

            "can_change_status": can_change_status,

            "is_overdue": is_invoice_overdue(invoice),

            "today": date.today()

        }

    )


# =========================================
# ÚPRAVA FAKTÚRY (len v stave Návrh)
# =========================================

@router.get("/invoices/{invoice_id}/edit")
def edit_invoice_form(

    invoice_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items),
            joinedload(Invoice.customer)
        )
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )


    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    require_draft_invoice(invoice)


    company = get_or_create_company(db)

    reverse_charge_available = bool(
        company.is_vat_payer and invoice.customer.ic_dph
    )


    return templates.TemplateResponse(

        request=request,

        name="invoice_form.html",

        context={

            "customer": invoice.customer,

            "job": invoice.job,

            "today": date.today(),

            "invoice": invoice,

            "company_is_vat_payer": company.is_vat_payer,

            "reverse_charge_available": reverse_charge_available

        }

    )


@router.post("/invoices/{invoice_id}/edit")
async def update_invoice(

    invoice_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items)
        )
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )


    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    require_draft_invoice(invoice)


    form = await request.form()

    items_data = parse_items_from_form(form, InvoiceItemCreate, "Faktúra musí obsahovať aspoň jednu položku")


    issue_date = parse_required_date(
        form.get("issue_date", ""),
        "Dátum vystavenia"
    )

    due_date = parse_required_date(
        form.get("due_date", ""),
        "Dátum splatnosti"
    )

    delivery_date = parse_optional_date(
        form.get("delivery_date", "")
    )

    validate_invoice_dates(issue_date, due_date)

    variable_symbol = form.get("variable_symbol", "").strip() or None
    constant_symbol = form.get("constant_symbol", "").strip() or None
    specific_symbol = form.get("specific_symbol", "").strip() or None
    payment_method = form.get("payment_method", "").strip() or "Prevodom"
    note = form.get("note", "").strip() or None
    reverse_charge = form.get("reverse_charge") == "on"

    company = get_or_create_company(db)

    try:

        validate_invoice_vat(
            company_is_vat_payer=company.is_vat_payer,
            customer_ic_dph=invoice.customer.ic_dph,
            reverse_charge=reverse_charge,
            item_vat_rates=[item.vat_rate for item in items_data]
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=422,
            detail=str(exc)
        )


    invoice.issue_date = issue_date
    invoice.due_date = due_date
    invoice.delivery_date = delivery_date
    invoice.variable_symbol = variable_symbol
    invoice.constant_symbol = constant_symbol
    invoice.specific_symbol = specific_symbol
    invoice.payment_method = payment_method
    invoice.reverse_charge = reverse_charge
    invoice.note = note


    # Nahradenie položiek - jednoduchšie a spoľahlivejšie ako
    # zosúlaďovanie existujúcich riadkov s novým formulárom
    invoice.items.clear()

    for item in items_data:

        invoice.items.append(
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate
            )
        )


    db.commit()


    return RedirectResponse(

        url=f"/invoices/{invoice.id}",

        status_code=303

    )


# =========================================
# ZMAZANIE FAKTÚRY (len v stave Návrh)
# =========================================

@router.post("/invoices/{invoice_id}/delete")
def delete_invoice(

    invoice_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )


    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    require_draft_invoice(invoice)

    customer_id = invoice.customer_id

    db.delete(invoice)

    db.commit()


    return RedirectResponse(

        url=f"/customers/{customer_id}",

        status_code=303

    )


# =========================================
# KOPÍROVANIE FAKTÚRY
#
# Vytvorí novú faktúru (Návrh) s rovnakými položkami a zákazníkom, ale
# s novým číslom a dnešnými dátumami - šetrí čas pri opakovaných/
# podobných zákazkách. DPH sadzby, poznámka a spôsob úhrady sa preberajú,
# platobné symboly a stav sa NEPreberajú (nová faktúra začína ako Návrh
# bez väzby na starý variabilný/špecifický symbol).
# =========================================

@router.post("/invoices/{invoice_id}/duplicate")
def duplicate_invoice(

    invoice_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    original = (

        db
        .query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.customer))
        .filter(Invoice.id == invoice_id)
        .first()

    )

    if original is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    company = get_or_create_company(db)

    try:

        validate_invoice_vat(
            company_is_vat_payer=company.is_vat_payer,
            customer_ic_dph=original.customer.ic_dph,
            reverse_charge=original.reverse_charge,
            item_vat_rates=[item.vat_rate for item in original.items]
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=422,
            detail=(
                f"{exc} Pôvodná faktúra má sadzby DPH nezlučiteľné so "
                "súčasným DPH režimom firmy - skopírovanie by vytvorilo "
                "neplatnú faktúru."
            )
        )

    # Kópia zachováva, či išlo o zálohovú (proforma) faktúru - a teda aj
    # jej vlastný číselný rad (viď next_proforma_number), nech kopírovanie
    # zálohovej faktúry opäť nespotrebuje číslo z ostrej fakturačnej rady.
    if original.is_proforma:

        new_invoice_number = next_proforma_number(db, issue_date.year)

    else:

        new_invoice_number = next_invoice_number(db, issue_date.year)

    new_invoice = Invoice(
        invoice_number=new_invoice_number,
        customer_id=original.customer_id,
        job_id=original.job_id,
        status=InvoiceStatus.DRAFT.value,
        issue_date=issue_date,
        due_date=due_date,
        payment_method=original.payment_method,
        constant_symbol=original.constant_symbol,
        reverse_charge=original.reverse_charge,
        is_proforma=original.is_proforma,
        note=original.note
    )

    for item in original.items:

        new_invoice.items.append(
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate
            )
        )

    db.add(new_invoice)
    db.commit()
    db.refresh(new_invoice)


    return RedirectResponse(

        url=f"/invoices/{new_invoice.id}",

        status_code=303

    )


# =========================================
# DOBROPIS (opravný daňový doklad)
# =========================================

@router.get("/invoices/{invoice_id}/credit-note/new")
def new_credit_note_form(

    invoice_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    original = (

        db
        .query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.customer))
        .filter(Invoice.id == invoice_id)
        .first()

    )

    if original is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )

    if original.is_credit_note:

        raise HTTPException(
            status_code=409,
            detail="K dobropisu sa nedá vytvoriť ďalší dobropis."
        )

    if original.status not in (InvoiceStatus.SENT.value, InvoiceStatus.PAID.value):

        raise HTTPException(
            status_code=409,
            detail=(
                "Dobropis sa dá vytvoriť len k odoslanej alebo uhradenej "
                "faktúre (návrh jednoducho uprav/zmaž, k stornovanej "
                "faktúre dobropis nie je potrebný)."
            )
        )


    return templates.TemplateResponse(

        request=request,

        name="credit_note_form.html",

        context={

            "original": original,

            "today": date.today()

        }

    )


@router.post("/invoices/{invoice_id}/credit-note")
async def create_credit_note(

    invoice_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    original = (

        db
        .query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.customer))
        .filter(Invoice.id == invoice_id)
        .first()

    )

    if original is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )

    if original.is_credit_note:

        raise HTTPException(
            status_code=409,
            detail="K dobropisu sa nedá vytvoriť ďalší dobropis."
        )

    if original.status not in (InvoiceStatus.SENT.value, InvoiceStatus.PAID.value):

        raise HTTPException(
            status_code=409,
            detail=(
                "Dobropis sa dá vytvoriť len k odoslanej alebo uhradenej "
                "faktúre."
            )
        )


    form = await request.form()

    items_data = parse_items_from_form(
        form,
        InvoiceItemCreate,
        "Dobropis musí obsahovať aspoň jednu položku"
    )

    reason = form.get("reason", "").strip()

    if not reason:

        raise HTTPException(
            status_code=422,
            detail="Dôvod dobropisu je povinný."
        )


    issue_date = date.today()

    company = get_or_create_company(db)

    try:

        validate_invoice_vat(
            company_is_vat_payer=company.is_vat_payer,
            customer_ic_dph=original.customer.ic_dph,
            reverse_charge=original.reverse_charge,
            item_vat_rates=[item.vat_rate for item in items_data]
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=422,
            detail=str(exc)
        )


    credit_note = Invoice(
        invoice_number=next_credit_note_number(db, issue_date.year),
        customer_id=original.customer_id,
        job_id=original.job_id,
        status=InvoiceStatus.DRAFT.value,
        issue_date=issue_date,
        due_date=issue_date,
        reverse_charge=original.reverse_charge,
        is_credit_note=True,
        original_invoice_id=original.id,
        note=f"Dobropis k faktúre č. {original.invoice_number}. Dôvod: {reason}"
    )

    for item in items_data:

        credit_note.items.append(
            InvoiceItem(
                description=item.description,
                quantity=item.quantity,
                unit=item.unit,
                unit_price=item.unit_price,
                vat_rate=item.vat_rate
            )
        )

    db.add(credit_note)
    db.commit()
    db.refresh(credit_note)


    return RedirectResponse(

        url=f"/invoices/{credit_note.id}",

        status_code=303

    )


# =========================================
# PDF EXPORT
# =========================================

@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(

    invoice_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items),
            joinedload(Invoice.customer)
        )
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )


    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    company = db.query(Company).first()


    pdf_bytes = generate_invoice_pdf(invoice, company)


    return Response(

        content=pdf_bytes,

        media_type="application/pdf",

        headers={
            "Content-Disposition": f'inline; filename="faktura-{invoice.invoice_number}.pdf"'
        }

    )


# =========================================
# DODACÍ LIST (z faktúry)
# =========================================

@router.get("/invoices/{invoice_id}/delivery-note")
def invoice_delivery_note(

    invoice_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items),
            joinedload(Invoice.customer)
        )
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )

    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )

    company = get_or_create_company(db)

    pdf_bytes = generate_delivery_note_pdf(
        customer=invoice.customer,
        items=invoice.items,
        document_number=invoice.invoice_number,
        document_label=f"Faktúra č. {invoice.invoice_number}",
        issue_date=invoice.issue_date,
        company=company
    )

    return Response(

        content=pdf_bytes,

        media_type="application/pdf",

        headers={
            "Content-Disposition": f'inline; filename="dodaci-list-{invoice.invoice_number}.pdf"'
        }

    )


# =========================================
# PEPPOL XML EXPORT (príprava na e-fakturáciu)
# =========================================

@router.get("/invoices/{invoice_id}/peppol-xml")
def invoice_peppol_xml(

    invoice_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items),
            joinedload(Invoice.customer)
        )
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )


    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    company = db.query(Company).first()


    xml_bytes = generate_peppol_xml(invoice, company)


    return Response(

        content=xml_bytes,

        media_type="application/xml",

        headers={
            "Content-Disposition": f'attachment; filename="faktura-{invoice.invoice_number}-peppol.xml"'
        }

    )


# =========================================
# ZMENA STAVU FAKTÚRY
# =========================================

@router.post("/invoices/{invoice_id}/status")
def update_invoice_status(

    invoice_id: int,

    status: str = Form(...),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    invoice = (

        db
        .query(Invoice)
        .filter(
            Invoice.id == invoice_id
        )
        .first()

    )


    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )


    try:

        new_status = InvoiceStatus(status).value

    except ValueError:

        raise HTTPException(
            status_code=422,
            detail=f"Neplatný stav faktúry: {status}"
        )


    # "Po splatnosti" sa NIKDY nedá nastaviť ručne - je to čisto počítaná
    # vlastnosť podľa dátumu splatnosti (viď invoice_utils.is_invoice_overdue),
    # nie stav, ktorý by niekto mal alebo mohol manuálne priradiť.
    if new_status == InvoiceStatus.OVERDUE.value:

        raise HTTPException(
            status_code=422,
            detail=(
                "Stav 'Po splatnosti' sa počíta automaticky podľa dátumu "
                "splatnosti - nedá sa nastaviť ručne."
            )
        )


    if not is_valid_invoice_status_transition(invoice.status, new_status):

        raise HTTPException(
            status_code=409,
            detail=(
                f"Nepovolený prechod stavu z '{invoice.status}' "
                f"na '{new_status}'."
            )
        )


    invoice.status = new_status

    # Pri prechode na "Uhradená" nastavíme dátum úhrady na dnešok, ak
    # ešte nie je zaznamenaný - dá sa neskôr ručne opraviť cez
    # update_invoice_paid_date. Pri odchode zo stavu "Uhradená" (napr.
    # náprava chyby -> Stornovaná) dátum úhrady zámerne NEmažeme, aby sa
    # nestratila historická informácia o tom, kedy peniaze prišli.
    if new_status == InvoiceStatus.PAID.value and invoice.paid_date is None:

        invoice.paid_date = date.today()

    db.commit()


    return RedirectResponse(

        url=f"/invoices/{invoice_id}",

        status_code=303

    )


@router.post("/invoices/{invoice_id}/paid-date")
def update_invoice_paid_date(

    invoice_id: int,

    paid_date: str = Form(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):
    """
    Ručná oprava dátumu skutočnej úhrady - nezávislé od zmeny stavu,
    lebo platba mohla prísť inokedy, než keď to niekto zaklikol v appke.
    Dá sa aj vymazať (prázdna hodnota), ak sa niekto pomýlil.
    """

    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    if invoice is None:

        raise HTTPException(
            status_code=404,
            detail="Faktúra neexistuje"
        )

    parsed_date = parse_optional_date(paid_date)

    if parsed_date is not None and parsed_date > date.today():

        raise HTTPException(
            status_code=422,
            detail="Dátum úhrady nemôže byť v budúcnosti"
        )

    invoice.paid_date = parsed_date

    db.commit()


    return RedirectResponse(

        url=f"/invoices/{invoice_id}",

        status_code=303

    )


# =========================================
# ZOZNAM FAKTÚR (API)
# =========================================

@router.get(
    "/invoices",
    response_model=list[InvoiceRead]
)
def get_invoices(

    customer_id: int | None = None,

    status: str | None = None,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_api)

):

    query = (

        db
        .query(Invoice)
        .options(
            joinedload(Invoice.items)
        )

    )


    if customer_id is not None:

        query = query.filter(
            Invoice.customer_id == customer_id
        )


    if status is not None:

        query = query.filter(
            Invoice.status == status
        )


    return query.order_by(
        Invoice.issue_date.desc()
    ).all()
