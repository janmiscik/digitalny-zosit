"""Zmena hesla v Nastaveniach."""

import os
import re

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import auth
import tenancy
from accounts_models import Account
from main import app

OLD = "stare-heslo1"
NEW = "nove-heslo99"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    accounts_db.AccountsBase.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def override():

        db = Session()

        try:
            yield db

        finally:
            db.close()

    app.dependency_overrides[accounts_db.get_accounts_db] = override
    tenancy._engine_cache.clear()

    yield Session

    app.dependency_overrides.clear()
    tenancy._engine_cache.clear()


def csrf(client, path):

    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text).group(1)


def register(username="firma"):

    client = TestClient(app)
    client.post("/register", data={"csrf_token": csrf(client, "/register"), "username": username,
                                   "password": OLD, "password_confirm": OLD}, follow_redirects=False)

    return client


def change(client, current=OLD, new=NEW, confirm=None):

    return client.post(
        "/settings/password",
        data={"csrf_token": csrf(client, "/settings"), "current_password": current,
              "new_password": new, "new_password_confirm": new if confirm is None else confirm},
        follow_redirects=False,
        headers={"accept": "application/json"},
    )


def login(username, password):

    client = TestClient(app)

    return client.post("/login", data={"csrf_token": csrf(client, "/login"), "username": username,
                                       "password": password}, follow_redirects=False)


def test_settings_page_has_password_form():

    client = register()

    assert "/settings/password" in client.get("/settings").text


def test_change_password_success(isolated):

    client = register()

    response = change(client)

    assert response.status_code == 303 and response.headers["location"] == "/settings?password=ok"
    assert "Heslo bolo zmenené" in client.get("/settings?password=ok").text

    assert login("firma", OLD).status_code == 401
    assert login("firma", NEW).status_code == 303

    db = isolated()
    stored = db.query(Account).first().password_hash
    db.close()

    assert stored.startswith("pbkdf2_sha256$") and not auth.needs_rehash(stored)


def test_session_stays_valid_after_change():

    client = register()

    change(client)

    assert client.get("/settings").status_code == 200


def test_wrong_current_password_is_rejected():

    client = register()

    response = change(client, current="zle-heslo")

    assert response.status_code == 403
    assert login("firma", OLD).status_code == 303                  # staré heslo ostalo


@pytest.mark.parametrize("new, confirm, status", [
    ("krátke", None, 422),                      # < 8 znakov
    (NEW, "iné-heslo99", 422),                  # nezhoda
    (OLD, None, 422),                           # rovnaké ako súčasné
    ("x" * 300, None, 422),                     # príliš dlhé
])
def test_invalid_new_password(new, confirm, status):

    client = register()

    assert change(client, new=new, confirm=confirm).status_code == status
    assert login("firma", OLD).status_code == 303


def test_requires_login():

    client = TestClient(app)
    token = csrf(client, "/login")

    response = client.post("/settings/password", data={"csrf_token": token, "current_password": "a",
                                                        "new_password": "bbbbbbbb", "new_password_confirm": "bbbbbbbb"},
                           follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_requires_csrf_token():

    client = register()

    response = client.post("/settings/password", data={"current_password": OLD, "new_password": NEW,
                                                        "new_password_confirm": NEW})

    assert response.status_code == 403
    assert login("firma", OLD).status_code == 303


def test_wrong_current_password_attempts_are_rate_limited():

    client = register()

    for _ in range(auth.MAX_LOGIN_ATTEMPTS):
        assert change(client, current="zle-heslo").status_code == 403

    assert change(client, current=OLD).status_code == 429          # aj správne heslo je dočasne blokované


def test_other_account_password_is_untouched():

    a = register("firmaA")
    register("firmaB")

    change(a)

    assert login("firmaB", OLD).status_code == 303


def test_audit_log_records_change():

    import sqlite3

    client = register()

    change(client, current="zle-heslo")
    auth.register_successful_login(type("R", (), {"client": type("C", (), {"host": "testclient"})()})(), "firma")
    change(client)

    conn = sqlite3.connect(str(tenancy.account_database_path("firma")))
    actions = [row[0] for row in conn.execute("SELECT action FROM audit_log")]
    conn.close()

    assert "auth.password_change_failed" in actions and "auth.password_changed" in actions
