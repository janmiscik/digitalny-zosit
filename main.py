import os
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, inspect
from sqlalchemy.orm import Session, joinedload

from auth import require_login_page
from database import Base, engine, get_db
from invoice_utils import CLOSED_INVOICE_STATUSES, calculate_invoice_totals, is_invoice_overdue, signed_invoice_total

from models import Customer, Invoice, Job, Quote
from schemas import InvoiceStatus

from routers.auth import router as auth_router
from routers.company import router as company_router
from routers.customers import router as customers_router
from routers.invoices import router as invoices_router
from routers.jobs import router as jobs_router
from routers.quotes import router as quotes_router

from templates_config import templates


# =========================================
# DATABASE
#
# ZÁMERNE tu nie je Base.metadata.create_all(bind=engine) - schéma DB sa
# spravuje VÝHRADNE cez Alembic migrácie (viď README, "alembic upgrade
# head"). create_all() by na čerstvej databáze potichu vytvorilo tabuľky
# priamo z aktuálnych modelov (bez zápisu do alembic_version), takže by
# appka fungovala navonok rovnako, ale alembic by si "myslel", že žiadna
# migrácia ešte neprebehla - pri ďalšej reálnej migrácii by to viedlo k
# chybám ("tabuľka už existuje") alebo k rozídeniu sa skutočnej schémy
# od toho, čo si Alembic o nej myslí.
#
# Namiesto toho appka pri štarte (nie pri importe - viď lifespan nižšie)
# len skontroluje, že migrácie boli spustené, a ak nie, zlyhá s jasnou
# hláškou - radšej hneď pri štarte, než neskôr nezrozumiteľnou SQL
# chybou "no such table" pri prvej požiadavke.
# =========================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    if not inspect(engine).has_table("alembic_version"):

        raise RuntimeError(
            "Databázová schéma nie je inicializovaná (chýba tabuľka "
            "alembic_version). Pred spustením appky spusti databázové "
            "migrácie:\n\n    alembic upgrade head\n"
        )

    yield


# =========================================
# APP
# =========================================

app = FastAPI(lifespan=lifespan)


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

# POZNÁMKA: /uploads (logo, podpis/pečiatka) sa NEpripája cez StaticFiles,
# pretože ten by bol dostupný bez prihlásenia. Namiesto toho ho obsluhuje
# chránená route nižšie (serve_upload) - viď routers/company.py by bolo
# logickejšie, ale kvôli jednoduchosti je priamo tu.


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
    quotes_router
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

    total_quotes = db.query(func.count(Quote.id)).scalar()

    overdue_invoices = (
        db
        .query(func.count(Invoice.id))
        .filter(
            Invoice.due_date < today,
            Invoice.status.notin_(CLOSED_INVOICE_STATUSES),
            Invoice.is_proforma.is_(False)
        )
        .scalar()
    )


    # =====================================
    # FINANCIE
    #
    # "Na inkaso" = faktúry v stave Odoslaná (Návrh sa nepočíta - ešte
    # nemusí byť reálne vystavená zákazníkovi; Uhradená/Stornovaná sú už
    # uzavreté). "Po splatnosti" je podmnožina tohto istého zoznamu,
    # kde už uplynul dátum splatnosti - viď invoice_utils.is_invoice_overdue.
    #
    # Súčty sa počítajú v Pythone (nie priamo v SQL), aby sme použili
    # tú istú, už otestovanú Decimal-presnú logiku (calculate_invoice_totals)
    # ako všade inde v appke. Dotazy sú ale zámerne ohraničené (len
    # odoslané / len tento mesiac / len tento rok), nie celá tabuľka -
    # v duchu rovnakej výkonovej opravy ako vyššie.
    # =====================================

    collection_invoices = (
        db
        .query(Invoice)
        .options(joinedload(Invoice.items))
        .filter(
            Invoice.status == InvoiceStatus.SENT.value,
            Invoice.is_proforma.is_(False)
        )
        .all()
    )

    collection_total = sum(
        (
            signed_invoice_total(invoice)
            for invoice in collection_invoices
        ),
        Decimal("0")
    )

    # Dobropis sa nikdy nepovažuje za "po splatnosti" - nikto nevymáha
    # platbu dobropisu, takže koncept "omeškania" preň nedáva zmysel.
    overdue_collection_invoices = [
        invoice
        for invoice in collection_invoices
        if not invoice.is_credit_note and is_invoice_overdue(invoice, today)
    ]

    overdue_total = sum(
        (
            calculate_invoice_totals(invoice.items)["total_gross"]
            for invoice in overdue_collection_invoices
        ),
        Decimal("0")
    )


    month_start = today.replace(day=1)

    # Tržby sa počítajú podľa DÁTUMU ÚHRADY, nie podľa dátumu vystavenia -
    # presnejšie odráža, kedy peniaze reálne prišli. Ako poistku pre
    # prípad, že by paid_date z nejakého dôvodu chýbal (nemalo by nastať,
    # ale radšej nespadnúť), sa použije dátum vystavenia ako náhrada.
    paid_date_expr = func.coalesce(Invoice.paid_date, Invoice.issue_date)

    paid_this_month = (
        db
        .query(Invoice)
        .options(joinedload(Invoice.items))
        .filter(
            Invoice.status == "Uhradená",
            paid_date_expr >= month_start,
            paid_date_expr <= today,
            Invoice.is_proforma.is_(False)
        )
        .all()
    )

    revenue_this_month = sum(
        (
            signed_invoice_total(invoice)
            for invoice in paid_this_month
        ),
        Decimal("0")
    )


    year_start = today.replace(month=1, day=1)

    paid_this_year = (
        db
        .query(Invoice)
        .options(joinedload(Invoice.items))
        .filter(
            Invoice.status == "Uhradená",
            paid_date_expr >= year_start,
            paid_date_expr <= today,
            Invoice.is_proforma.is_(False)
        )
        .all()
    )

    revenue_this_year = sum(
        (
            signed_invoice_total(invoice)
            for invoice in paid_this_year
        ),
        Decimal("0")
    )


    overdue_jobs_count = (
        db
        .query(func.count(Job.id))
        .filter(
            Job.due_date.isnot(None),
            Job.due_date < today,
            Job.status != "Hotová"
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

            "total_quotes": total_quotes,

            "overdue_invoices": overdue_invoices,

            "collection_total": collection_total,

            "collection_count": len(collection_invoices),

            "overdue_total": overdue_total,

            "overdue_count": len(overdue_collection_invoices),

            "revenue_this_month": revenue_this_month,

            "revenue_this_year": revenue_this_year,

            "overdue_jobs_count": overdue_jobs_count,

            "overdue_jobs": overdue_jobs,

            "today_jobs": today_jobs,

            "upcoming_jobs": upcoming_jobs,

            "today": today

        }

    )


# =========================================
# UPLOADY (logo, podpis/pečiatka) - CHRÁNENÉ
#
# Servuje sa vlastnou route (nie StaticFiles mount), aby to vyžadovalo
# prihlásenie. Názov súboru sa navyše prísne validuje - povolený je len
# vzor "logo.<prípona>" / "signature.<prípona>" s bezpečnou príponou,
# žiadne "/", "\" ani ".." (ochrana proti path traversal).
# =========================================

ALLOWED_UPLOAD_NAMES = {"logo", "signature"}
ALLOWED_UPLOAD_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@app.get("/uploads/{filename}")
def serve_upload(
    filename: str,
    user: str = Depends(require_login_page)
):

    stem = os.path.splitext(filename)[0]
    extension = os.path.splitext(filename)[1].lower()

    if stem not in ALLOWED_UPLOAD_NAMES or extension not in ALLOWED_UPLOAD_EXTENSIONS:

        raise HTTPException(
            status_code=404,
            detail="Súbor neexistuje"
        )


    candidate_path = (UPLOADS_DIR / filename).resolve()

    # Aj napriek kontrole vyššie si ešte overíme, že výsledná cesta
    # naozaj leží vnútri uploads/ priečinka (obrana do hĺbky).
    if UPLOADS_DIR.resolve() not in candidate_path.parents:

        raise HTTPException(
            status_code=404,
            detail="Súbor neexistuje"
        )


    if not candidate_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Súbor neexistuje"
        )


    return FileResponse(
        candidate_path,
        media_type=ALLOWED_UPLOAD_EXTENSIONS[extension]
    )

