from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from auth import require_login_api, require_login_page
from database import get_db
from models import Customer, Job
from schemas import JobCreate, JobRead, JobStatus, JobUpdate
from templates_config import templates


router = APIRouter()


def parse_due_date(raw_value: str) -> date | None:
    """
    Bezpečne skonvertuje reťazec vo formáte YYYY-MM-DD na date.
    Vráti None pre prázdny vstup, vyhodí HTTPException 400 pre nevalidný formát.
    """

    if not raw_value:
        return None

    try:

        return date.fromisoformat(raw_value)

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Neplatný formát dátumu (očakávaný formát: RRRR-MM-DD)"
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


    parsed_due_date = parse_due_date(due_date)


    try:

        job_data = JobCreate(
            title=title,
            description=description or None,
            status=JobStatus(status),
            due_date=parsed_due_date,
            customer_id=customer_id
        )

    except (ValidationError, ValueError) as exc:

        raise HTTPException(
            status_code=422,
            detail=str(exc)
        )


    new_job = Job(
        **job_data.model_dump()
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

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

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

        raise HTTPException(
            status_code=404,
            detail="Zákazka neexistuje"
        )


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

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

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

        raise HTTPException(
            status_code=404,
            detail="Zákazka neexistuje"
        )


    parsed_due_date = parse_due_date(due_date)


    try:

        job_data = JobUpdate(
            title=title,
            description=description or None,
            status=JobStatus(status),
            due_date=parsed_due_date
        )

    except (ValidationError, ValueError) as exc:

        raise HTTPException(
            status_code=422,
            detail=str(exc)
        )


    for field, value in job_data.model_dump().items():

        setattr(job, field, value)


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

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

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

        raise HTTPException(
            status_code=404,
            detail="Zákazka neexistuje"
        )


    try:

        job.status = JobStatus(status)

    except ValueError:

        raise HTTPException(
            status_code=422,
            detail=f"Neplatný stav zákazky: {status}"
        )


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

    db: Session = Depends(get_db),

    user: str = Depends(require_login_api)

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


    return query.order_by(
        Job.due_date.asc()
    ).all()
