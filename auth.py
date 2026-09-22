import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException, Request, status


# =========================================
# KONFIGURÁCIA
# =========================================

# .env hľadáme priamo v koreňovom adresári projektu,
# teda vedľa auth.py.
ENV_FILE = Path(__file__).resolve().parent / ".env"

# Pre túto single-user aplikáciu chceme, aby konfigurácia
# z .env bola jednoznačne použitá.
load_dotenv(dotenv_path=ENV_FILE, override=True)

from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()


# =========================================
# HASHOVANIE HESLA
# =========================================

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """
    Vytvorí hash hesla v tvare:

        salt$hash

    Použi napríklad na vygenerovanie ADMIN_PASSWORD_HASH
    do .env.
    """

    salt = secrets.token_hex(16)

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )

    return f"{salt}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """
    Overí heslo proti uloženému PBKDF2 hashu.

    Pri neplatnom alebo poškodenom hashi jednoducho vráti False.
    """

    if not stored_hash or "$" not in stored_hash:
        return False

    salt, hex_digest = stored_hash.split("$", 1)

    if not salt or not hex_digest:
        return False

    try:
        # Očakávame 16-byte salt uložený ako hex = 32 znakov.
        if len(salt) != 32:
            return False

        bytes.fromhex(salt)

        # SHA-256 digest v hex forme = 64 znakov.
        if len(hex_digest) != 64:
            return False

        bytes.fromhex(hex_digest)

    except ValueError:
        return False

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )

    return hmac.compare_digest(derived.hex(), hex_digest)


# =========================================
# PRIHLÁSENIE / ODHLÁSENIE
# =========================================

def login_user(request: Request, username: str) -> None:
    request.session["user"] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def get_current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_login_page(request: Request) -> str:
    """
    Dependency pre stránky renderované cez Jinja2.
    Neprihláseného používateľa presmeruje na /login.
    """

    user = get_current_user(request)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )

    return user


def require_login_api(request: Request) -> str:
    """
    Dependency pre JSON API endpointy.
    Neprihláseného používateľa vráti ako 401 JSON.
    """

    user = get_current_user(request)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neprihlásený používateľ",
        )

    return user


# =========================================
# RATE LIMITING PRIHLÁSENIA
# =========================================

MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCKOUT_SECONDS = 300

_failed_login_attempts: list[float] = []
_lockout_until: float | None = None


def is_login_locked() -> tuple[bool, int]:
    """
    Vráti:

        (True, počet sekúnd)
    
    ak je login zamknutý.
    """

    global _lockout_until

    if _lockout_until is None:
        return False, 0

    remaining = _lockout_until - time.time()

    if remaining <= 0:
        _lockout_until = None
        _failed_login_attempts.clear()
        return False, 0

    return True, int(remaining) + 1


def register_failed_login() -> None:
    """Zaznamená neúspešný pokus."""

    global _lockout_until

    now = time.time()

    while (
        _failed_login_attempts
        and now - _failed_login_attempts[0] >= LOGIN_WINDOW_SECONDS
    ):
        _failed_login_attempts.pop(0)

    _failed_login_attempts.append(now)

    if len(_failed_login_attempts) >= MAX_LOGIN_ATTEMPTS:
        _lockout_until = now + LOGIN_LOCKOUT_SECONDS


def register_successful_login() -> None:
    """Po úspešnom prihlásení vyčistí históriu zlyhaní."""

    global _lockout_until

    _failed_login_attempts.clear()
    _lockout_until = None