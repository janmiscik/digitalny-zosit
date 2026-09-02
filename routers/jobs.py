from datetime import date
from decimal import Decimal
import calendar as calendar_module

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, joinedload

from auth import require_login_api, require_login_page
from database import get_db
from invoice_utils import CLOSED_INVOICE_STATUSES, calculate_invoice_totals
from models import Customer, Job, JobCost, JobPhoto
from schemas import InvoiceStatus, JobCreate, JobRead, JobStatus, JobUpdate
from templates_config import templates
from uploads_utils import delete_job_photo, job_photo_path, save_job_photo_upload


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
# ZOZNAM ZÁKAZIEK (STRÁNKA)
# =========================================

@router.get("/zakazky")
def jobs_list_page(

    request: Request,

    status: str | None = None,

    when: str | None = None,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    today = date.today()


    query = (

        db
        .query(Job)
        .options(
            joinedload(Job.customer)
        )

    )

    if status is not None:

        query = query.filter(
            Job.status == status
        )


    if when == "overdue":

        query = query.filter(
            Job.due_date.isnot(None),
            Job.due_date < today,
            Job.status != "Hotová"
        )

    elif when == "no_date":

        query = query.filter(
            Job.due_date.is_(None)
        )


    jobs = query.order_by(
        Job.due_date.is_(None),
        Job.due_date.asc()
    ).all()


    return templates.TemplateResponse(

        request=request,

        name="jobs_list.html",

        context={

            "jobs": jobs,

            "statuses": list(JobStatus),

            "selected_status": status,

            "selected_when": when,

            "today": today

        }

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


    # =====================================
    # NÁKLADY A REÁLNY ZISK ZO ZÁKAZKY
    #
    # "Fakturovaná suma" počíta len faktúry, ktoré boli skutočne vystavené
    # zákazníkovi a nie sú zrušené ani zálohové (Odoslaná/Uhradená) - Návrh
    # ešte nemusí byť finálny a Stornovaná/proforma nie sú reálny príjem.
    # =====================================

    invoiced_statuses = (InvoiceStatus.SENT.value, InvoiceStatus.PAID.value)

    invoiced_total = sum(
        (
            calculate_invoice_totals(invoice.items)["total_gross"]
            for invoice in job.invoices
            if invoice.status in invoiced_statuses and not invoice.is_proforma
        ),
        Decimal("0")
    )

    total_costs = sum(
        (cost.amount for cost in job.costs),
        Decimal("0")
    )

    profit = invoiced_total - total_costs


    return templates.TemplateResponse(

        request=request,

        name="edit_job.html",

        context={

            "job": job,

            "today": date.today(),

            "invoiced_total": invoiced_total,

            "total_costs": total_costs,

            "profit": profit

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
# KALENDÁR / PLÁNOVAČ (podľa termínu zákazky)
# =========================================

@router.get("/kalendar")
def jobs_calendar_page(

    request: Request,

    year: int | None = None,

    month: int | None = None,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    today = date.today()

    year = year or today.year
    month = month or today.month

    if month < 1 or month > 12:

        raise HTTPException(
            status_code=400,
            detail="Neplatný mesiac"
        )


    first_day = date(year, month, 1)
    last_day_num = calendar_module.monthrange(year, month)[1]
    last_day = date(year, month, last_day_num)

    jobs_this_month = (
        db
        .query(Job)
        .options(joinedload(Job.customer))
        .filter(
            Job.due_date >= first_day,
            Job.due_date <= last_day
        )
        .order_by(Job.due_date.asc())
        .all()
    )

    jobs_by_day: dict[int, list] = {}

    for job in jobs_this_month:

        jobs_by_day.setdefault(job.due_date.day, []).append(job)


    # calendar.monthcalendar vráti zoznam týždňov, každý týždeň je
    # zoznam 7 čísel dní (0 = deň mimo tohto mesiaca)
    calendar_module.setfirstweekday(calendar_module.MONDAY)
    weeks = calendar_module.monthcalendar(year, month)

    month_names = [
        "", "Január", "Február", "Marec", "Apríl", "Máj", "Jún",
        "Júl", "August", "September", "Október", "November", "December"
    ]

    # Navigácia na predchádzajúci/nasledujúci mesiac
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1

    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1


    return templates.TemplateResponse(

        request=request,

        name="jobs_calendar.html",

        context={

            "year": year,

            "month": month,

            "month_name": month_names[month],

            "weeks": weeks,

            "jobs_by_day": jobs_by_day,

            "today": today,

            "prev_year": prev_year,

            "prev_month": prev_month,

            "next_year": next_year,

            "next_month": next_month

        }

    )


# =========================================
# FOTODOKUMENTÁCIA ZÁKAZKY (pred/po)
# =========================================

@router.post("/jobs/{job_id}/photos")
async def upload_job_photo(

    job_id: int,

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    job = db.query(Job).filter(Job.id == job_id).first()

    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Zákazka neexistuje"
        )


    form = await request.form()

    photo_type = form.get("photo_type", "pred").strip()

    if photo_type not in ("pred", "po"):
        photo_type = "pred"

    upload = form.get("photo")

    if upload is None or not getattr(upload, "filename", None):

        raise HTTPException(
            status_code=422,
            detail="Nebol vybraný žiadny súbor"
        )


    filename = await save_job_photo_upload(upload, job_id)

    photo = JobPhoto(
        job_id=job_id,
        filename=filename,
        photo_type=photo_type,
        uploaded_at=date.today()
    )

    db.add(photo)
    db.commit()


    return RedirectResponse(

        url=f"/jobs/{job_id}/edit",

        status_code=303

    )


@router.post("/jobs/{job_id}/photos/{photo_id}/delete")
def delete_job_photo_route(

    job_id: int,

    photo_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    photo = (
        db
        .query(JobPhoto)
        .filter(JobPhoto.id == photo_id, JobPhoto.job_id == job_id)
        .first()
    )

    if photo is None:

        raise HTTPException(
            status_code=404,
            detail="Fotka neexistuje"
        )

    delete_job_photo(photo.filename)

    db.delete(photo)
    db.commit()


    return RedirectResponse(

        url=f"/jobs/{job_id}/edit",

        status_code=303

    )


@router.get("/jobs/photos/{filename}")
def serve_job_photo(

    filename: str,

    user: str = Depends(require_login_page)

):
    """
    Rovnaký princíp ako /uploads/{filename} pre logo/podpis (main.py) -
    fotky zákaziek NIE sú verejne dostupné cez StaticFiles, servujú sa
    len prihlásenému používateľovi, s whitelistou znakov v názve súboru
    (obrana proti path traversal).
    """

    path = job_photo_path(filename)

    if path is None:

        raise HTTPException(
            status_code=404,
            detail="Fotka neexistuje"
        )

    extension = path.suffix.lower()

    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }

    return FileResponse(
        path,
        media_type=media_types.get(extension, "application/octet-stream")
    )


# =========================================
# NÁKLADY NA ZÁKAZKU
# =========================================

@router.post("/jobs/{job_id}/costs")
def add_job_cost(

    job_id: int,

    description: str = Form(...),

    amount: str = Form(...),

    cost_date: str = Form(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    job = db.query(Job).filter(Job.id == job_id).first()

    if job is None:

        raise HTTPException(
            status_code=404,
            detail="Zákazka neexistuje"
        )


    try:

        amount_decimal = Decimal(amount)

    except Exception:

        raise HTTPException(
            status_code=422,
            detail="Neplatná suma nákladu"
        )

    if amount_decimal < 0:

        raise HTTPException(
            status_code=422,
            detail="Suma nákladu nemôže byť záporná"
        )


    parsed_cost_date = parse_due_date(cost_date) or date.today()


    cost = JobCost(
        job_id=job_id,
        description=description.strip(),
        amount=amount_decimal,
        cost_date=parsed_cost_date
    )

    db.add(cost)
    db.commit()


    return RedirectResponse(

        url=f"/jobs/{job_id}/edit",

        status_code=303

    )


@router.post("/jobs/{job_id}/costs/{cost_id}/delete")
def delete_job_cost(

    job_id: int,

    cost_id: int,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    cost = (
        db
        .query(JobCost)
        .filter(JobCost.id == cost_id, JobCost.job_id == job_id)
        .first()
    )

    if cost is None:

        raise HTTPException(
            status_code=404,
            detail="Náklad neexistuje"
        )

    db.delete(cost)
    db.commit()


    return RedirectResponse(

        url=f"/jobs/{job_id}/edit",

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
