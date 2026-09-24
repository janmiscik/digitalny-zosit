"""
Testy pre CSRF ochranu (csrf.py).

Na rozdiel od ostatných testovacích súborov tento NEOBCHÁDZA
Depends(verify_csrf) - overuje priamo mechanizmus double-submit
tokenu na reálnych routách /login a /logout (tie nepotrebujú DB, takže
tu nie je potrebný ani override get_db).
"""

import os
import re
import sys
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["ADMIN_USERNAME"] = "testadmin"

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)

from fastapi.testclient import TestClient

from auth import hash_password
from main import app


TEST_PASSWORD = "tajne-heslo-123"

os.environ["ADMIN_PASSWORD_HASH"] = hash_password(TEST_PASSWORD)

# auth.py aj routers/auth.py si ADMIN_PASSWORD_HASH/ADMIN_USERNAME
# načítajú pri importe (rovnaký princíp ako v tests/test_auth.py) - po
# nastavení env premenných ich preto musíme prepísať priamo v modules.
import auth

auth.ADMIN_USERNAME = "testadmin"
auth.ADMIN_PASSWORD_HASH = os.environ["ADMIN_PASSWORD_HASH"]

import routers.auth as auth_router_module

auth_router_module.ADMIN_USERNAME = "testadmin"
auth_router_module.ADMIN_PASSWORD_HASH = os.environ["ADMIN_PASSWORD_HASH"]


# Zámerne ŽIADNY app.dependency_overrides[verify_csrf] - to je presne to,
# čo tento súbor testuje.


CSRF_INPUT_RE = re.compile(
    r'name="csrf_token"\s+value="([^"]+)"'
)


def fresh_client() -> TestClient:
    """Nová izolovaná inštancia klienta - žiadne zdieľané cookies."""

    return TestClient(app)


def get_csrf_token(client: TestClient, path: str = "/login") -> str:
    """Načíta stránku s formulárom a vytiahne z nej skryté CSRF pole.

    Súčasne (rovnako ako v prehliadači) tým klientovi založí session
    cookie s uloženým tokenom - ten musí sedieť s hodnotou vráteného
    poľa, aby ďalší POST prešiel.
    """

    response = client.get(path)
    assert response.status_code == 200

    match = CSRF_INPUT_RE.search(response.text)
    assert match, f"Stránka {path} neobsahuje skryté pole csrf_token"

    return match.group(1)


def reset_login_rate_limit():
    auth._failed_login_attempts.clear()
    auth._lockout_until = None


# =========================================
# CHÝBAJÚCI / NESPRÁVNY TOKEN
# =========================================

def test_post_without_csrf_token_is_rejected():

    reset_login_rate_limit()
    client = fresh_client()

    # Založíme session cookie (GET /login), ale token do POST tela
    # vôbec nepošleme.
    client.get("/login")

    response = client.post(
        "/login",
        data={"username": "testadmin", "password": TEST_PASSWORD}
    )

    assert response.status_code == 403

    # Prihlásenie naozaj neprebehlo - appka nás stále považuje za
    # neprihlásených.
    assert client.get("/", follow_redirects=False).status_code == 303


def test_post_with_wrong_csrf_token_is_rejected():

    reset_login_rate_limit()
    client = fresh_client()

    client.get("/login")

    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD,
            "csrf_token": "toto-urcite-nie-je-spravny-token"
        }
    )

    assert response.status_code == 403


def test_post_without_any_session_cookie_is_rejected_even_with_token():
    """Token odoslaný vo formulári bez zodpovedajúcej session cookie
    (napr. útočníkova vlastná stránka, ktorá si vymyslí ľubovoľnú
    hodnotu) musí byť odmietnutý - sila ochrany stojí na tom, že
    útočník nevie uhádnuť hodnotu uloženú v obeťovej session."""

    reset_login_rate_limit()
    client = fresh_client()

    # Žiadny predchádzajúci GET - klient nemá vôbec session cookie.
    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD,
            "csrf_token": "hocijaka-hodnota"
        }
    )

    assert response.status_code == 403


# =========================================
# SPRÁVNY TOKEN
# =========================================

def test_post_with_correct_csrf_token_succeeds():

    reset_login_rate_limit()
    client = fresh_client()

    token = get_csrf_token(client, "/login")

    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD,
            "csrf_token": token
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_logout_requires_valid_csrf_token():

    reset_login_rate_limit()
    client = fresh_client()

    # Prihlásime sa (s platným tokenom z /login).
    token = get_csrf_token(client, "/login")

    client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD,
            "csrf_token": token
        }
    )

    # Odhlásenie BEZ tokenu -> zamietnuté, session ostáva platná.
    response = client.post("/logout")
    assert response.status_code == 403

    # Nová stránka po prihlásení nesie AKTUÁLNY (možno iný) token -
    # session pri prihlásení nebola vyčistená, takže ide o ten istý
    # token ako predtým, ale radšej ho znova vytiahneme z reálne
    # vykreslenej stránky, nech test nezávisí na tomto detaile.
    token = get_csrf_token(client, "/")

    response = client.post(
        "/logout",
        data={"csrf_token": token},
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# =========================================
# METÓDY, KTORÉ CSRF NEKONTROLUJE
# =========================================

def test_get_request_never_requires_csrf_token():

    client = fresh_client()

    response = client.get("/login")

    assert response.status_code == 200
