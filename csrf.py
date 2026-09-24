"""
CSRF ochrana (double-submit token).

Token sa vygeneruje pri prvom vykreslení formulára (funkcia
get_csrf_token, registrovaná v templates_config.py ako Jinja globál
"csrf_token"), uloží sa do session (podpísanej cookie cez
SessionMiddleware v main.py) a zároveň sa vloží ako skryté pole
do formulára:

    <input type="hidden" name="csrf_token" value="{{ csrf_token(request) }}">

Pri POST/PUT/PATCH/DELETE požiadavke dependency verify_csrf porovná
hodnotu z formulára s hodnotou uloženou v session - ak sa nezhodujú
(alebo niektorá chýba), požiadavka sa zamietne s 403.

Použitie v routeri:

    router = APIRouter(dependencies=[Depends(verify_csrf)])

- tým je CSRF kontrola aplikovaná na všetky POST/PUT/PATCH/DELETE
  endpointy v danom routeri naraz, bez nutnosti meniť signatúru
  každej handler funkcie.
"""

import hmac
import secrets

from fastapi import HTTPException, Request, status


CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"

# Pre tieto metódy CSRF kontrolu nevyžadujeme - nemenia stav.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def get_csrf_token(request: Request) -> str:
    """
    Vráti CSRF token pre aktuálnu session.

    Ak session ešte žiadny token nemá (napr. prvá návšteva /login),
    vygeneruje nový a uloží ho - tým sa zároveň založí session cookie
    ešte pred prihlásením, takže double-submit funguje aj na
    prihlasovacom formulári.
    """

    token = request.session.get(CSRF_SESSION_KEY)

    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token

    return token


async def verify_csrf(request: Request) -> None:
    """
    Dependency pre routery s formulármi (viď docstring modulu).

    Pri bezpečných metódach (GET a pod.) nič nekontroluje. Pri
    POST/PUT/PATCH/DELETE načíta odoslaný formulár (funguje pre
    application/x-www-form-urlencoded aj multipart/form-data vrátane
    uploadu fotiek - Starlette výsledok cachuje, takže sa telo
    požiadavky číta len raz aj keď handler následne sám používa
    Form(...)/File(...)) a porovná pole "csrf_token" s hodnotou
    uloženou v session.
    """

    if request.method in SAFE_METHODS:
        return

    session_token = request.session.get(CSRF_SESSION_KEY)

    form = await request.form()
    submitted_token = form.get(CSRF_FORM_FIELD)

    token_ok = (
        isinstance(submitted_token, str)
        and bool(session_token)
        and hmac.compare_digest(submitted_token, session_token)
    )

    if not token_ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Neplatný alebo vypršaný bezpečnostný token formulára. "
                "Obnovte stránku a skúste to znova."
            ),
        )
