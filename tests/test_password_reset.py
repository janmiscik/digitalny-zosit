"""Obnova zabudnutého hesla cez e-mail (voliteľná; SMTP + APP_BASE_URL)."""

import os
import re
import time

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import auth
import mailer
import tenancy
from accounts_models import Account
from main import app
from routers import auth as auth_routes

OLD = "stare-heslo1"
NEW = "nove-heslo99"
BASE = "https://appka.example.com"

SENT = []


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    for name, value in {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "zosit@example.com", "APP_BASE_URL": BASE}.items():
        monkeypatch.setenv(name, value)

    SENT.clear()
    monkeypatch.setattr(mailer, "send_mail", lambda to, subject, body: SENT.append((to, subject, body)) or True)

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


def set_email(client, email="jano@example.com", password=OLD):

    return client.post("/settings/email", data={"csrf_token": csrf(client, "/settings"), "email": email,
                                                "current_password": password}, follow_redirects=False,
                       headers={"accept": "application/json"})


def request_reset(username="firma"):

    client = TestClient(app)

    return client.post("/forgot-password", data={"csrf_token": csrf(client, "/forgot-password"),
                                                 "username": username})


def token_from_mail():

    return re.search(r"/reset-password/([^\s]+)", SENT[-1][2]).group(1)


def do_reset(token, password=NEW, confirm=None):

    client = TestClient(app)

    return client.post(f"/reset-password/{token}",
                       data={"csrf_token": csrf(client, "/login"), "new_password": password,
                             "new_password_confirm": password if confirm is None else confirm},
                       follow_redirects=False)


def login(username, password):

    client = TestClient(app)

    return client.post("/login", data={"csrf_token": csrf(client, "/login"), "username": username,
                                       "password": password}, follow_redirects=False)


# ---------- zapnutie ----------

def test_disabled_without_smtp(monkeypatch):

    monkeypatch.delenv("SMTP_HOST")

    client = TestClient(app)

    assert mailer.enabled() is False
    assert client.get("/forgot-password").status_code == 404
    assert client.post("/forgot-password", data={"username": "x"}).status_code in (403, 404)
    assert "Zabudnuté heslo" not in client.get("/login").text


def test_apb_base_url_is_required(monkeypatch):

    monkeypatch.delenv("APP_BASE_URL")

    assert mailer.enabled() is False                    # odkaz sa nikdy nesklada z hlavičky Host


def test_login_shows_link_when_enabled():

    assert "/forgot-password" in TestClient(app).get("/login").text


# ---------- e-mail konta ----------

def test_set_and_clear_email(isolated):

    client = register()

    assert set_email(client, " Jano@Example.com ").status_code == 303

    db = isolated()
    assert db.query(Account).first().email == "jano@example.com"
    db.close()

    assert 'value="jano@example.com"' in client.get("/settings").text

    set_email(client, "")

    db = isolated()
    assert db.query(Account).first().email is None
    db.close()


def test_set_email_requires_current_password_and_valid_format(isolated):

    client = register()

    assert set_email(client, password="zle").status_code == 403
    assert set_email(client, "nie-email").status_code == 422

    db = isolated()
    assert db.query(Account).first().email is None
    db.close()


# ---------- priebeh obnovy ----------

def test_full_reset_flow():

    client = register()
    set_email(client)

    response = request_reset()

    assert response.status_code == 200 and "poslali sme" in response.text
    assert len(SENT) == 1 and SENT[0][0] == "jano@example.com"
    assert SENT[0][2].count(BASE + "/reset-password/") == 1

    token = token_from_mail()

    assert do_reset(token).status_code == 303
    assert login("firma", OLD).status_code == 401
    assert login("firma", NEW).status_code == 303


def test_response_is_identical_for_unknown_user_and_user_without_email():

    register("bezmailu")

    unknown = request_reset("neexistuje")
    no_email = request_reset("bezmailu")

    assert unknown.text.count("poslali sme") == no_email.text.count("poslali sme") == 1
    assert SENT == []


def test_token_is_single_use():

    client = register()
    set_email(client)
    request_reset()
    token = token_from_mail()

    assert do_reset(token).status_code == 303

    again = do_reset(token, "uplne-ine-heslo1")

    assert again.status_code == 400
    assert login("firma", NEW).status_code == 303


def test_token_dies_when_password_changes_otherwise():

    client = register()
    set_email(client)
    request_reset()
    token = token_from_mail()

    client.post("/settings/password", data={"csrf_token": csrf(client, "/settings"), "current_password": OLD,
                                            "new_password": "ine-heslo-123", "new_password_confirm": "ine-heslo-123"})

    assert TestClient(app).get(f"/reset-password/{token}").status_code == 400


def test_expired_token(monkeypatch):

    client = register()
    set_email(client)
    request_reset()
    token = token_from_mail()

    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3601)

    assert TestClient(app).get(f"/reset-password/{token}").status_code == 400


@pytest.mark.parametrize("token", ["blabla", "a.b.c", ""])
def test_garbage_token(token):

    response = TestClient(app).get(f"/reset-password/{token or 'x'}")

    assert response.status_code == 400 and "neplatný" in response.text


def test_token_from_other_secret_is_rejected(monkeypatch):

    client = register()
    set_email(client)
    request_reset()
    token = token_from_mail()

    monkeypatch.setenv("SECRET_KEY", "ine-tajomstvo")

    assert TestClient(app).get(f"/reset-password/{token}").status_code == 400


@pytest.mark.parametrize("password, confirm, text", [
    ("krátke", None, "aspoň 8"),
    (NEW, "iné-heslo-99", "nezhodujú"),
    ("x" * 300, None, "najviac"),
])
def test_reset_password_validation(password, confirm, text):

    client = register()
    set_email(client)
    request_reset()

    response = do_reset(token_from_mail(), password, confirm)

    assert response.status_code == 422 and text in response.text
    assert login("firma", OLD).status_code == 303


def test_reset_only_affects_target_account():

    a = register("firmaA")
    register("firmaB")
    set_email(a)
    request_reset("firmaA")

    do_reset(token_from_mail())

    assert login("firmaB", OLD).status_code == 303


def test_reset_clears_login_lockout():

    client = register()
    set_email(client)

    for _ in range(auth.MAX_LOGIN_ATTEMPTS):
        login("firma", "zle-heslo")

    assert login("firma", OLD).status_code == 429

    request_reset()
    do_reset(token_from_mail())

    assert login("firma", NEW).status_code == 303


def test_reset_requests_are_rate_limited():

    register()

    for _ in range(auth.MAX_RESET_REQUESTS_PER_IP):
        assert request_reset().status_code == 200

    assert request_reset().status_code == 429


def test_audit_log_records_reset(isolated):

    import sqlite3

    client = register()
    set_email(client)
    request_reset()
    do_reset(token_from_mail())

    conn = sqlite3.connect(str(tenancy.account_database_path("firma")))
    actions = [row[0] for row in conn.execute("SELECT action FROM audit_log")]
    conn.close()

    assert {"auth.email_changed", "auth.password_reset_requested", "auth.password_reset_done"} <= set(actions)


# ---------- SMTP odosielač ----------

def test_send_mail_never_raises_and_uses_starttls(monkeypatch):

    monkeypatch.undo()
    for name, value in {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "z@example.com", "APP_BASE_URL": BASE,
                        "SMTP_USER": "u", "SMTP_PASSWORD": "p", "SMTP_PORT": "587"}.items():
        os.environ[name] = value

    calls = []

    class FakeSMTP:

        def __init__(self, host, port, timeout=None, context=None):
            calls.append(("init", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context=None):
            calls.append("starttls")

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["To"], message["Subject"]))

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    assert mailer.send_mail("a@example.com", "Predmet", "Telo") is True
    assert calls == [("init", "smtp.example.com", 587), "starttls", ("login", "u"), ("send", "a@example.com", "Predmet")]

    class Broken(FakeSMTP):

        def send_message(self, message):
            raise OSError("sieť")

    monkeypatch.setattr(smtplib, "SMTP", Broken)

    assert mailer.send_mail("a@example.com", "Predmet", "Telo") is False        # nevyhodí výnimku

    for name in ("SMTP_HOST", "SMTP_FROM", "APP_BASE_URL", "SMTP_USER", "SMTP_PASSWORD", "SMTP_PORT"):
        os.environ.pop(name, None)
