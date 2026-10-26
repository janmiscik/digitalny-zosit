"""
Správa kont majiteľom servera: zoznam kont, založenie konta, nové heslo.

Prístup majú len kontá uvedené v SERVER_ADMINS (mená oddelené čiarkou). Ostatným
(aj prihláseným) stránka vracia 404, aby sa nedala ani zistiť. Bez nastaveného
SERVER_ADMINS neexistuje pre nikoho. Heslá sa nikde nezapisujú ani nelogujú.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

import account_service
import auto_backup
import tenancy
from accounts_db import get_accounts_db
from accounts_models import Account
from audit_log import log_action
from auth import require_login_page
from csrf import verify_csrf
from database import get_db
from templates_config import templates

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_csrf)])


def require_server_admin(
    user: str = Depends(require_login_page),
    accounts_db: Session = Depends(get_accounts_db),
) -> Account:

    account = accounts_db.query(Account).filter(Account.slug == user).first()

    if account is None or not account_service.is_server_admin(account.username):
        raise HTTPException(status_code=404, detail="Not Found")

    return account


def _directory_size(path: Path) -> int:

    total = 0

    try:

        for item in path.rglob("*"):

            try:

                if item.is_file():
                    total += item.stat().st_size

            except OSError:
                continue

    except OSError:
        return total

    return total


def _rows(accounts_db: Session) -> list[dict]:

    rows = []

    for account in accounts_db.query(Account).order_by(Account.id).all():

        status = auto_backup.read_status(account.slug)

        rows.append({
            "username": account.username,
            "slug": account.slug,
            "created_at": account.created_at,
            "last_backup": status.get("last_success_at"),
            "size_mb": round(_directory_size(tenancy.account_dir(account.slug)) / (1024 * 1024), 1),
        })

    return rows


def _render(request: Request, accounts_db: Session, admin: Account, *, error=None, form_username="",
            error_slug=None, message=None, status_code=200):

    return templates.TemplateResponse(
        request=request,
        name="admin_accounts.html",
        status_code=status_code,
        context={
            "rows": _rows(accounts_db),
            "admin_slug": admin.slug,
            "error": error,
            "error_slug": error_slug,
            "form_username": form_username,
            "message": message,
            "min_password_length": account_service.MIN_PASSWORD_LENGTH,
        },
    )


MESSAGES = {
    "created": "Konto bolo založené.",
    "password": "Heslo konta bolo zmenené, doterajšie prihlásenia konta prestali platiť.",
}


@router.get("/admin/accounts")
def accounts_page(
    request: Request,
    ok: str = "",
    admin: Account = Depends(require_server_admin),
    accounts_db: Session = Depends(get_accounts_db),
):

    return _render(request, accounts_db, admin, message=MESSAGES.get(ok))


@router.post("/admin/accounts")
def create_account(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    admin: Account = Depends(require_server_admin),
    accounts_db: Session = Depends(get_accounts_db),
    db: Session = Depends(get_db),
):

    if password != password_confirm:

        return _render(request, accounts_db, admin, error="Heslá sa nezhodujú.",
                       form_username=username, status_code=422)

    result = account_service.create_account(accounts_db, username, password)

    if result.error:

        return _render(request, accounts_db, admin, error=result.error,
                       form_username=username, status_code=422)

    log_action(db, "admin.account_created", entity_type="account", detail=f"Založené konto {result.account.username}")
    db.commit()

    return RedirectResponse(url="/admin/accounts?ok=created", status_code=303)


@router.post("/admin/accounts/{slug}/password")
def change_password(
    slug: str,
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    admin: Account = Depends(require_server_admin),
    accounts_db: Session = Depends(get_accounts_db),
    db: Session = Depends(get_db),
):

    target = accounts_db.query(Account).filter(Account.slug == slug).first()

    if target is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if target.slug == admin.slug:

        return _render(request, accounts_db, admin, error_slug=slug, status_code=422,
                       error="Svoje heslo zmeň v Nastaveniach.")

    if password != password_confirm:

        return _render(request, accounts_db, admin, error_slug=slug, status_code=422,
                       error="Heslá sa nezhodujú.")

    result = account_service.set_password(accounts_db, slug, password)

    if result.error:

        return _render(request, accounts_db, admin, error_slug=slug, status_code=422, error=result.error)

    log_action(db, "admin.password_set", entity_type="account", detail=f"Nové heslo pre konto {target.username}")
    db.commit()

    return RedirectResponse(url="/admin/accounts?ok=password", status_code=303)
