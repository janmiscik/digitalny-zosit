import hmac
import shutil

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from accounts_db import get_accounts_db
from accounts_models import Account
from audit_log import log_action
from auth import (
    hash_password,
    is_login_locked,
    login_user,
    logout_user,
    register_failed_login,
    register_successful_login,
    verify_password,
)
from csrf import verify_csrf
from templates_config import templates
import tenancy


router = APIRouter(dependencies=[Depends(verify_csrf)])


# Platný PBKDF2-tvarovaný hash bez zodpovedajúceho hesla - použije sa
# pri neexistujúcom username, aby overenie hesla trvalo rovnako dlho
# ako pri existujúcom konte (bez tohto by bolo z času odpovede badateľné,
# ktoré username v appke existuje).
_DUMMY_PASSWORD_HASH = hash_password(hash_password(""))

MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 50
MIN_PASSWORD_LENGTH = 8


def _log_to_account_db(account_slug: str, action: str, detail: str | None = None) -> None:
    """
    Audit log pre prihlasovacie udalosti sa zapisuje do databázy
    KONKRÉTNEHO konta (audit_log.log_action to tak robí vždy - appka
    predtým mala len jednu databázu, teraz treba konto nájsť explicitne,
    lebo get_db() v request kontexte login/register handleru ešte
    nemusí vedieť, o koho ide).
    """

    engine = tenancy.get_account_engine(account_slug)
    AccountSessionLocal = sessionmaker(bind=engine)
    db = AccountSessionLocal()

    try:
        log_action(db, action, entity_type="auth", detail=detail)
        db.commit()

    finally:
        db.close()


# =========================================
# REGISTRÁCIA - FORM
# =========================================

@router.get("/register")
def register_form(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "error": None
        }
    )


# =========================================
# REGISTRÁCIA - SUBMIT
# =========================================

@router.post("/register")
def register_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    accounts_db: Session = Depends(get_accounts_db)
):

    username = username.strip()

    def error(message: str, status_code: int = 422):
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"error": message},
            status_code=status_code
        )

    if not (MIN_USERNAME_LENGTH <= len(username) <= MAX_USERNAME_LENGTH):

        return error(
            f"Používateľské meno musí mať {MIN_USERNAME_LENGTH} až "
            f"{MAX_USERNAME_LENGTH} znakov."
        )

    if len(password) < MIN_PASSWORD_LENGTH:

        return error(
            f"Heslo musí mať aspoň {MIN_PASSWORD_LENGTH} znakov."
        )

    if password != password_confirm:

        return error("Heslá sa nezhodujú.")

    existing = (
        accounts_db
        .query(Account)
        .filter(Account.username == username)
        .first()
    )

    if existing is not None:

        return error("Toto používateľské meno je už obsadené.")

    base_slug = tenancy.slugify_username(username)

    def slug_exists(candidate: str) -> bool:
        return (
            accounts_db
            .query(Account)
            .filter(Account.slug == candidate)
            .first()
            is not None
        )

    account = None

    # Rovnaký princíp opakovania pri kolízii ako pri názve bezpečnostnej
    # zálohy (backup_utils.save_automatic_backup) - ensure_unique_slug
    # kontroluje kolíziu len voči tomu, čo accounts_db vidела PRED touto
    # požiadavkou; pri naozaj súbežnej registrácii rovnakého mena by
    # databázové UNIQUE obmedzenie na slug odchytilo zvyšok.
    for _attempt in range(5):

        slug = tenancy.ensure_unique_slug(base_slug, slug_exists)

        account = Account(
            username=username,
            slug=slug,
            password_hash=hash_password(password)
        )

        accounts_db.add(account)

        try:

            accounts_db.commit()
            break

        except IntegrityError:

            accounts_db.rollback()
            account = None

    if account is None:

        return error(
            "Registrácia sa nepodarila, skús to prosím znova.",
            status_code=500
        )

    try:

        tenancy.provision_account(account.slug)

    except Exception:

        # Databáza/priečinok konta sa nepodarilo pripraviť - konto bez
        # funkčných dát by bolo nepoužiteľné, takže ho radšej zrušíme
        # (kompenzačná akcia), než aby ostalo "napoly zaregistrované".
        accounts_db.delete(account)
        accounts_db.commit()

        shutil.rmtree(tenancy.account_dir(account.slug), ignore_errors=True)

        return error(
            "Konto sa nepodarilo pripraviť, skús to prosím znova.",
            status_code=500
        )

    login_user(request, account.slug, account.username)

    _log_to_account_db(account.slug, "auth.account_created")
    _log_to_account_db(account.slug, "auth.login_success")

    return RedirectResponse(
        url="/",
        status_code=303
    )


# =========================================
# LOGIN - FORM
# =========================================

@router.get("/login")
def login_form(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "error": None
        }
    )


# =========================================
# LOGIN - SUBMIT
# =========================================

@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    accounts_db: Session = Depends(get_accounts_db)
):

    locked, retry_after_seconds = is_login_locked(request)

    if locked:

        retry_minutes = max(1, retry_after_seconds // 60)

        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": (
                    "Príliš veľa neúspešných pokusov o prihlásenie. "
                    f"Skúste to znova o približne {retry_minutes} min."
                )
            },
            status_code=429
        )

    account = (
        accounts_db
        .query(Account)
        .filter(Account.username == username.strip())
        .first()
    )

    # Overenie hesla sa vykoná VŽDY (aj pri neexistujúcom username,
    # proti dummy hashu) - konštantný čas bez ohľadu na to, či účet
    # existuje, nech appka cez čas odpovede neprezradí, ktoré mená sú
    # zaregistrované.
    password_hash = account.password_hash if account else _DUMMY_PASSWORD_HASH
    valid_password = verify_password(password, password_hash)

    if account is None or not valid_password:

        register_failed_login(request)

        # Zámerne nelogujeme zadané meno/heslo - len fakt neúspešného
        # pokusu. Logovať je kam len vtedy, keď username skutočne
        # patrí nejakému kontu (inak niet kam zapísať).
        if account is not None:
            _log_to_account_db(account.slug, "auth.login_failed")

        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Nesprávne meno alebo heslo"
            },
            status_code=401
        )

    register_successful_login(request)
    login_user(request, account.slug, account.username)

    _log_to_account_db(account.slug, "auth.login_success")

    return RedirectResponse(
        url="/",
        status_code=303
    )


# =========================================
# LOGOUT
# =========================================

@router.post("/logout")
def logout(request: Request):

    account_slug = request.session.get("account_slug")

    if account_slug:
        _log_to_account_db(account_slug, "auth.logout")

    logout_user(request)

    return RedirectResponse(
        url="/login",
        status_code=303
    )
