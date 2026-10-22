"""
Kontrola SECRET_KEY pri štarte.

SECRET_KEY podpisuje prihlasovaciu session aj odkazy na obnovu hesla. Ak je
slabý alebo predvolený (napr. `change-me` z .env.example), útočník si vie
podpísanú session či token sám vyrobiť. Preto sa v produkcii (cookie len cez
HTTPS, t. j. SESSION_HTTPS_ONLY nie je vypnuté) appka so slabým kľúčom
odmietne spustiť. Lokálne pri vývoji (SESSION_HTTPS_ONLY=false) a v testoch sa
slabý kľúč len zaloguje ako varovanie.
"""

import logging
import re

logger = logging.getLogger(__name__)

MIN_SECRET_KEY_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 12

# Hodnoty z príkladov, návodov a testov - nikdy nesmú ísť do produkcie.
KNOWN_WEAK_VALUES = {
    "changeme", "changethis", "secret", "secretkey", "yoursecretkey", "mysecret",
    "mysecretkey", "supersecret", "supersecretkey", "default", "password", "passw0rd",
    "dev", "development", "devsecret", "test", "testing", "testsecret", "testsecretkey",
    "citestsecret", "example", "placeholder", "replaceme", "todo", "abc123", "qwerty",
    "railway", "digitalnyzosit", "zosit", "fastapi",
}

WEAK_FRAGMENTS = (
    "changeme", "changethis", "replaceme", "yoursecret", "testsecret", "citest",
    "placeholder", "example", "insertkey", "yourkey",
)


def _normalized(value: str) -> str:
    """Malé písmená bez oddeľovačov: `Change-Me`, `change_me` a `change me` sú to isté."""

    return re.sub(r"[^a-z0-9]", "", value.lower())


def secret_key_problems(value: str | None) -> list[str]:
    """Zoznam dôvodov, prečo je kľúč nevyhovujúci (prázdny zoznam = v poriadku)."""

    if value is None or not value.strip():
        return ["SECRET_KEY nie je nastavený."]

    value = value.strip()
    problems = []

    normalized = _normalized(value)

    if normalized in KNOWN_WEAK_VALUES or any(part in normalized for part in WEAK_FRAGMENTS):
        problems.append("je to známa predvolená alebo ukážková hodnota")

    if len(value) < MIN_SECRET_KEY_LENGTH:
        problems.append(f"je kratší než {MIN_SECRET_KEY_LENGTH} znakov")

    if len(set(value)) < MIN_DISTINCT_CHARACTERS:
        problems.append(f"obsahuje menej než {MIN_DISTINCT_CHARACTERS} rôznych znakov (nie je náhodný)")

    return problems


def validate_secret_key(value: str | None, enforce: bool) -> None:
    """
    Pri `enforce=True` (produkcia) vyhodí RuntimeError, ak je kľúč chybný alebo slabý.
    Inak slabý kľúč iba zaloguje; chýbajúci kľúč je chyba vždy.
    """

    problems = secret_key_problems(value)

    if not problems:
        return

    if value is None or not value.strip():
        raise RuntimeError("SECRET_KEY nie je nastavený v .env - pozri .env.example")

    message = (
        "SECRET_KEY je slabý: " + "; ".join(problems) + ". "
        "Vygeneruj náhodný kľúč príkazom: "
        'python -c "import secrets; print(secrets.token_hex(32))" '
        "a nastav ho ako SECRET_KEY."
    )

    if enforce:
        raise RuntimeError(message)

    logger.warning("%s (lokálny vývoj, appka sa napriek tomu spustí)", message)
