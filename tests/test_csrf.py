"""
Testy pre CSRF ochranu (csrf.py).

Na rozdiel od ostatných testovacích súborov tento NEOBCHÁDZA
Depends(verify_csrf) - overuje priamo mechanizmus double-submit
tokenu na reálnych routách /login a /logout.
"""

import os
import re
import sys
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import auth
import tenancy
from accounts_models import Account
from auth import hash_password
from database import Base, get_db
from main import app


TEST_USERNAME = "testadmin"
TEST_PASSWORD = "tajne-heslo-123"
TEST_SLUG = "test-csrf-account"

# Prihlásenie teraz ide proti accounts_db (nie proti ADMIN_USERNAME/
# ADMIN_PASSWORD_HASH z .env) - rovnaký princíp ako v tests/test_auth.py.
accounts_test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

AccountsTestSessionLocal = sessionmaker(bind=accounts_test_engine)

accounts_db.AccountsBase.metadata.create_all(bind=accounts_test_engine)


def override_get_accounts_db():

    db = AccountsTestSessionLocal()

    try:
        yield db

    finally:
        db.close()


_seed_db = AccountsTestSessionLocal()
_seed_db.add(Account(
    username=TEST_USERNAME,
    slug=TEST_SLUG,
    password_hash=hash_password(TEST_PASSWORD)
))
_seed_db.commit()
_seed_db.close()


# Login/logout teraz zapisujú do audit logu (audit_log.py), takže
# potrebujú funkčnú DB - rovnaká izolovaná in-memory SQLite ako v
# ostatných test súboroch (viď tests/test_auth.py).
TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine
)


def override_get_db():

    db = TestingSessionLocal()

    try:
        yield db

    finally:
        db.close()


Base.metadata.create_all(bind=test_engine)

# Audit log pri prihlásení/odhlásení sa zapisuje priamo cez
# tenancy.get_account_engine(), nie cez get_db() override.
tenancy._engine_cache[TEST_SLUG] = test_engine

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[accounts_db.get_accounts_db] = override_get_accounts_db

# Zámerne ŽIADNY app.dependency_overrides[verify_csrf] - to je presne to,
# čo tento súbor testuje.


@pytest.fixture(autouse=True)
def _ensure_get_db_override():
    """
    Iné testovacie súbory (napr. tests/test_backup.py) majú vlastnú
    autouse fixtúru, ktorá na konci KAŽDÉHO svojho testu robí
    app.dependency_overrides.clear() - keďže `app` je v rámci jedného
    behu pytestu jedna zdieľaná inštancia naprieč všetkými súbormi,
    to zmaže aj override nastavený vyššie (ten sa nastavil len raz,
    pri importe tohto súboru). Táto fixture ho preto pred KAŽDÝM
    testom v tomto súbore znova nastaví, nech poradie spúšťania
    ostatných test súborov nič nepokazí. verify_csrf sa tu nedotýka -
    ten musí zostať skutočný, to je predmet tohto súboru.
    """

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[accounts_db.get_accounts_db] = override_get_accounts_db
    tenancy._engine_cache[TEST_SLUG] = test_engine

    yield


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
    auth._lockout_until.clear()


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
        },
        follow_redirects=False
    )

    # Odhlásenie BEZ tokenu -> zamietnuté, session ostáva platná.
    response = client.post("/logout")
    assert response.status_code == 403

    # Prihlásenie session nevyčistilo (login_user len nastaví "user"),
    # takže pôvodný token z /login je stále platný aj po prihlásení.
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
