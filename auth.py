import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request, status

import tenancy


# =========================================
# HASHOVANIE HESLA
# =========================================

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """
    Vytvorí hash hesla v tvare:

        salt$hash
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
        if len(salt) != 32:
            return False

        bytes.fromhex(salt)

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
#
# V session sa ukladá `account_slug` (priečinok konta na disku - viď
# tenancy.py), nie rovno username - slug sa pri zmene username nemení,
# takže appka ho môže použiť priamo na nájdenie databázy konta bez
# ďalšieho dotazu do accounts.db pri KAŽDEJ požiadavke.
# =========================================

def login_user(request: Request, account_slug: str, username: str) -> None:
    request.session["account_slug"] = account_slug
    request.session["username"] = username


def logout_user(request: Request) -> None:
    request.session.clear()


def get_current_account_slug(request: Request) -> str | None:
    return request.session.get("account_slug")


def require_login_page(request: Request) -> str:
    """
    Dependency pre stránky renderované cez Jinja2.
    Neprihláseného používateľa presmeruje na /login.

    Vracia account_slug (nie username) - je to to, čo ostatné časti
    appky (napr. get_db) potrebujú na nájdenie dát tohto konta.
    """

    account_slug = get_current_account_slug(request)

    if account_slug is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )

    tenancy.set_current_tenant(account_slug)

    return account_slug


def require_login_api(request: Request) -> str:
    """
    Dependency pre JSON API endpointy.
    Neprihláseného používateľa vráti ako 401 JSON.
    """

    account_slug = get_current_account_slug(request)

    if account_slug is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neprihlásený používateľ",
        )

    tenancy.set_current_tenant(account_slug)

    return account_slug


# =========================================
# RATE LIMITING PRIHLÁSENIA
#
# KĽÚČOVANÉ PODĽA IP ADRESY - v appke s viacerými kontami by jeden
# globálny (nekľúčovaný) limit znamenal, že útočník skúšajúci heslo k
# JEDNÉMU kontu by dočasne zamkol prihlásenie VŠETKÝM kontám appky.
# Limit teda platí len pre danú IP adresu, nie naprieč appkou - presne
# tak, ako to bolo pôvodne myslené pri appke s jedným používateľom.
# =========================================

MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCKOUT_SECONDS = 300

_failed_login_attempts: dict[str, list[float]] = {}
_lockout_until: dict[str, float] = {}


def _rate_limit_key(request: Request) -> str:
    """IP adresa klienta - pri appke za reverse proxy by sem v
    budúcnosti mohlo byť treba doplniť X-Forwarded-For, zatiaľ appka
    beží priamo."""

    return request.client.host if request.client else "unknown"


def is_login_locked(request: Request) -> tuple[bool, int]:
    """
    Vráti:

        (True, počet sekúnd)

    ak je prihlásenie z tejto IP adresy dočasne zablokované.
    """

    key = _rate_limit_key(request)
    until = _lockout_until.get(key)

    if until is None:
        return False, 0

    remaining = until - time.time()

    if remaining <= 0:
        _lockout_until.pop(key, None)
        _failed_login_attempts.pop(key, None)
        return False, 0

    return True, int(remaining) + 1


def register_failed_login(request: Request) -> None:
    """Zaznamená neúspešný pokus pre IP adresu tejto požiadavky."""

    key = _rate_limit_key(request)
    now = time.time()

    attempts = _failed_login_attempts.setdefault(key, [])

    while attempts and now - attempts[0] >= LOGIN_WINDOW_SECONDS:
        attempts.pop(0)

    attempts.append(now)

    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        _lockout_until[key] = now + LOGIN_LOCKOUT_SECONDS


def register_successful_login(request: Request) -> None:
    """Po úspešnom prihlásení vyčistí históriu zlyhaní pre túto IP."""

    key = _rate_limit_key(request)

    _failed_login_attempts.pop(key, None)
    _lockout_until.pop(key, None)
