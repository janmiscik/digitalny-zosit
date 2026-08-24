import os
from datetime import date

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

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


# =========================================
# CHYBOVÉ STRÁNKY
#
# Formuláre (napr. vytvorenie faktúry s chýbajúcou položkou) vracajú
# HTTPException, čo by FastAPI defaultne zobrazilo ako surový JSON -
# nepoužiteľné pre bežného používateľa v prehliadači. Tento handler
# preto pre prehliadačové požiadavky (Accept: text/html) zobrazí
# prehľadnú chybovú stránku namiesto JSON.
#
# Presmerovania (napr. redirect na /login pri neprihlásení, alebo
# 303 po úspešnom uložení formulára) majú vlastnú Location hlavičku
# a tento handler ich nezachytáva - prejdú ako zvyčajne.
# =========================================

@app.exception_handler(HTTPException)
async def html_aware_exception_handler(request: Request, exc: HTTPException):

    is_redirect = (
        300 <= exc.status_code < 400
        and exc.headers
        and "location" in {h.lower() for h in exc.headers.keys()}
    )

    wants_html = "text/html" in request.headers.get("accept", "")

    if is_redirect or not wants_html:

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers
        )


    detail = exc.detail

    if isinstance(detail, (list, dict)):
        detail = "Skontroluj prosím zadané údaje."


    referer = request.headers.get("referer", "/")


    return templates.TemplateResponse(

        request=request,

        name="error.html",

        status_code=exc.status_code,

        context={

            "status_code": exc.status_code,

            "detail": detail,

            "back_url": referer

        }

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
#
# Štatistiky sa počítajú priamo v databáze (COUNT/filter), nie načítaním
# všetkých riadkov do Pythonu - pri väčšom množstve dát je to podstatný
# rozdiel vo výkone aj pamäti. Zoznamy zákaziek pre jednotlivé sekcie
# (termíny, posledné zákazky) sa načítavajú len v potrebnom rozsahu
# (LIMIT, filter na strane DB) a s joinedload(Job.customer), aby sa
# predišlo N+1 dotazom pri prístupe k job.customer v šablóne.
# =========================================

@app.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: str = Depends(require_login_page)
):

    today = date.today()


    # =====================================
    # ZÁKLADNÉ ŠTATISTIKY (COUNT na strane DB)
    # =====================================

    new_jobs = (
        db
        .query(func.count(Job.id))
        .filter(Job.status == "Nová")
        .scalar()
    )

    active_jobs = (
        db
        .query(func.count(Job.id))
        .filter(Job.status == "Prebieha")
        .scalar()
    )

    total_jobs = db.query(func.count(Job.id)).scalar()

    total_customers = db.query(func.count(Customer.id)).scalar()

    total_invoices = db.query(func.count(Invoice.id)).scalar()

    overdue_invoices = (
        db
        .query(func.count(Invoice.id))
        .filter(
            Invoice.due_date < today,
            Invoice.status.notin_(("Uhradená", "Stornovaná"))
        )
        .scalar()
    )


    # =====================================
    # TERMÍNY (filtrované a zoradené priamo v DB)
    # =====================================

    jobs_with_due_date = (
        db
        .query(Job)
        .options(joinedload(Job.customer))
        .filter(
            Job.due_date.isnot(None),
            Job.status != "Hotová"
        )
    )

    overdue_jobs = (
        jobs_with_due_date
        .filter(Job.due_date < today)
        .order_by(Job.due_date.asc())
        .all()
    )

    today_jobs = (
        jobs_with_due_date
        .filter(Job.due_date == today)
        .order_by(Job.due_date.asc())
        .all()
    )

    upcoming_jobs = (
        jobs_with_due_date
        .filter(Job.due_date > today)
        .order_by(Job.due_date.asc())
        .limit(5)
        .all()
    )


    # =====================================
    # POSLEDNÉ ZÁKAZKY (najnovšie vytvorené, max 5)
    # =====================================

    recent_jobs = (
        db
        .query(Job)
        .options(joinedload(Job.customer))
        .order_by(Job.id.desc())
        .limit(5)
        .all()
    )


    return templates.TemplateResponse(

        request=request,

        name="index.html",

        context={

            "jobs": recent_jobs,

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

