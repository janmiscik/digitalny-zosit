"""
Testy pre registráciu kont a oddelenie dát medzi nimi (tenancy.py,
accounts_db.py, routers/auth.py).

Na rozdiel od ostatných testovacích súborov tu NEPOUŽÍVAME
app.dependency_overrides[get_db] - práve get_db() (podľa prihláseného
konta) je to, čo sa tu overuje. Každý test dostane vlastný dočasný
ACCOUNTS_DATA_DIR (tenancy.ACCOUNTS_DATA_DIR) a vlastnú accounts.db,
nech testy navzájom nekolidujú a nedotýkajú sa skutočných dát appky.
"""

import os
import re
import sys
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import tenancy
from main import app


@pytest.fixture(autouse=True)
def isolated_tenancy(tmp_path, monkeypatch):
    """
    Každý test dostane: vlastný ACCOUNTS_DATA_DIR (priečinky kont na
    disku), vlastnú (in-memory) accounts.db a vyprázdnenú engine cache
    (tenancy.py) - aby medzi testami nič "neprežilo".
    """

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

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

    app.dependency_overrides[accounts_db.get_accounts_db] = override_get_accounts_db

    tenancy._engine_cache.clear()

    yield

    app.dependency_overrides.clear()
    tenancy._engine_cache.clear()


def _csrf_token(client: TestClient, path: str) -> str:

    response = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)

    assert match, f"Stránka {path} neobsahuje CSRF token"

    return match.group(1)


def register(client: TestClient, username: str, password: str = "heslo1234"):

    csrf = _csrf_token(client, "/register")

    return client.post(
        "/register",
        data={
            "csrf_token": csrf,
            "username": username,
            "password": password,
            "password_confirm": password
        },
        follow_redirects=False
    )


# =========================================
# REGISTRÁCIA
# =========================================

def test_registration_creates_isolated_account_and_logs_in():

    client = TestClient(app)

    response = register(client, "firmaA")

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # Je rovno prihlásený.
    assert client.get("/", follow_redirects=False).status_code == 200


def test_registration_provisions_database_file_with_migrations():

    client = TestClient(app)
    register(client, "firmaA")

    db_path = tenancy.account_database_path("firmaa")

    assert db_path.exists()

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    tables = {
        row[0] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()

    assert "alembic_version" in tables
    assert "customers" in tables
    assert "invoices" in tables


def test_registration_rejects_duplicate_username():

    client = TestClient(app)
    register(client, "firmaA")

    client2 = TestClient(app)
    response = register(client2, "firmaA")

    assert response.status_code == 422
    assert "obsaden" in response.text.lower()


def test_registration_rejects_mismatched_passwords():

    client = TestClient(app)
    csrf = _csrf_token(client, "/register")

    response = client.post(
        "/register",
        data={
            "csrf_token": csrf,
            "username": "firmaA",
            "password": "heslo1234",
            "password_confirm": "ineHeslo"
        }
    )

    assert response.status_code == 422
    assert "nezhoduj" in response.text.lower()


def test_registration_rejects_short_password():

    client = TestClient(app)
    csrf = _csrf_token(client, "/register")

    response = client.post(
        "/register",
        data={
            "csrf_token": csrf,
            "username": "firmaA",
            "password": "krátke",
            "password_confirm": "krátke"
        }
    )

    assert response.status_code == 422


def test_two_different_usernames_get_different_slugs_on_collision():
    """Rovnaký základ slugu (napr. rovnaké písmená po normalizácii)
    sa má vyriešiť príponou -2, nie kolíziou/chybou."""

    client_a = TestClient(app)
    register(client_a, "Firma")

    client_b = TestClient(app)
    response = register(client_b, "FIRMA")  # iný username, rovnaký slug základ

    assert response.status_code == 303

    assert tenancy.account_database_path("firma").exists()
    assert tenancy.account_database_path("firma-2").exists()


# =========================================
# IZOLÁCIA DÁT MEDZI KONTAMI
# =========================================

def test_account_cannot_see_other_accounts_customers():

    client_a = TestClient(app)
    register(client_a, "firmaA")

    csrf = _csrf_token(client_a, "/")
    client_a.post(
        "/customers",
        data={"csrf_token": csrf, "name": "Tajny zakaznik A"},
        follow_redirects=False
    )

    client_b = TestClient(app)
    register(client_b, "firmaB")

    response = client_b.get("/customers")

    assert "Tajny zakaznik A" not in response.text


def test_account_sees_only_its_own_customers():

    client_a = TestClient(app)
    register(client_a, "firmaA")
    csrf = _csrf_token(client_a, "/")
    client_a.post(
        "/customers",
        data={"csrf_token": csrf, "name": "Zakaznik A"},
        follow_redirects=False
    )

    client_b = TestClient(app)
    register(client_b, "firmaB")
    csrf = _csrf_token(client_b, "/")
    client_b.post(
        "/customers",
        data={"csrf_token": csrf, "name": "Zakaznik B"},
        follow_redirects=False
    )

    response_a = client_a.get("/customers")
    response_b = client_b.get("/customers")

    assert "Zakaznik A" in response_a.text
    assert "Zakaznik B" not in response_a.text

    assert "Zakaznik B" in response_b.text
    assert "Zakaznik A" not in response_b.text


def test_accounts_have_physically_separate_database_files():

    client_a = TestClient(app)
    register(client_a, "firmaA")

    client_b = TestClient(app)
    register(client_b, "firmaB")

    path_a = tenancy.account_database_path("firmaa")
    path_b = tenancy.account_database_path("firmab")

    assert path_a != path_b
    assert path_a.exists()
    assert path_b.exists()


def test_login_with_one_accounts_credentials_does_not_grant_access_to_another():

    client = TestClient(app)
    register(client, "firmaA", password="heslofirmyA")

    csrf = _csrf_token(client, "/")
    client.post("/logout", data={"csrf_token": csrf})

    register(TestClient(app), "firmaB", password="heslofirmyB")

    csrf = _csrf_token(client, "/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf,
            "username": "firmaB",
            "password": "heslofirmyA"  # heslo konta A, meno konta B
        }
    )

    assert response.status_code == 401


# =========================================
# REGRESNÝ TEST: Alembic migrácia pri registrácii nesmie vypnúť
# logovanie zvyšku appky (fileConfig() defaultne disable_existing_loggers=True)
# =========================================

def test_provisioning_account_does_not_disable_other_loggers():

    import logging

    other_logger = logging.getLogger("uploads_utils")

    assert other_logger.disabled is False

    client = TestClient(app)
    register(client, "firmaA")

    assert other_logger.disabled is False, (
        "Spustenie migrácie (tenancy.provision_account) vyplo logger "
        "'uploads_utils' - alembic/env.py chýba disable_existing_loggers=False."
    )
