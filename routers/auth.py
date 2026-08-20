import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from auth import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    login_user,
    logout_user,
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

    valid_username = hmac.compare_digest(username, ADMIN_USERNAME)
    valid_password = verify_password(password, ADMIN_PASSWORD_HASH)

    if not (valid_username and valid_password):

        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "Nesprávne meno alebo heslo"
            },
            status_code=401
        )

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
