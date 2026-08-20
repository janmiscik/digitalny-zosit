import hashlib
import hmac
import os
import secrets

from dotenv import load_dotenv
from fastapi import HTTPException, Request, status


# =========================================
# KONFIGURÁCIA
# =========================================
# auth.py sa v main.py importuje pred database.py, preto si .env
# načítavame aj tu - inak by ADMIN_PASSWORD_HASH bol prázdny (poradie importov).

load_dotenv()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")


# =========================================
# HASHOVANIE HESLA (PBKDF2, stdlib, bez extra závislostí)
# =========================================

def hash_password(password: str) -> str:
    """
    Vytvorí hash hesla v tvare 'salt$hash' (oboje hex).
    Použi na vygenerovanie ADMIN_PASSWORD_HASH do .env.
    """

    salt = secrets.token_hex(16)

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000
    )

    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:

    if not stored_hash or "$" not in stored_hash:
        return False

    salt, hex_digest = stored_hash.split("$", 1)

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000
    )

    return hmac.compare_digest(derived.hex(), hex_digest)


# =========================================
# PRIHLÁSENIE / ODHLÁSENIE (session cookie)
# =========================================

def login_user(request: Request, username: str) -> None:
    request.session["user"] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def get_current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_login_page(request: Request) -> str:
    """
    Dependency pre stránky renderované cez Jinja2 (server-side HTML).
    Nepriateleného používateľa presmeruje na /login.
    """

    user = get_current_user(request)

    if user is None:

        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"}
        )

    return user


def require_login_api(request: Request) -> str:
    """
    Dependency pre JSON API endpointy (napr. GET /customers, GET /jobs).
    Nepriateleného používateľa vráti ako 401 JSON namiesto redirectu.
    """

    user = get_current_user(request)

    if user is None:

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neprihlásený používateľ"
        )

    return user
