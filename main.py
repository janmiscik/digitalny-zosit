import os
from datetime import date

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from auth import require_login_page
from database import Base, engine, get_db

from models import Customer, Invoice, Job

from routers.auth import router as auth_router
from routers.company import router as company_router
from routers.customers import router as customers_router
from routers.invoices import router as invoices_router
from routers.jobs import router as jobs_router

from templates_config import templates


# =========================================
# DATABASE
# =========================================

Base.metadata.create_all(bind=engine)


# =========================================
# APP
# =========================================

app = FastAPI()


SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY nie je nastavený v .env - pozri .env.example"
    )


app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
)


app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

from uploads_utils import ensure_uploads_dir, UPLOADS_DIR

ensure_uploads_dir()

app.mount(
    "/uploads",
    StaticFiles(directory=str(UPLOADS_DIR)),
    name="uploads"
)


# =========================================
# ROUTERS
# =========================================

app.include_router(
    auth_router
)

app.include_router(
    customers_router
)

app.include_router(
    jobs_router
)

app.include_router(
    invoices_router
)

app.include_router(
    company_router
)


# =========================================
# HOME / DASHBOARD
# =========================================

@app.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_login_page)
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


    invoices = (
        db
        .query(Invoice)
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

    total_customers = len(customers)

    total_invoices = len(invoices)


    # =====================================
    # TERMÍNY
    # =====================================

    today = date.today()


    overdue_invoices = sum(
        1
        for invoice in invoices
        if invoice.due_date < today
        and invoice.status not in ("Uhradená", "Stornovaná")
    )


    overdue_jobs = []

    today_jobs = []

    upcoming_jobs = []


    for job in jobs:

        if job.due_date is None:
            continue


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

            "total_customers": total_customers,

            "total_invoices": total_invoices,

            "overdue_invoices": overdue_invoices,

            "overdue_jobs": overdue_jobs,

            "today_jobs": today_jobs,

            "upcoming_jobs": upcoming_jobs,

            "today": today

        }

    )
