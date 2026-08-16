from datetime import date

from fastapi import FastAPI, Depends, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal
from models import Customer, Job


# =========================================
# DATABASE
# =========================================

Base.metadata.create_all(bind=engine)


# =========================================
# APP
# =========================================

app = FastAPI()


app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)


templates = Jinja2Templates(
    directory="templates"
)


# =========================================
# DATABASE SESSION
# =========================================

def get_db():

    db = SessionLocal()

    try:

        yield db

    finally:

        db.close()


# =========================================
# HOME / DASHBOARD
# =========================================

@app.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db)
):

    customers = (
        db
        .query(Customer)
        .all()
    )


    jobs = (
        db
        .query(Job)
        .all()
    )


    # =====================================
    # ZÁKLADNÉ ŠTATISTIKY
    # =====================================

    new_jobs = sum(
        1
        for job in jobs
        if job.status == "Nová"
    )


    active_jobs = sum(
        1
        for job in jobs
        if job.status == "Prebieha"
    )


    total_jobs = len(jobs)


    # =====================================
    # TERMÍNY
    # =====================================

    today = date.today()


    overdue_jobs = []

    today_jobs = []

    upcoming_jobs = []


    for job in jobs:

        if job.due_date is None:
            continue


        # Hotové zákazky už nechceme
        # zobrazovať medzi termínmi.

        if job.status == "Hotová":
            continue


        if job.due_date < today:

            overdue_jobs.append(job)


        elif job.due_date == today:

            today_jobs.append(job)


        else:

            upcoming_jobs.append(job)


    # =====================================
    # ZORADENIE
    # =====================================

    overdue_jobs.sort(
        key=lambda job: job.due_date
    )


    today_jobs.sort(
        key=lambda job: job.due_date
    )


    upcoming_jobs.sort(
        key=lambda job: job.due_date
    )


    # =====================================
    # NAJBLIŽŠIE ZÁKAZKY
    # =====================================

    upcoming_jobs = upcoming_jobs[:5]


    return templates.TemplateResponse(

        request=request,

        name="index.html",

        context={

            "customers": customers,

            "jobs": jobs,

            "new_jobs": new_jobs,

            "active_jobs": active_jobs,

            "total_jobs": total_jobs,

            "overdue_jobs": overdue_jobs,

            "today_jobs": today_jobs,

            "upcoming_jobs": upcoming_jobs,

            "today": today

        }

    )


# =========================================
# CREATE CUSTOMER
# =========================================

@app.post("/customers")
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
# CUSTOMER DETAIL
# =========================================

@app.get("/customers/{customer_id}")
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


    jobs = (

        db
        .query(Job)
        .filter(
            Job.customer_id == customer_id
        )
        .all()

    )


    return templates.TemplateResponse(

        request=request,

        name="customer.html",

        context={

            "customer": customer,

            "jobs": jobs

        }

    )


# =========================================
# EDIT CUSTOMER - FORM
# =========================================

@app.get("/customers/{customer_id}/edit")
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

@app.post("/customers/{customer_id}/edit")
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
# CREATE JOB
# =========================================

@app.post("/jobs")
def create_job(

    title: str = Form(...),

    description: str = Form(""),

    status: str = Form("Nová"),

    due_date: str = Form(""),

    customer_id: int = Form(...),

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


    parsed_due_date = None


    if due_date:

        parsed_due_date = date.fromisoformat(
            due_date
        )


    new_job = Job(

        title=title,

        description=description,

        status=status,

        due_date=parsed_due_date,

        customer_id=customer_id

    )


    db.add(new_job)

    db.commit()


    return RedirectResponse(

        url=f"/customers/{customer_id}",

        status_code=303

    )


# =========================================
# EDIT JOB - FORM
# =========================================

@app.get("/jobs/{job_id}/edit")
def edit_job_form(

    job_id: int,

    request: Request,

    db: Session = Depends(get_db)

):

    job = (

        db
        .query(Job)
        .filter(
            Job.id == job_id
        )
        .first()

    )


    if job is None:

        return {
            "error": "Zákazka neexistuje"
        }


    return templates.TemplateResponse(

        request=request,

        name="edit_job.html",

        context={

            "job": job

        }

    )


# =========================================
# EDIT JOB - SAVE
# =========================================

@app.post("/jobs/{job_id}/edit")
def update_job(

    job_id: int,

    title: str = Form(...),

    description: str = Form(""),

    status: str = Form("Nová"),

    due_date: str = Form(""),

    db: Session = Depends(get_db)

):

    job = (

        db
        .query(Job)
        .filter(
            Job.id == job_id
        )
        .first()

    )


    if job is None:

        return {
            "error": "Zákazka neexistuje"
        }


    parsed_due_date = None


    if due_date:

        parsed_due_date = date.fromisoformat(
            due_date
        )


    job.title = title

    job.description = description

    job.status = status

    job.due_date = parsed_due_date


    db.commit()


    return RedirectResponse(

        url=f"/customers/{job.customer_id}",

        status_code=303

    )


# =========================================
# UPDATE JOB STATUS
# =========================================

@app.post("/jobs/{job_id}/status")
def update_job_status(

    job_id: int,

    status: str = Form(...),

    db: Session = Depends(get_db)

):

    job = (

        db
        .query(Job)
        .filter(
            Job.id == job_id
        )
        .first()

    )


    if job is None:

        return {
            "error": "Zákazka neexistuje"
        }


    job.status = status


    db.commit()


    return RedirectResponse(

        url=f"/customers/{job.customer_id}",

        status_code=303

    )


# =========================================
# CUSTOMERS API
# =========================================

@app.get("/customers")
def get_customers(

    db: Session = Depends(get_db)

):

    return (

        db
        .query(Customer)
        .all()

    )


# =========================================
# JOBS API
# =========================================

@app.get("/jobs")
def get_jobs(

    db: Session = Depends(get_db)

):

    return (

        db
        .query(Job)
        .all()

    )