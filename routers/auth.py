import hmac

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from audit_log import log_action
from auth import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    is_login_locked,
    login_user,
    logout_user,
    register_failed_login,
    register_successful_login,
    verify_password,
)
from csrf import verify_csrf
from database import get_db
from templates_config import templates


router = APIRouter(dependencies=[Depends(verify_csrf)])


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
    db: Session = Depends(get_db)
):

    locked, retry_after_seconds = is_login_locked()

    if locked:

        retry_minutes = max(1, retry_after_seconds // 60)

        log_action(
            db,
            "auth.login_failed",
            entity_type="auth",
            detail="Zamietnuté - prihlásenie dočasne zablokované po viacerých zlyhaniach"
        )
        db.commit()

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

    valid_username = hmac.compare_digest(username, ADMIN_USERNAME)
    valid_password = verify_password(password, ADMIN_PASSWORD_HASH)

    if not (valid_username and valid_password):

        register_failed_login()

        # Zámerne nelogujeme zadané meno/heslo - len fakt neúspešného
        # pokusu, nech log nikdy neobsahuje citlivé prihlasovacie údaje.
        log_action(
            db,
            "auth.login_failed",
            entity_type="auth",
            detail="Nesprávne meno alebo heslo"
        )
        db.commit()

        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Nesprávne meno alebo heslo"
            },
            status_code=401
        )

    register_successful_login()
    login_user(request, username)

    log_action(db, "auth.login_success", entity_type="auth")
    db.commit()

    return RedirectResponse(
        url="/",
        status_code=303
    )


# =========================================
# LOGOUT
# =========================================

@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):

    logout_user(request)

    log_action(db, "auth.logout", entity_type="auth")
    db.commit()

    return RedirectResponse(
        url="/login",
        status_code=303
    )
