from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.orm import Session

from auth import require_login_api, require_login_page
from database import get_db
from models import Customer
from schemas import CustomerCreate, CustomerRead, CustomerUpdate
from templates_config import templates


router = APIRouter()


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


    return templates.TemplateResponse(

        request=request,

        name="customer.html",

        context={

            "customer": customer,

            "jobs": jobs,

            "invoices": invoices

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

    note: str = Form(""),

    ico: str = Form(""),

    dic: str = Form(""),

    ic_dph: str = Form(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    try:

        customer_data = CustomerCreate(
            name=name,
            phone=phone or None,
            email=email or None,
            address=address or None,
            note=note or None,
            ico=ico or None,
            dic=dic or None,
            ic_dph=ic_dph or None
        )

    except ValidationError as exc:

        raise HTTPException(
            status_code=422,
            detail=exc.errors()
        )


    new_customer = Customer(
        **customer_data.model_dump()
    )


    db.add(new_customer)

    db.commit()


    return RedirectResponse(

        url="/",

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

    note: str = Form(""),

    ico: str = Form(""),

    dic: str = Form(""),

    ic_dph: str = Form(""),

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
            note=note or None,
            ico=ico or None,
            dic=dic or None,
            ic_dph=ic_dph or None
        )

    except ValidationError as exc:

        raise HTTPException(
            status_code=422,
            detail=exc.errors()
        )


    for field, value in customer_data.model_dump().items():

        setattr(customer, field, value)


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

    query = db.query(Customer)


    if search is not None:

        search_value = f"%{search}%"

        query = query.filter(
            or_(
                Customer.name.ilike(search_value),
                Customer.phone.ilike(search_value),
                Customer.email.ilike(search_value)
            )
        )


    return query.order_by(
        Customer.name.asc()
    ).all()
