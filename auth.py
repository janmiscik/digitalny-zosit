import hashlib
import hmac
import os
import secrets
import time
import unicodedata

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

import tenancy
from accounts_db import get_accounts_db
from accounts_models import Account


# =========================================
# HASHOVANIE HESLA
# =========================================

# OWASP odporúča pre PBKDF2-HMAC-SHA256 aspoň 600 000 iterácií. Nový
# hash má tvar `pbkdf2_sha256$<iterácie>$<soľ>$<hash>`; staré hashe
# (`soľ$hash`, 200 000 iterácií) sa dajú ďalej overiť a pri úspešnom
# prihlásení sa potichu prehashujú (needs_rehash).
PBKDF2_ITERATIONS = 600_000
LEGACY_PBKDF2_ITERATIONS = 200_000
HASH_PREFIX = "pbkdf2_sha256"


def _derive(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()


def hash_password(password: str) -> str:
    """
    Vytvorí hash hesla v tvare:

        pbkdf2_sha256$<iterácie>$<soľ>$<hash>
    """

    salt = secrets.token_hex(16)

    return f"{HASH_PREFIX}${PBKDF2_ITERATIONS}${salt}${_derive(password, salt, PBKDF2_ITERATIONS)}"


def _parse_hash(stored_hash: str) -> tuple[int, str, str] | None:
    """Vráti (iterácie, soľ, hash) alebo None pri neplatnom/poškodenom hashi."""

    if not stored_hash or "$" not in stored_hash:
        return None

    parts = stored_hash.split("$")

    if len(parts) == 4 and parts[0] == HASH_PREFIX:

        try:
            iterations = int(parts[1])
        except ValueError:
            return None

        # horná hranica: poškodený/podvrhnutý hash nesmie vyvolať
        # extrémne dlhý výpočet
        if not (LEGACY_PBKDF2_ITERATIONS <= iterations <= 5_000_000):
            return None

        salt, hex_digest = parts[2], parts[3]

    elif len(parts) == 2:

        iterations = LEGACY_PBKDF2_ITERATIONS
        salt, hex_digest = parts

    else:
        return None

    try:

        if len(salt) != 32 or len(hex_digest) != 64:
            return None

        bytes.fromhex(salt)
        bytes.fromhex(hex_digest)

    except ValueError:
        return None

    return iterations, salt, hex_digest


def verify_password(password: str, stored_hash: str) -> bool:
    """
    Overí heslo proti uloženému PBKDF2 hashu (nový aj starý formát).

    Pri neplatnom alebo poškodenom hashi jednoducho vráti False.
    """

    parsed = _parse_hash(stored_hash)

    if parsed is None:
        return False

    iterations, salt, hex_digest = parsed

    return hmac.compare_digest(_derive(password, salt, iterations), hex_digest)


def needs_rehash(stored_hash: str) -> bool:
    """True, ak je hash v starom formáte alebo s menej iteráciami než dnes."""

    parsed = _parse_hash(stored_hash)

    return parsed is not None and (
        not stored_hash.startswith(HASH_PREFIX + "$")
        or parsed[0] < PBKDF2_ITERATIONS
    )


# =========================================
# PRIHLÁSENIE / ODHLÁSENIE
#
# V session sa ukladá `account_slug` (priečinok konta na disku - viď
# tenancy.py), nie rovno username - slug sa pri zmene username nemení,
# takže appka ho môže použiť priamo na nájdenie databázy konta bez
# ďalšieho dotazu do accounts.db pri KAŽDEJ požiadavke.
# =========================================

def password_fingerprint(password_hash: str) -> str:
    """
    Krátky odtlačok hashu hesla. Ukladá sa do session a pri každej
    požiadavke sa porovnáva s kontom - po zmene (alebo obnove) hesla
    sa hash zmení, a tým prestanú platiť VŠETKY ostatné už vydané
    prihlásenia (podpísané cookies by inak platili ďalej).
    """

    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:24]


def login_user(
    request: Request,
    account_slug: str,
    username: str,
    password_hash: str,
) -> None:
    # Session sa pri prihlásení vyčistí (ochrana proti session fixation:
    # starý obsah session, vrátane CSRF tokenu, sa neprenáša do prihlásenej).
    request.session.clear()
    request.session["account_slug"] = account_slug
    request.session["username"] = username
    request.session["pwv"] = password_fingerprint(password_hash)


def logout_user(request: Request) -> None:
    request.session.clear()


def get_current_account_slug(request: Request) -> str | None:
    return request.session.get("account_slug")


def _session_is_current(
    request: Request, account_slug: str, accounts_db: Session
) -> bool:
    """
    True, ak konto stále existuje a heslo sa od prihlásenia nezmenilo.
    Session bez odtlačku (vydaná pred touto kontrolou) neplatí - používateľ
    sa raz znova prihlási.
    """

    account = (
        accounts_db
        .query(Account.password_hash, Account.is_active)
        .filter(Account.slug == account_slug)
        .first()
    )

    if account is None or not account.is_active:
        return False

    stored = request.session.get("pwv")

    return isinstance(stored, str) and hmac.compare_digest(
        stored, password_fingerprint(account.password_hash)
    )


async def require_login_page(
    request: Request,
    accounts_db: Session = Depends(get_accounts_db),
) -> str:
    """
    Dependency pre stránky renderované cez Jinja2.

    ZÁMERNE `async def`: sync dependency FastAPI spúšťa vo vlákne s
    KOPIOU kontextu, takže `set_current_tenant()` (ContextVar) by sa do
    obsluhy požiadavky neprenieslo a trasy bez get_db (fotky zákaziek,
    logo, stiahnutie zálohy) by padali na "aktuálne konto nie je
    nastavené". Async dependency beží v kontexte samotnej požiadavky,
    odkiaľ ho obsluha zdedí.
    Neprihláseného používateľa presmeruje na /login.

    Vracia account_slug (nie username) - je to to, čo ostatné časti
    appky (napr. get_db) potrebujú na nájdenie dát tohto konta.
    """

    account_slug = get_current_account_slug(request)

    if account_slug is None or not _session_is_current(
        request, account_slug, accounts_db
    ):
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )

    tenancy.set_current_tenant(account_slug)

    return account_slug


async def require_login_api(
    request: Request,
    accounts_db: Session = Depends(get_accounts_db),
) -> str:
    """
    Dependency pre JSON API endpointy.
    Neprihláseného používateľa vráti ako 401 JSON.
    (`async def` z rovnakého dôvodu ako require_login_page.)
    """

    account_slug = get_current_account_slug(request)

    if account_slug is None or not _session_is_current(
        request, account_slug, accounts_db
    ):
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Neprihlásený používateľ",
        )

    tenancy.set_current_tenant(account_slug)

    return account_slug


# =========================================
# RATE LIMITING
#
# Prihlásenie sa obmedzuje DVOMA nezávislými limitmi:
#
#   * podľa IP adresy (5 neúspešných pokusov / 5 min -> blok 5 min) -
#     útočník z jednej adresy nezamkne ostatné kontá appky,
#   * podľa používateľského mena (20 neúspešných pokusov / 15 min ->
#     blok 15 min) - ak by útočník IP adresu menil alebo podvrhol
#     (X-Forwarded-For), heslo k jednému kontu aj tak nemôže skúšať
#     donekonečna. Limit je zámerne vyšší než IP limit, aby samotné
#     skúšanie cudzieho mena nezablokovalo majiteľa príliš ľahko.
#
# Registrácia má vlastný limit podľa IP (viď register_allowed) - bez
# neho by sa dali donekonečna zakladať kontá (každé = databáza na
# disku + drahé hashovanie).
#
# Počítadlá sú v pamäti procesu (appka beží v jednej inštancii). Staré
# záznamy sa pravidelne čistia, aby slovník nerástol donekonečna.
# =========================================

MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCKOUT_SECONDS = 300

MAX_USERNAME_ATTEMPTS = 20
USERNAME_WINDOW_SECONDS = 900
USERNAME_LOCKOUT_SECONDS = 900

MAX_REGISTRATIONS_PER_IP = 5
REGISTRATION_WINDOW_SECONDS = 3600

_failed_login_attempts: dict[str, list[float]] = {}
_lockout_until: dict[str, float] = {}

_failed_username_attempts: dict[str, list[float]] = {}
_username_lockout_until: dict[str, float] = {}

_registrations: dict[str, list[float]] = {}

MAX_RESET_REQUESTS_PER_IP = 5
RESET_WINDOW_SECONDS = 3600
_reset_requests: dict[str, list[float]] = {}

_last_cleanup = 0.0
CLEANUP_INTERVAL_SECONDS = 600


def _cleanup(now: float) -> None:
    """Zahodí záznamy, ktorým už vypršalo okno aj blokácia."""

    global _last_cleanup

    if now - _last_cleanup < CLEANUP_INTERVAL_SECONDS:
        return

    _last_cleanup = now

    for attempts, until, window in (
        (_failed_login_attempts, _lockout_until, LOGIN_WINDOW_SECONDS),
        (_failed_username_attempts, _username_lockout_until, USERNAME_WINDOW_SECONDS),
    ):

        for key in list(attempts):

            fresh = [t for t in attempts[key] if now - t < window]

            if fresh:
                attempts[key] = fresh
            else:
                attempts.pop(key, None)

        for key in list(until):

            if until[key] <= now:
                until.pop(key, None)

    for store, window in (
        (_registrations, REGISTRATION_WINDOW_SECONDS),
        (_reset_requests, RESET_WINDOW_SECONDS),
    ):

        for key in list(store):

            fresh = [t for t in store[key] if now - t < window]

            if fresh:
                store[key] = fresh
            else:
                store.pop(key, None)


def _rate_limit_key(request: Request) -> str:
    """IP adresa klienta. Za reverse proxy (Railway) ju uvicorn doplní z
    X-Forwarded-For (viď FORWARDED_ALLOW_IPS v Dockerfile)."""

    return request.client.host if request.client else "unknown"


def _username_key(username: str) -> str:
    return (username or "").strip().lower()[:100]


def is_login_locked(request: Request, username: str | None = None) -> tuple[bool, int]:
    """
    Vráti:

        (True, počet sekúnd)

    ak je prihlásenie z tejto IP adresy (alebo pre dané používateľské
    meno, ak je zadané) dočasne zablokované.
    """

    now = time.time()

    _cleanup(now)

    remaining_values = []

    for lockouts, attempts, key in (
        (_lockout_until, _failed_login_attempts, _rate_limit_key(request)),
        (_username_lockout_until, _failed_username_attempts, _username_key(username) if username else None),
    ):

        if key is None:
            continue

        until = lockouts.get(key)

        if until is None:
            continue

        remaining = until - now

        if remaining <= 0:
            lockouts.pop(key, None)
            attempts.pop(key, None)
            continue

        remaining_values.append(remaining)

    if not remaining_values:
        return False, 0

    return True, int(max(remaining_values)) + 1


def register_failed_login(request: Request, username: str | None = None) -> None:
    """Zaznamená neúspešný pokus pre IP adresu (a používateľské meno)."""

    now = time.time()

    key = _rate_limit_key(request)

    attempts = _failed_login_attempts.setdefault(key, [])

    while attempts and now - attempts[0] >= LOGIN_WINDOW_SECONDS:
        attempts.pop(0)

    attempts.append(now)

    if len(attempts) >= MAX_LOGIN_ATTEMPTS:
        _lockout_until[key] = now + LOGIN_LOCKOUT_SECONDS

    if username:

        user_key = _username_key(username)

        user_attempts = _failed_username_attempts.setdefault(user_key, [])

        while user_attempts and now - user_attempts[0] >= USERNAME_WINDOW_SECONDS:
            user_attempts.pop(0)

        user_attempts.append(now)

        if len(user_attempts) >= MAX_USERNAME_ATTEMPTS:
            _username_lockout_until[user_key] = now + USERNAME_LOCKOUT_SECONDS


def register_successful_login(request: Request, username: str | None = None) -> None:
    """Po úspešnom prihlásení vyčistí históriu zlyhaní pre IP aj meno."""

    key = _rate_limit_key(request)

    _failed_login_attempts.pop(key, None)
    _lockout_until.pop(key, None)

    if username:

        user_key = _username_key(username)

        _failed_username_attempts.pop(user_key, None)
        _username_lockout_until.pop(user_key, None)


def registration_retry_after(request: Request) -> int:
    """Počet sekúnd do ďalšej povolenej registrácie z tejto IP (0 = môže)."""

    now = time.time()

    _cleanup(now)

    attempts = [
        t for t in _registrations.get(_rate_limit_key(request), [])
        if now - t < REGISTRATION_WINDOW_SECONDS
    ]

    if len(attempts) < MAX_REGISTRATIONS_PER_IP:
        return 0

    return int(REGISTRATION_WINDOW_SECONDS - (now - attempts[0])) + 1


def register_registration(request: Request) -> None:
    """Zapíše úspešnú registráciu do limitu danej IP."""

    _registrations.setdefault(_rate_limit_key(request), []).append(time.time())


def reset_request_allowed(request: Request) -> bool:
    """Žiadosti o obnovu hesla: max. 5 za hodinu z jednej IP (proti spamovaniu e-mailov)."""

    now = time.time()

    # Pravidelné upratanie starých záznamov (inak by pri veľkom počte rôznych
    # IP adries ostávali v pamäti neobmedzene dlho).
    _cleanup(now)

    key = _rate_limit_key(request)

    attempts = [t for t in _reset_requests.get(key, []) if now - t < RESET_WINDOW_SECONDS]

    if len(attempts) >= MAX_RESET_REQUESTS_PER_IP:

        _reset_requests[key] = attempts

        return False

    attempts.append(now)
    _reset_requests[key] = attempts

    return True


# =========================================
# POLITIKA REGISTRÁCIE A HESIEL
# =========================================

COMMON_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1", "password123",
    "qwertyui", "qwerty123", "qwertyuiop", "11111111", "00000000", "iloveyou",
    "abcd1234", "abc12345", "heslo123", "heslo1234", "administrator", "admin123",
    "admin1234", "letmein1", "welcome1", "zosit123", "digitalnyzosit",
    # slovenské a české zvyky, sezóny, klávesnicové rady a bežné vzory
    "slovensko", "slovensko1", "slovensko123", "bratislava", "bratislava1", "kosice123",
    "heslo12345", "mojeheslo", "mojeheslo1", "mojeheslo123", "tajneheslo", "tajne123",
    "faktura123", "firma123", "firma1234", "zakaznik1", "kancelaria", "jahodka1",
    "leto2024", "leto2025", "leto2026", "zima2024", "zima2025", "zima2026",
    "jar2025", "jar2026", "jesen2025", "jesen2026", "heslo2024", "heslo2025", "heslo2026",
    "password12", "password1!", "password2", "passw0rd1", "p@ssw0rd", "p@ssword1",
    "qwertz123", "qwertzui", "asdfghjk", "asdfgh123", "zxcvbnm1", "zxcvbnm123",
    "1q2w3e4r", "1q2w3e4r5t", "q1w2e3r4", "qazwsx123", "1qaz2wsx", "123qweasd",
    "12341234", "123123123", "987654321", "98765432", "123456789a", "a1234567",
    "iloveyou1", "monkey123", "dragon123", "football1", "baseball1", "sunshine1",
    "princess1", "trustno1", "superman1", "master123", "shadow123", "michael1",
    "login1234", "welcome123", "changeme1", "changeme123", "letmein123", "test1234",
    "testtest", "testtest1", "user1234", "root1234", "temp1234", "guest1234",
}


def password_problem(password: str, username: str = "") -> str | None:
    """Vráti dôvod, prečo je heslo príliš slabé, alebo None (dĺžka sa kontroluje inde)."""

    # NFKC + casefold: celošírkové znaky, veľkosť písmen a okrajové medzery
    # nemenia bežné heslo na "bezpečné"
    normalized = unicodedata.normalize("NFKC", password).strip().casefold()

    if normalized in COMMON_PASSWORDS:
        return "Toto heslo je príliš bežné. Zvoľ iné."

    if username and normalized == unicodedata.normalize("NFKC", username).strip().casefold():
        return "Heslo nesmie byť rovnaké ako používateľské meno."

    if len(set(password)) < 4:
        return "Heslo je príliš jednoduché (málo rôznych znakov)."

    return None


_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def registration_enabled() -> bool:
    """
    REGISTRATION_ENABLED=false vypne verejnú registráciu nových kont (predvolene
    zapnutá). Neznáma hodnota (preklep ako `flase`) je chyba konfigurácie, nie
    tiché ponechanie registrácie otvorenej.
    """

    raw = os.getenv("REGISTRATION_ENABLED", "true").strip().lower()

    if raw in _TRUE_VALUES or raw == "":
        return True

    if raw in _FALSE_VALUES:
        return False

    raise RuntimeError(
        f"Neplatná hodnota REGISTRATION_ENABLED={raw!r}. Použi `true` alebo `false`."
    )


def max_accounts() -> int:
    """
    MAX_ACCOUNTS: strop počtu kont na serveri (nenastavené alebo 0 = bez limitu).
    Neplatná hodnota (text, záporné číslo) je chyba konfigurácie - nezamieňa sa
    za "bez limitu".
    """

    raw = os.getenv("MAX_ACCOUNTS", "").strip()

    if not raw:
        return 0

    try:
        value = int(raw)

    except ValueError:
        value = -1

    if value < 0:
        raise RuntimeError(f"Neplatná hodnota MAX_ACCOUNTS={raw!r}. Použi celé číslo 0 alebo viac.")

    return value


def check_registration_config() -> None:
    """Zavolá sa pri štarte: appka sa so zlou konfiguráciou registrácie nespustí."""

    registration_enabled()
    max_accounts()
