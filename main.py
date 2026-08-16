from datetime import date

from fastapi import FastAPI, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import Base, engine, get_db

from models import Customer, Job

from routers.customers import router as customers_router
from routers.jobs import router as jobs_router


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
# ROUTERS
# =========================================

app.include_router(
    customers_router
)

app.include_router(
    jobs_router
)


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