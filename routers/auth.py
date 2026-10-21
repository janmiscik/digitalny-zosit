import hashlib
import hmac
import os
import shutil

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
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
    needs_rehash,
    register_failed_login,
    register_registration,
    register_successful_login,
    registration_retry_after,
    require_login_page,
    reset_request_allowed,
    verify_password,
)
from csrf import verify_csrf
import mailer
from templates_config import templates
import tenancy
from validators import validate_email_format


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

    retry_after = registration_retry_after(request)

    if retry_after:

        return error(
            "Z tejto adresy bolo založených príliš veľa kont. "
            f"Skús to znova o približne {max(1, retry_after // 60)} min.",
            status_code=429
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

    register_registration(request)

    login_user(request, account.slug, account.username, account.password_hash)

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

    locked, retry_after_seconds = is_login_locked(request, username)

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

        register_failed_login(request, username)

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

    register_successful_login(request, username)

    # Starý (slabší) hash sa pri úspešnom prihlásení potichu nahradí
    # novým - heslo je práve k dispozícii v čitateľnej podobe.
    if needs_rehash(account.password_hash):

        account.password_hash = hash_password(password)
        accounts_db.commit()

    login_user(request, account.slug, account.username, account.password_hash)

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


# =========================================
# ZMENA HESLA (prihlásený používateľ)
# =========================================

MAX_PASSWORD_LENGTH = 256


@router.post("/settings/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    account_slug: str = Depends(require_login_page),
    accounts_db: Session = Depends(get_accounts_db)
):

    account = (
        accounts_db
        .query(Account)
        .filter(Account.slug == account_slug)
        .first()
    )

    if account is None:

        raise HTTPException(status_code=401, detail="Konto neexistuje.")

    # Aj tu platí limit pokusov - ukradnutá session by inak mohla hádať
    # staré heslo donekonečna.
    locked, retry_after_seconds = is_login_locked(request, account.username)

    if locked:

        raise HTTPException(
            status_code=429,
            detail=(
                "Príliš veľa neúspešných pokusov. "
                f"Skús to znova o približne {max(1, retry_after_seconds // 60)} min."
            )
        )

    if not verify_password(current_password, account.password_hash):

        register_failed_login(request, account.username)
        _log_to_account_db(account.slug, "auth.password_change_failed")

        raise HTTPException(status_code=403, detail="Súčasné heslo nie je správne.")

    if len(new_password) < MIN_PASSWORD_LENGTH:

        raise HTTPException(
            status_code=422,
            detail=f"Nové heslo musí mať aspoň {MIN_PASSWORD_LENGTH} znakov."
        )

    if len(new_password) > MAX_PASSWORD_LENGTH:

        raise HTTPException(
            status_code=422,
            detail=f"Nové heslo môže mať najviac {MAX_PASSWORD_LENGTH} znakov."
        )

    if new_password != new_password_confirm:

        raise HTTPException(status_code=422, detail="Nové heslá sa nezhodujú.")

    if hmac.compare_digest(new_password, current_password):

        raise HTTPException(
            status_code=422,
            detail="Nové heslo musí byť iné ako súčasné."
        )

    account.password_hash = hash_password(new_password)
    accounts_db.commit()

    register_successful_login(request, account.username)

    # Nová session (nový CSRF token) - rovnako ako pri prihlásení.
    login_user(request, account.slug, account.username, account.password_hash)

    _log_to_account_db(account.slug, "auth.password_changed")

    return RedirectResponse(url="/settings?password=ok", status_code=303)


# =========================================
# E-MAIL KONTA (na obnovu hesla)
# =========================================

@router.post("/settings/email")
def set_account_email(
    request: Request,
    email: str = Form(""),
    current_password: str = Form(...),
    account_slug: str = Depends(require_login_page),
    accounts_db: Session = Depends(get_accounts_db)
):

    account = accounts_db.query(Account).filter(Account.slug == account_slug).first()

    if account is None:

        raise HTTPException(status_code=401, detail="Konto neexistuje.")

    locked, retry_after_seconds = is_login_locked(request, account.username)

    if locked:

        raise HTTPException(
            status_code=429,
            detail=(
                "Príliš veľa neúspešných pokusov. "
                f"Skús to znova o približne {max(1, retry_after_seconds // 60)} min."
            )
        )

    # Bez súčasného hesla by si ukradnutou session vedel nastaviť vlastný
    # e-mail a následne heslo obnoviť.
    if not verify_password(current_password, account.password_hash):

        register_failed_login(request, account.username)

        raise HTTPException(status_code=403, detail="Súčasné heslo nie je správne.")

    email = email.strip()

    if email:

        try:
            email = validate_email_format(email)

        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    account.email = email or None
    accounts_db.commit()

    _log_to_account_db(account.slug, "auth.email_changed", "nastavený" if email else "odstránený")

    return RedirectResponse(url="/settings?email=ok#heslo", status_code=303)


# =========================================
# OBNOVA ZABUDNUTÉHO HESLA (cez e-mail)
#
# Odkaz obsahuje podpísaný časovo obmedzený token (1 hodina), do ktorého
# je zahrnutý odtlačok súčasného hashu hesla - po zmene hesla preto
# prestane platiť (jednorazový). Odpoveď na žiadosť je vždy rovnaká,
# nech konto existuje alebo nie (nedá sa zistiť, ktoré mená sú
# zaregistrované). Funguje len keď je nastavený SMTP aj APP_BASE_URL.
# =========================================

RESET_TOKEN_MAX_AGE_SECONDS = 3600
RESET_NEUTRAL_MESSAGE = (
    "Ak konto s týmto menom existuje a má nastavený e-mail, poslali sme naň "
    "odkaz na zmenu hesla (platí 1 hodinu)."
)


def _reset_serializer() -> URLSafeTimedSerializer:

    return URLSafeTimedSerializer(os.environ["SECRET_KEY"], salt="password-reset")


def _password_fingerprint(password_hash: str) -> str:

    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:24]


def make_reset_token(account: Account) -> str:

    return _reset_serializer().dumps({"id": account.id, "f": _password_fingerprint(account.password_hash)})


def account_from_reset_token(token: str, accounts_db: Session) -> Account | None:
    """Vráti konto, ak je token platný, nevypršal a heslo sa od jeho vydania nezmenilo."""

    try:
        data = _reset_serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE_SECONDS)

    except (BadSignature, SignatureExpired):
        return None

    if not isinstance(data, dict):
        return None

    account = accounts_db.query(Account).filter(Account.id == data.get("id")).first()

    if account is None or not hmac.compare_digest(
        str(data.get("f", "")), _password_fingerprint(account.password_hash)
    ):
        return None

    return account


def _require_reset_enabled() -> None:

    if not mailer.enabled():

        raise HTTPException(status_code=404, detail="Obnova hesla cez e-mail nie je zapnutá.")


@router.get("/forgot-password")
def forgot_password_form(request: Request):

    _require_reset_enabled()

    return templates.TemplateResponse(
        request=request, name="forgot_password.html", context={"message": None, "error": None}
    )


def _send_reset_mail(account_slug: str, username: str, email: str, link: str) -> None:
    """Odošle e-mail s odkazom (beží na pozadí) a zapíše výsledok do histórie zmien."""

    sent = mailer.send_mail(
        email,
        "Obnova hesla - Digitálny zošit",
        (
            f"Dobrý deň,\n\nna obnovu hesla k účtu „{username}“ použite tento odkaz "
            f"(platí 1 hodinu a dá sa použiť raz):\n\n{link}\n\n"
            "Ak ste o obnovu nežiadali, tento e-mail ignorujte - heslo ostáva nezmenené.\n"
        )
    )

    _log_to_account_db(
        account_slug, "auth.password_reset_requested",
        "e-mail odoslaný" if sent else "odoslanie zlyhalo"
    )


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    background_tasks: BackgroundTasks,
    username: str = Form(...),
    accounts_db: Session = Depends(get_accounts_db)
):

    _require_reset_enabled()

    if not reset_request_allowed(request):

        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"message": None, "error": "Príliš veľa žiadostí. Skús to neskôr."},
            status_code=429
        )

    account = accounts_db.query(Account).filter(Account.username == username.strip()).first()

    if account is not None and account.email:

        link = f"{mailer.base_url()}/reset-password/{make_reset_token(account)}"

        # Odosielanie ide na pozadí: odpoveď trvá rovnako dlho, nech konto
        # existuje alebo nie (inak by sa zo času odpovede dalo zistiť, ktoré
        # mená sú zaregistrované).
        background_tasks.add_task(_send_reset_mail, account.slug, account.username, account.email, link)

    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={"message": RESET_NEUTRAL_MESSAGE, "error": None}
    )


@router.get("/reset-password/{token}")
def reset_password_form(
    token: str,
    request: Request,
    accounts_db: Session = Depends(get_accounts_db)
):

    _require_reset_enabled()

    valid = account_from_reset_token(token, accounts_db) is not None

    return templates.TemplateResponse(
        request=request,
        name="reset_password.html",
        context={
            "token": token,
            "valid": valid,
            "error": None if valid else "Odkaz je neplatný alebo vypršal. Vyžiadaj si nový."
        },
        status_code=200 if valid else 400
    )


@router.post("/reset-password/{token}")
def reset_password_submit(
    token: str,
    request: Request,
    new_password: str = Form(...),
    new_password_confirm: str = Form(...),
    accounts_db: Session = Depends(get_accounts_db)
):

    _require_reset_enabled()

    account = account_from_reset_token(token, accounts_db)

    def fail(message: str, status_code: int):

        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"token": token, "valid": account is not None, "error": message},
            status_code=status_code
        )

    if account is None:

        return fail("Odkaz je neplatný alebo vypršal. Vyžiadaj si nový.", 400)

    if len(new_password) < MIN_PASSWORD_LENGTH:

        return fail(f"Heslo musí mať aspoň {MIN_PASSWORD_LENGTH} znakov.", 422)

    if len(new_password) > MAX_PASSWORD_LENGTH:

        return fail(f"Heslo môže mať najviac {MAX_PASSWORD_LENGTH} znakov.", 422)

    if new_password != new_password_confirm:

        return fail("Heslá sa nezhodujú.", 422)

    account.password_hash = hash_password(new_password)
    accounts_db.commit()

    register_successful_login(request, account.username)      # zruší prípadný zámok prihlásenia

    _log_to_account_db(account.slug, "auth.password_reset_done")

    return RedirectResponse(url="/login?reset=ok", status_code=303)
