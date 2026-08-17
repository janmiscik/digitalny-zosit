from datetime import date

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Customer, Job
from schemas import JobRead


router = APIRouter()


templates = Jinja2Templates(
    directory="templates"
)


# =========================================
# CREATE JOB
# =========================================

@router.post("/jobs")
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

@router.get("/jobs/{job_id}/edit")
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

@router.post("/jobs/{job_id}/edit")
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

@router.post("/jobs/{job_id}/status")
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
# JOBS API
# =========================================

@router.get(
    "/jobs",
    response_model=list[JobRead]
)
def get_jobs(

    status: str | None = Query(
        default=None
    ),

    customer_id: int | None = Query(
        default=None
    ),

    db: Session = Depends(get_db)

):

    query = db.query(Job)


    if status is not None:

        query = query.filter(
            Job.status == status
        )


    if customer_id is not None:

        query = query.filter(
            Job.customer_id == customer_id
        )


    return query.all()