import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

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
from templates_config import templates


router = APIRouter()


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
    password: str = Form(...)
):

    locked, retry_after_seconds = is_login_locked()

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

    valid_username = hmac.compare_digest(username, ADMIN_USERNAME)
    valid_password = verify_password(password, ADMIN_PASSWORD_HASH)

    if not (valid_username and valid_password):

        register_failed_login()

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

    return RedirectResponse(
        url="/",
        status_code=303
    )


# =========================================
# LOGOUT
# =========================================

@router.post("/logout")
def logout(request: Request):

    logout_user(request)

    return RedirectResponse(
        url="/login",
        status_code=303
    )
