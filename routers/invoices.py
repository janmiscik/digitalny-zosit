from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from auth import require_login_api, require_login_page
from database import get_db
from invoice_pdf import generate_invoice_pdf
from invoice_utils import (
    allowed_next_invoice_statuses,
    calculate_invoice_totals,
    is_invoice_overdue,
    is_valid_invoice_status_transition,
    next_invoice_number,
    validate_invoice_vat,
)
from models import Company, Customer, Invoice, InvoiceItem, Job
from routers.company import get_or_create_company
from peppol_xml import generate_peppol_xml
from schemas import InvoiceItemCreate, InvoiceRead, InvoiceStatus
from templates_config import templates


router = APIRouter()


def parse_optional_date(raw_value: str) -> date | None:

    if not raw_value:
        return None

    try:

        return date.fromisoformat(raw_value)

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Neplatný formát dátumu (očakávaný formát: RRRR-MM-DD)"
        )


def parse_required_date(raw_value: str, field_label: str) -> date:

    parsed = parse_optional_date(raw_value)

    if parsed is None:

        raise HTTPException(
            status_code=400,
            detail=f"Pole '{field_label}' je povinné"
        )

    return parsed


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


def parse_items_from_form(form) -> list:
    """
    Zdieľaná logika parsovania riadkov položiek faktúry z formulára -
    používa create_invoice aj update_invoice, aby sa nedublovala.
    Vyhodí HTTPException 422, ak nie je zadaná ani jedna platná položka.
    """

    descriptions = form.getlist("description")
    quantities = form.getlist("quantity")
    units = form.getlist("unit")
    unit_prices = form.getlist("unit_price")
    vat_rates = form.getlist("vat_rate")


    items_data = []

    for i in range(len(descriptions)):

        description = descriptions[i].strip()

        if not description:
            continue

        try:

            item = InvoiceItemCreate(
                description=description,
                quantity=quantities[i] or "1",
                unit=units[i] or "ks",
                unit_price=unit_prices[i] or "0",
                vat_rate=int(vat_rates[i] or 23)
            )

        except (ValidationError, ValueError, IndexError) as exc:

            raise HTTPException(
                status_code=422,
                detail=f"Neplatná položka faktúry: {exc}"
            )

        items_data.append(item)


    if not items_data:

        raise HTTPException(
            status_code=422,
            detail="Faktúra musí obsahovať aspoň jednu položku"
        )

    return items_data


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

        invoice.id: calculate_invoice_totals(invoice.items)["total_gross"]

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

    items_data = parse_items_from_form(form)


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

            "is_overdue": is_invoice_overdue(invoice)

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

    items_data = parse_items_from_form(form)


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
