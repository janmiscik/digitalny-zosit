import hashlib
import hmac
import os
import secrets
import time

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


# =========================================
# RATE LIMITING PRIHLÁSENIA (in-memory, jeden proces)
# =========================================
# Appka má vždy len jedného používateľa (živnostník/remeselník) a jednu
# prihlasovaciu stránku, takže limit je zámerne GLOBÁLNY pre celú appku,
# nie podľa IP adresy klienta. Dôvod: appka môže bežať za rôznymi proxy/
# reverse-proxy nastaveniami (a v testoch aj cez rôzne loopback adresy,
# napr. 127.0.0.1 vs ::1), kde by sledovanie podľa IP bolo nespoľahlivé.
# Keďže legitímne existuje len jeden používateľ, globálny limit rieši
# presne to, čo má - ochranu pred hrubou silou na to jediné heslo - bez
# rizika, že sa útočník "schová" za inú IP a limit obíde.
#
# Stav sa drží len v pamäti procesu - pri reštarte appky sa vynuluje,
# čo je pre tento use-case v poriadku. Žiadna DB, žiadna nová závislosť.

MAX_LOGIN_ATTEMPTS = 5        # koľko zlých pokusov je tolerovaných
LOGIN_WINDOW_SECONDS = 300    # v akom okne sa pokusy počítajú (5 min)
LOGIN_LOCKOUT_SECONDS = 300   # na ako dlho sa prihlásenie po limite zamkne

_failed_login_attempts: list[float] = []
_lockout_until: float | None = None


def is_login_locked() -> tuple[bool, int]:
    """
    Vráti (je_zamknuté, sekúnd_do_odomknutia).
    """

    global _lockout_until

    if _lockout_until is None:
        return False, 0

    remaining = _lockout_until - time.time()

    if remaining <= 0:
        # zámka vypršala - vyčistíme záznamy, nech appka dostane čistý štart
        _lockout_until = None
        _failed_login_attempts.clear()
        return False, 0

    return True, int(remaining) + 1


def register_failed_login() -> None:
    """Zaznamená neúspešný pokus a prípadne prihlásenie zamkne."""

    global _lockout_until

    now = time.time()

    while _failed_login_attempts and now - _failed_login_attempts[0] >= LOGIN_WINDOW_SECONDS:
        _failed_login_attempts.pop(0)

    _failed_login_attempts.append(now)

    if len(_failed_login_attempts) >= MAX_LOGIN_ATTEMPTS:
        _lockout_until = now + LOGIN_LOCKOUT_SECONDS


def register_successful_login() -> None:
    """Po úspešnom prihlásení vyčistí históriu zlyhaní."""

    global _lockout_until

    _failed_login_attempts.clear()
    _lockout_until = None
