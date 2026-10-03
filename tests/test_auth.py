import os
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
from auth import hash_password, verify_password
from csrf import verify_csrf
from database import Base, get_db
from main import app


# =========================================
# TESTOVACIE KONTO
#
# Prihlásenie teraz ide proti accounts_db (nie proti ADMIN_USERNAME/
# ADMIN_PASSWORD_HASH z .env) - vytvoríme preto skutočný riadok Account
# v izolovanej (in-memory) accounts databáze pre tento testovací súbor.
# =========================================

TEST_USERNAME = "testadmin"
TEST_PASSWORD = "tajne-heslo-123"
TEST_SLUG = "test-auth-account"

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


# =========================================
# TEST DATABASE (business dáta tohto jedného testovacieho konta)
# =========================================

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

# routers/auth.py pri prihlásení/odhlásení zapisuje do audit logu PRIAMO
# cez tenancy.get_account_engine(account.slug) (nie cez get_db()
# override nižšie - v okamihu prihlásenia appka ešte nevie, čí
# get_db() by sa mal použiť). Predvyplnením cache zabezpečíme, že tento
# zápis skutočne skončí v TOM ISTOM in-memory engine, ktorý test ďalej
# číta cez get_db() override.
tenancy._engine_cache[TEST_SLUG] = test_engine

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[accounts_db.get_accounts_db] = override_get_accounts_db

# Tento súbor testuje prihlásenie/odhlásenie/rate limiting, nie CSRF
# (na to je tests/test_csrf.py) - tu ho obídeme.
app.dependency_overrides[verify_csrf] = lambda: None


@pytest.fixture(autouse=True)
def _ensure_dependency_overrides():
    """
    Iné testovacie súbory (napr. tests/test_invoices.py a ďalšie s
    vlastnou per-test fixtúrou) na konci KAŽDÉHO svojho testu robia
    app.dependency_overrides.clear() - keďže `app` je v rámci jedného
    behu pytestu jedna zdieľaná inštancia naprieč všetkými súbormi,
    to zmaže aj override nastavený vyššie (ten sa nastavil len raz, pri
    importe tohto súboru). Táto fixture ho preto pred KAŽDÝM testom v
    tomto súbore znova nastaví, nech poradie/výber spúšťaných test
    súborov nič nepokazí.
    """

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[accounts_db.get_accounts_db] = override_get_accounts_db
    app.dependency_overrides[verify_csrf] = lambda: None
    tenancy._engine_cache[TEST_SLUG] = test_engine

    yield

    # Rate limiting je kľúčovaný podľa IP adresy (auth._rate_limit_key) -
    # TestClient vždy posiela rovnakú (fiktívnu) IP, takže bez vyčistenia
    # by si testy naprieč súbormi mohli navzájom ovplyvňovať stav.
    auth._failed_login_attempts.clear()
    auth._lockout_until.clear()


client = TestClient(app)


# =========================================
# HASHOVANIE HESLA
# =========================================

def test_password_hash_roundtrip():

    hashed = hash_password("moje-heslo")

    assert verify_password("moje-heslo", hashed) is True
    assert verify_password("zle-heslo", hashed) is False


# =========================================
# PRÍSTUP BEZ PRIHLÁSENIA
# =========================================

def test_home_requires_login():

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_customers_api_requires_login():

    response = client.get("/customers")

    assert response.status_code == 401


# =========================================
# PRIHLÁSENIE
# =========================================

def test_login_wrong_password():

    response = client.post(
        "/login",
        data={
            "username": TEST_USERNAME,
            "password": "zle-heslo"
        }
    )

    assert response.status_code == 401


def test_login_unknown_username():
    """Neexistujúce meno sa má správať rovnako ako zlé heslo (žiadne
    rozlíšenie, ktoré meno v appke existuje)."""

    response = client.post(
        "/login",
        data={
            "username": "neexistuje-vobec",
            "password": "čokoľvek"
        }
    )

    assert response.status_code == 401


def test_login_success_and_access():

    response = client.post(
        "/login",
        data={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # session cookie by mal byť teraz nastavený v kliente
    response = client.get("/")

    assert response.status_code == 200


def test_logout():

    client.post(
        "/login",
        data={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD
        }
    )

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303


# =========================================
# RATE LIMITING PRIHLÁSENIA
# =========================================

def test_login_locked_after_too_many_wrong_attempts():

    # vyčistíme stav limitera, nech test nezávisí od poradia iných testov
    auth._failed_login_attempts.clear()
    auth._lockout_until.clear()

    for _ in range(auth.MAX_LOGIN_ATTEMPTS):

        response = client.post(
            "/login",
            data={
                "username": TEST_USERNAME,
                "password": "zle-heslo"
            }
        )

        assert response.status_code == 401

    # ďalší pokus (aj so správnym heslom) musí byť zablokovaný
    response = client.post(
        "/login",
        data={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD
        }
    )

    assert response.status_code == 429

    # Zablokovaný pokus neprihlási - appka nás považuje naďalej za
    # neprihlásených (aj keby response niesla cookie s CSRF tokenom
    # pre formulár, čo je od zavedenia CSRF ochrany očakávané).
    assert client.get("/", follow_redirects=False).status_code == 303

    auth._failed_login_attempts.clear()
    auth._lockout_until.clear()


def test_successful_login_resets_lockout_counter():

    auth._failed_login_attempts.clear()
    auth._lockout_until.clear()

    for _ in range(auth.MAX_LOGIN_ATTEMPTS - 1):

        client.post(
            "/login",
            data={
                "username": TEST_USERNAME,
                "password": "zle-heslo"
            }
        )

    response = client.post(
        "/login",
        data={
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert len(auth._failed_login_attempts) == 0

    auth._failed_login_attempts.clear()
    auth._lockout_until.clear()
