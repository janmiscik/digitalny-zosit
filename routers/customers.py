from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from csrf import verify_csrf
from auth import require_login_api, require_login_page
from database import get_db
from ico_lookup import lookup_company_by_ico
from invoice_utils import calculate_invoice_totals, signed_invoice_total
from customer_history import (
    MAX_NOTE_LENGTH,
    build_timeline,
    customer_photos,
    customer_summary,
    format_note_time,
)
from audit_log import log_action
from models import Customer, CustomerNote
from schemas import CustomerCreate, CustomerRead, CustomerUpdate, InvoiceStatus
from search_utils import matches_search
from templates_config import templates


router = APIRouter(dependencies=[Depends(verify_csrf)])


# =========================================
# VYHĽADANIE FIRMY PODĽA IČO (auto-doplnenie formulára)
# =========================================

@router.get("/customers/lookup-ico/{ico}")
def lookup_customer_by_ico(

    ico: str,

    user: str = Depends(require_login_api)

):

    result = lookup_company_by_ico(ico)

    if result is None:

        raise HTTPException(
            status_code=404,
            detail=(
                "Firma s týmto IČO sa nenašla, alebo vyhľadávacia "
                "služba momentálne nie je dostupná. Údaje vyplň ručne."
            )
        )

    return result


# =========================================
# ZOZNAM ZÁKAZNÍKOV (STRÁNKA)
# =========================================

@router.get("/zakaznici")
def customers_list_page(

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    customers = (

        db
        .query(Customer)
        .options(
            joinedload(Customer.jobs)
        )
        .order_by(
            Customer.name.asc()
        )
        .all()

    )


    return templates.TemplateResponse(

        request=request,

        name="customers_list.html",

        context={

            "customers": customers

        }

    )


# =========================================
# CUSTOMER DETAIL
# =========================================

@router.get("/customers/{customer_id}")
def customer_detail(

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


    jobs = customer.jobs

    invoices = sorted(
        customer.invoices,
        key=lambda invoice: invoice.issue_date,
        reverse=True
    )

    quotes = sorted(
        customer.quotes,
        key=lambda quote: quote.issue_date,
        reverse=True
    )


    # =====================================
    # HISTÓRIA - fakturované a neuhradené sumy
    # =====================================

    active_invoices = [
        invoice
        for invoice in invoices
        if invoice.status != InvoiceStatus.CANCELLED.value
        and not invoice.is_proforma
    ]

    unpaid_invoices = [
        invoice
        for invoice in active_invoices
        if invoice.status != InvoiceStatus.PAID.value
    ]

    total_invoiced = sum(
        (
            signed_invoice_total(invoice)
            for invoice in active_invoices
        ),
        Decimal("0")
    )

    total_unpaid = sum(
        (
            signed_invoice_total(invoice)
            for invoice in unpaid_invoices
        ),
        Decimal("0")
    )


    return templates.TemplateResponse(

        request=request,

        name="customer.html",

        context={

            "customer": customer,

            "jobs": jobs,

            "invoices": invoices,

            "quotes": quotes,

            "total_invoiced": total_invoiced,

            "total_unpaid": total_unpaid,

            "unpaid_count": len(unpaid_invoices),

            "summary": customer_summary(customer),

            "timeline": build_timeline(customer),

            "photos": customer_photos(customer),

            "notes": [
                {"id": note.id, "text": note.text, "time": format_note_time(note.created_at)}
                for note in customer.notes
            ],

            "max_note_length": MAX_NOTE_LENGTH,

            "today": date.today()

        }

    )


# =========================================
# CREATE CUSTOMER
# =========================================

@router.post("/customers")
def create_customer(

    name: str = Form(...),

    phone: str = Form(""),

    email: str = Form(""),

    address: str = Form(""),

    city: str = Form(""),

    zip_code: str = Form(""),

    note: str = Form(""),

    ico: str = Form(""),

    dic: str = Form(""),

    ic_dph: str = Form(""),

    country_code: str = Form("SK"),

    peppol_scheme_id: str = Form(""),

    peppol_endpoint_id: str = Form(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    try:

        customer_data = CustomerCreate(
            name=name,
            phone=phone or None,
            email=email or None,
            address=address or None,
            city=city or None,
            zip_code=zip_code or None,
            note=note or None,
            ico=ico or None,
            dic=dic or None,
            ic_dph=ic_dph or None,
            country_code=country_code or None,
            peppol_scheme_id=peppol_scheme_id or None,
            peppol_endpoint_id=peppol_endpoint_id or None
        )

    except ValidationError as exc:

        # include_context=False - pydantic inak do 'ctx' vkladá
        # priamo pôvodný Python ValueError objekt (z field_validator),
        # ktorý nie je JSON-serializovateľný a FastAPI by pri jeho
        # posielaní ako HTTPException detail spadol s 500.
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False)
        )


    new_customer = Customer(
        **customer_data.model_dump()
    )


    db.add(new_customer)

    db.flush()

    log_action(
        db,
        "customer.create",
        entity_type="customer",
        entity_id=new_customer.id,
        detail=f"Zákazník {new_customer.name}"
    )

    db.commit()


    return RedirectResponse(

        url="/zakaznici",

        status_code=303

    )


# =========================================
# EDIT CUSTOMER - FORM
# =========================================

@router.get("/customers/{customer_id}/edit")
def edit_customer_form(

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


    return templates.TemplateResponse(

        request=request,

        name="edit_customer.html",

        context={

            "customer": customer

        }

    )


# =========================================
# EDIT CUSTOMER - SAVE
# =========================================

@router.post("/customers/{customer_id}/edit")
def update_customer(

    customer_id: int,

    name: str = Form(...),

    phone: str = Form(""),

    email: str = Form(""),

    address: str = Form(""),

    city: str = Form(""),

    zip_code: str = Form(""),

    note: str = Form(""),

    ico: str = Form(""),

    dic: str = Form(""),

    ic_dph: str = Form(""),

    country_code: str = Form("SK"),

    peppol_scheme_id: str = Form(""),

    peppol_endpoint_id: str = Form(""),

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


    try:

        customer_data = CustomerUpdate(
            name=name,
            phone=phone or None,
            email=email or None,
            address=address or None,
            city=city or None,
            zip_code=zip_code or None,
            note=note or None,
            ico=ico or None,
            dic=dic or None,
            ic_dph=ic_dph or None,
            country_code=country_code or None,
            peppol_scheme_id=peppol_scheme_id or None,
            peppol_endpoint_id=peppol_endpoint_id or None
        )

    except ValidationError as exc:

        # include_context=False - pydantic inak do 'ctx' vkladá
        # priamo pôvodný Python ValueError objekt (z field_validator),
        # ktorý nie je JSON-serializovateľný a FastAPI by pri jeho
        # posielaní ako HTTPException detail spadol s 500.
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_context=False)
        )


    changed = []

    for field, value in customer_data.model_dump().items():

        if getattr(customer, field) != value:
            changed.append(field)

        setattr(customer, field, value)

    if changed:

        # zapisujú sa len NÁZVY zmenených polí, nie osobné údaje samotné
        log_action(
            db,
            "customer.edit",
            entity_type="customer",
            entity_id=customer.id,
            detail=f"Zákazník {customer.name}: zmenené polia {', '.join(sorted(changed))}"
        )

    db.commit()


    return RedirectResponse(

        url=f"/customers/{customer_id}",

        status_code=303

    )


# =========================================
# CUSTOMERS API
# =========================================

@router.get(
    "/customers",
    response_model=list[CustomerRead]
)
def get_customers(

    search: str | None = Query(
        default=None
    ),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_api)

):

    customers = (
        db
        .query(Customer)
        .order_by(Customer.name.asc())
        .all()
    )


    # Bez ohľadu na diakritiku a veľkosť písmen (viď search_utils.py).
    if search is not None:

        customers = [
            customer
            for customer in customers
            if matches_search(
                search,
                customer.name,
                customer.phone,
                customer.email,
                customer.ico
            )
        ]


    return customers


# =========================================
# POZNÁMKY V HISTÓRII ZÁKAZNÍKA
# =========================================

@router.post("/customers/{customer_id}/notes")
def add_customer_note(

    customer_id: int,

    text: str = Form(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if customer is None:

        raise HTTPException(status_code=404, detail="Zákazník neexistuje")

    clean_text = text.strip()

    if not clean_text:

        raise HTTPException(status_code=422, detail="Poznámka nesmie byť prázdna")

    if len(clean_text) > MAX_NOTE_LENGTH:

        raise HTTPException(
            status_code=422,
            detail=f"Poznámka môže mať najviac {MAX_NOTE_LENGTH} znakov"
        )

    db.add(CustomerNote(customer_id=customer_id, text=clean_text))

    log_action(
        db,
        "customer.note_add",
        entity_type="customer",
        entity_id=customer_id,
        detail=f"Poznámka k zákazníkovi {customer.name}"
    )

    db.commit()

    return RedirectResponse(url=f"/customers/{customer_id}#poznamky", status_code=303)


@router.post("/customers/{customer_id}/notes/{note_id}/delete")
def delete_customer_note(

    customer_id: int,

    note_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    note = (
        db
        .query(CustomerNote)
        .filter(CustomerNote.id == note_id, CustomerNote.customer_id == customer_id)
        .first()
    )

    if note is None:

        raise HTTPException(status_code=404, detail="Poznámka neexistuje")

    db.delete(note)

    log_action(
        db,
        "customer.note_delete",
        entity_type="customer",
        entity_id=customer_id,
        detail="Poznámka zmazaná"
    )

    db.commit()

    return RedirectResponse(url=f"/customers/{customer_id}#poznamky", status_code=303)
