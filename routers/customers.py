from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Customer
from schemas import CustomerRead


router = APIRouter()


templates = Jinja2Templates(
    directory="templates"
)


# =========================================
# CUSTOMER DETAIL
# =========================================

@router.get("/customers/{customer_id}")
def customer_detail(

    customer_id: int,

    request: Request,

    db: Session = Depends(get_db)

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

        return {
            "error": "Zákazník neexistuje"
        }


    jobs = customer.jobs


    return templates.TemplateResponse(

        request=request,

        name="customer.html",

        context={

            "customer": customer,

            "jobs": jobs

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

    db: Session = Depends(get_db)

):

    new_customer = Customer(

        name=name,

        phone=phone,

        email=email,

        address=address,

        note=note

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

    db: Session = Depends(get_db)

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

        return {
            "error": "Zákazník neexistuje"
        }


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

    db: Session = Depends(get_db)

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

        return {
            "error": "Zákazník neexistuje"
        }


    customer.name = name

    customer.phone = phone

    customer.email = email

    customer.address = address

    customer.note = note


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

    db: Session = Depends(get_db)

):

    query = db.query(Customer)


    if search is not None:

        query = query.filter(
            Customer.name.ilike(
                f"%{search}%"
            )
        )


    return query.all()