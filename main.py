import logging
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, inspect, text
from sqlalchemy.orm import Session, joinedload


# =========================================
# LOGOVANIE
#
# Bez tohto by `logger.exception(...)` volania v appke (napr. pri
# zlyhaní zmeny veľkosti fotky) síce niečo vypísali - Python má pre
# nenakonfigurovaný root logger "posledná záchrana" handler, ktorý
# WARNING a vyššie vypíše na stderr - ale bez času, mena modulu ani
# konzistentného formátu, takže by sa to v produkčnom logu ťažko
# hľadalo. LOG_LEVEL je nastaviteľný cez premennú prostredia, keby
# bolo treba dočasne zvýšiť podrobnosť (napr. na DEBUG) bez zásahu
# do kódu.
# =========================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)

import log_filters
import monitoring
from security_config import validate_secret_key
from accounts_db import accounts_engine, get_accounts_db
from accounts_models import Account
from auto_backup import (
    recover_interrupted_restores_on_startup,
    start_scheduler,
    stop_scheduler,
    upgrade_accounts_on_startup,
)
from auth import require_login_page
from dashboard_utils import (
    first_day_of_month_back,
    monthly_revenue_series,
    overdue_invoice_rows,
    revenue_paid_date,
)
from database import Base, get_db
from invoice_utils import CLOSED_INVOICE_STATUSES, calculate_invoice_totals, is_invoice_overdue, signed_invoice_total

from models import Customer, Invoice, Job, Quote
from schemas import InvoiceStatus

from routers.auth import router as auth_router
from routers.company import router as company_router
from routers.customers import router as customers_router
from routers.invoices import router as invoices_router
from routers.jobs import router as jobs_router
from routers.quotes import router as quotes_router
from routers.export import router as export_router
from routers.reminders import router as reminders_router

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
#
# Appka má DVE oddelené databázy (viď accounts_db.py a tenancy.py):
# jednu MASTER databázu kont (accounts.db) a pre KAŽDÉ konto vlastný
# súbor s business dátami. Pri štarte appky vieme skontrolovať len tú
# prvú - business databázy jednotlivých kont vznikajú až pri ich
# registrácii (tenancy.provision_account), appka pri štarte ešte
# nevie, koľko ich bude.
# =========================================

@asynccontextmanager
async def lifespan(app: FastAPI):

    if not inspect(accounts_engine).has_table("alembic_version"):

        raise RuntimeError(
            "Databáza kont nie je inicializovaná (chýba tabuľka "
            "alembic_version). Pred spustením appky spusti migrácie:\n\n"
            "    alembic -c alembic_accounts.ini upgrade head\n"
        )

    # Najprv vrátiť konto po prípadne prerušenej obnove (pád procesu
    # uprostred výmeny dát), až potom migrovať.
    recover_interrupted_restores_on_startup()

    # Kontám s čakajúcou migráciou schémy ju doplní (lokálny beh po
    # aktualizácii kódu; na Railway to robí aj start.sh).
    upgrade_accounts_on_startup()

    # Automatické zálohy kont na pozadí (viď auto_backup.py). Vypnuté v
    # testoch cez BACKUP_ENABLED=false (tests/conftest.py).
    start_scheduler()

    try:

        yield

    finally:

        stop_scheduler()


# =========================================
# APP
# =========================================

# Interaktívna dokumentácia API (/docs, /redoc, /openapi.json) je verejná
# a vyratúva všetky trasy appky - v produkcii je vypnutá. Na vývoj ju
# zapne ENABLE_API_DOCS=true.
_docs_enabled = os.getenv("ENABLE_API_DOCS", "false").strip().lower() in ("true", "1", "yes")

app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)


# Token z odkazu na obnovu hesla sa nesmie zapisovať do prístupových logov.
log_filters.install()

SECRET_KEY = os.getenv("SECRET_KEY")

# Session cookie sa štandardne posiela iba cez HTTPS (https_only=True).
# Lokálne pri vývoji cez obyčajné http://localhost by to zablokovalo
# prihlásenie, preto sa dá dočasne vypnúť cez SESSION_HTTPS_ONLY=false
# v .env - v produkcii (Railway) musí ostať zapnuté.
SESSION_HTTPS_ONLY = os.getenv("SESSION_HTTPS_ONLY", "true").strip().lower() not in (
    "false", "0", "no",
)

# V produkcii (HTTPS-only) sa appka so slabým alebo predvoleným SECRET_KEY
# nespustí; lokálne len zaloguje varovanie (viď security_config.py).
validate_secret_key(SECRET_KEY, enforce=SESSION_HTTPS_ONLY)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)


# =========================================
# BEZPEČNOSTNÉ HLAVIČKY
#
# Pridané ku každej odpovedi. Content-Security-Policy sa posiela len k HTML
# stránkam. Je "praktická", nie striktná: šablóny majú inline skripty,
# onclick handlery a inline štýly, preto je povolené 'unsafe-inline'.
# Chráni pred načítaním skriptov/štýlov/obrázkov z cudzích domén,
# odoslaním formulára inam, vložením do cudzieho rámu, <object> a <base>.
# Jediný povolený externý zdroj sú písma Google Fonts (static/style.css).
# Striktná CSP (bez 'unsafe-inline') by vyžadovala presun všetkých
# inline skriptov a handlerov do súborov v static/.
# =========================================

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}

if SESSION_HTTPS_ONLY:
    SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000"


CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


SENSITIVE_URL_PREFIXES = ("/reset-password/", "/forgot-password")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):

    response = await call_next(request)

    # Stránky obnovy hesla nesú token v URL: nesmú sa ukladať do cache
    # prehliadača ani proxy a prehliadač nesmie posielať adresu ako Referer.
    if request.url.path.startswith(SENSITIVE_URL_PREFIXES):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"

    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)

    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)

    return response


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


# =========================================
# NEOŠETRENÉ CHYBY (500) - programátorské chyby, nie HTTPException
#
# Bez tohto handlera by neošetrená výnimka (skutočná chyba v kóde, nie
# HTTPException, ktorú appka vyhadzuje zámerne) skončila ako holá
# "Internal Server Error" stránka bez štýlu appky A BEZ ZÁPISU DO LOGU
# - v produkcii by sa tak appka dala potichu "rozbiť" a nikto by sa
# o tom nedozvedel, kým by sa na to niekto náhodou nesťažoval.
#
# Registrácia handlera na `Exception` (základnú triedu) je oficiálny
# spôsob, ako v Starlette/FastAPI zachytiť AJ tieto neošetrené chyby -
# HTTPException má vlastný, špecifickejší handler vyššie a sem
# nepríde (Starlette si vyberie presnejšie sedliaci handler).
#
# Používateľovi sa detail výnimky NEUKAZUJE (mohol by obsahovať
# interné detaily, napr. časť SQL dotazu) - len všeobecná hláška.
# Skutočný text a traceback ide len do logu.
# =========================================

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):

    logger.exception(
        "Neošetrená chyba pri %s %s",
        request.method,
        request.url.path
    )

    wants_html = "text/html" in request.headers.get("accept", "")

    if not wants_html:

        return JSONResponse(
            status_code=500,
            content={"detail": "Nastala neočakávaná chyba. Skús to prosím znova."}
        )


    referer = request.headers.get("referer", "/")


    return templates.TemplateResponse(

        request=request,

        name="error.html",

        status_code=500,

        context={

            "status_code": 500,

            "detail": "Nastala neočakávaná chyba. Skús to prosím znova.",

            "back_url": referer

        }

    )


app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static"
)

from uploads_utils import uploads_dir

# POZNÁMKA: tu už NIE JE volanie ensure_uploads_dir() - v appke s
# jedným spoločným priečinkom to dávalo zmysel pri štarte appky, teraz
# má každé konto vlastný priečinok, ktorý vznikne až pri jeho
# registrácii (tenancy.provision_account) - appka pri štarte ešte
# nevie, koľko kont bude.

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
    reminders_router
)

app.include_router(
    export_router
)

app.include_router(
    company_router
)


# =========================================
# HEALTH CHECK
#
# Verejný (bez prihlásenia) endpoint pre Railway healthcheck
# (railway.json -> healthcheckPath). Overí, že appka beží A že vie
# čítať master databázu kont (t. j. že je pripojený disk/volume).
# Nevracia žiadne dáta o kontách ani o konfigurácii.
# =========================================

@app.get("/health", include_in_schema=False)
def health():

    try:

        with accounts_engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    except Exception:

        logger.exception("Health check: databáza kont nie je dostupná")

        return JSONResponse(
            {"status": "error"},
            status_code=503
        )

    return {"status": "ok"}


@app.get("/health/details", include_in_schema=False)
def health_details(
    request: Request,
    accounts_db: Session = Depends(get_accounts_db),
):
    """
    Podrobný stav záloh a migrácií pre externý monitoring. Bez nastaveného
    MONITOR_TOKEN neexistuje (404); token sa posiela v hlavičke
    `Authorization: Bearer <token>` (alebo ako `?token=` pre nástroje, ktoré
    hlavičky nevedia - ten sa však môže objaviť v logoch).
    """

    if not monitoring.monitor_token():

        raise HTTPException(status_code=404, detail="Not Found")

    header = request.headers.get("authorization", "")
    provided = header[7:].strip() if header.lower().startswith("bearer ") else request.query_params.get("token")

    if not monitoring.token_matches(provided):

        raise HTTPException(status_code=401, detail="Unauthorized")

    slugs = [row.slug for row in accounts_db.query(Account.slug).order_by(Account.id).all()]

    report = monitoring.collect(slugs)

    return JSONResponse(report, status_code=200 if report["status"] == "ok" else 503)


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
        .options(joinedload(Invoice.items), joinedload(Invoice.customer))
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

    year_start = today.replace(month=1, day=1)

    last_month_start = first_day_of_month_back(today, 1)

    # Tržby sa počítajú podľa DÁTUMU ÚHRADY, nie podľa dátumu vystavenia -
    # presnejšie odráža, kedy peniaze reálne prišli. Ako poistku pre
    # prípad, že by paid_date z nejakého dôvodu chýbal (nemalo by nastať,
    # ale radšej nespadnúť), sa použije dátum vystavenia ako náhrada
    # (viď dashboard_utils.revenue_paid_date).
    #
    # Jeden dotaz pokrýva všetko, čo dashboard potrebuje (tento mesiac,
    # minulý mesiac, tento rok aj graf za 6 mesiacov): od začiatku
    # roka, resp. od začiatku 6-mesačného okna, podľa toho, čo je skôr.
    paid_date_expr = func.coalesce(Invoice.paid_date, Invoice.issue_date)

    revenue_range_start = min(
        year_start,
        first_day_of_month_back(today, 5)
    )

    paid_invoices = (
        db
        .query(Invoice)
        .options(joinedload(Invoice.items))
        .filter(
            Invoice.status == "Uhradená",
            paid_date_expr >= revenue_range_start,
            paid_date_expr <= today,
            Invoice.is_proforma.is_(False)
        )
        .all()
    )

    def revenue_between(start, end):

        return sum(
            (
                signed_invoice_total(invoice)
                for invoice in paid_invoices
                if start <= revenue_paid_date(invoice) <= end
            ),
            Decimal("0")
        )

    revenue_this_month = revenue_between(month_start, today)

    revenue_last_month = revenue_between(
        last_month_start,
        month_start - timedelta(days=1)
    )

    revenue_this_year = revenue_between(year_start, today)

    revenue_series = monthly_revenue_series(paid_invoices, today, months=6)

    revenue_series_total = sum(
        (item["total"] for item in revenue_series),
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

            "revenue_last_month": revenue_last_month,

            "revenue_series": revenue_series,

            "revenue_series_total": revenue_series_total,

            "overdue_invoice_rows": overdue_invoice_rows(
                overdue_collection_invoices,
                today
            ),

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


    candidate_path = (uploads_dir() / filename).resolve()

    # Aj napriek kontrole vyššie si ešte overíme, že výsledná cesta
    # naozaj leží vnútri uploads/ priečinka (obrana do hĺbky).
    if uploads_dir().resolve() not in candidate_path.parents:

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

