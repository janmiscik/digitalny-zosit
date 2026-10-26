"""Správa kont majiteľom servera (/admin/accounts): kto smie, zakladanie kont, nové heslo."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import tenancy
from main import app
from models import AuditLog
from tests.test_multi_tenant import _csrf_token, isolated_tenancy, register  # noqa: F401

OWNER = "majitel"
PASSWORD = "Bezpecne-heslo-9"
NEW_PASSWORD = "Zelen4-Konvalinka"


@pytest.fixture
def owner(monkeypatch):

    monkeypatch.setenv("SERVER_ADMINS", OWNER)

    client = TestClient(app)
    assert register(client, OWNER).status_code == 303

    return client


def login(username, password):

    client = TestClient(app)
    token = _csrf_token(client, "/login")

    response = client.post("/login", data={"csrf_token": token, "username": username, "password": password},
                           follow_redirects=False)

    return client, response


def create(client, username, password=NEW_PASSWORD, confirm=None):

    return client.post("/admin/accounts", data={
        "csrf_token": _csrf_token(client, "/admin/accounts"), "username": username,
        "password": password, "password_confirm": password if confirm is None else confirm,
    }, follow_redirects=False)


def test_page_is_hidden_without_server_admins_setting(monkeypatch):

    monkeypatch.delenv("SERVER_ADMINS", raising=False)

    client = TestClient(app)
    assert register(client, OWNER).status_code == 303

    assert client.get("/admin/accounts").status_code == 404
    assert "Spravovať kontá" not in client.get("/settings").text


def test_anonymous_is_sent_to_login(owner):

    anonymous = TestClient(app)

    response = anonymous.get("/admin/accounts", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_other_logged_in_account_gets_404_everywhere_and_cannot_create(owner):

    other = TestClient(app)
    assert register(other, "cudzi").status_code == 303

    token = _csrf_token(other, "/settings")

    assert other.get("/admin/accounts").status_code == 404
    assert other.post("/admin/accounts", data={"csrf_token": token, "username": "x-konto", "password": NEW_PASSWORD,
                                               "password_confirm": NEW_PASSWORD}).status_code == 404
    assert other.post(f"/admin/accounts/{OWNER}/password", data={
        "csrf_token": token, "password": NEW_PASSWORD, "password_confirm": NEW_PASSWORD}).status_code == 404
    assert "Spravovať kontá" not in other.get("/settings").text

    # nič nevzniklo ani sa nezmenilo
    fresh, response = login(OWNER, PASSWORD)
    assert response.status_code == 303
    assert "x-konto" not in fresh.get("/admin/accounts").text


def test_owner_sees_link_and_list(owner):

    assert "Spravovať kontá" in owner.get("/settings").text

    page = owner.get("/admin/accounts")

    assert page.status_code == 200 and OWNER in page.text and "tvoje konto" in page.text


def test_owner_creates_account_that_can_log_in_even_with_registration_closed(owner, monkeypatch):

    monkeypatch.setenv("REGISTRATION_ENABLED", "false")

    response = create(owner, "novy-zivnostnik")

    assert response.status_code == 303 and response.headers["location"] == "/admin/accounts?ok=created"

    page = owner.get("/admin/accounts?ok=created").text
    assert "novy-zivnostnik" in page and "Konto bolo založené" in page

    client, login_response = login("novy-zivnostnik", NEW_PASSWORD)
    assert login_response.status_code == 303 and client.get("/zakaznici").status_code == 200

    assert TestClient(app).get("/register").status_code == 403


def test_create_validates_like_registration_and_never_echoes_passwords(owner):

    for username, password, confirm, expected in [
        ("novy-zivnostnik", NEW_PASSWORD, "ine-heslo-vobec", "nezhodujú"),
        ("ab", NEW_PASSWORD, None, "znakov"),
        ("slabe", "password123", None, "heslo"),
        (OWNER, NEW_PASSWORD, None, "obsadené"),
    ]:
        response = create(owner, username, password, confirm)

        assert response.status_code == 422, (username, response.status_code)
        assert expected in response.text.lower() or expected in response.text
        assert password not in response.text and (not confirm or confirm not in response.text)

    assert "novy-zivnostnik" not in owner.get("/admin/accounts").text


def test_admin_actions_are_audited_without_passwords(owner):

    assert create(owner, "novy-zivnostnik").status_code == 303

    session = sessionmaker(bind=tenancy.get_account_engine(OWNER))()

    try:
        tenancy.set_current_tenant(OWNER)
        entries = [(e.action, e.detail) for e in session.query(AuditLog).all()]

    finally:
        session.close()

    assert any(a == "admin.account_created" and "novy-zivnostnik" in (d or "") for a, d in entries)
    assert not [1 for _, d in entries if NEW_PASSWORD in (d or "")]


def test_password_change_invalidates_old_sessions_and_new_password_works(owner):

    assert create(owner, "novy-zivnostnik").status_code == 303

    old_client, response = login("novy-zivnostnik", NEW_PASSWORD)
    assert response.status_code == 303 and old_client.get("/zakaznici").status_code == 200

    changed = "Uplne-ine-Heslo-55"
    response = owner.post("/admin/accounts/novy-zivnostnik/password", data={
        "csrf_token": _csrf_token(owner, "/admin/accounts"), "password": changed, "password_confirm": changed},
        follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/admin/accounts?ok=password"

    assert old_client.get("/zakaznici", follow_redirects=False).status_code == 303    # staré prihlásenie neplatí
    assert login("novy-zivnostnik", NEW_PASSWORD)[1].status_code != 303
    assert login("novy-zivnostnik", changed)[1].status_code == 303


def test_password_change_rejects_weak_mismatch_unknown_and_own_account(owner):

    assert create(owner, "novy-zivnostnik").status_code == 303

    def change(slug, password, confirm=None):

        return owner.post(f"/admin/accounts/{slug}/password", data={
            "csrf_token": _csrf_token(owner, "/admin/accounts"), "password": password,
            "password_confirm": password if confirm is None else confirm})

    assert change("novy-zivnostnik", "password123").status_code == 422
    assert change("novy-zivnostnik", "Uplne-ine-Heslo-55", "ine").status_code == 422
    assert change("neexistuje", "Uplne-ine-Heslo-55").status_code == 404
    assert change(OWNER, "Uplne-ine-Heslo-55").status_code == 422    # vlastné heslo v Nastaveniach

    assert login("novy-zivnostnik", NEW_PASSWORD)[1].status_code == 303    # pôvodné heslo stále platí
    assert login(OWNER, PASSWORD)[1].status_code == 303


def test_csrf_is_required(owner):

    response = owner.post("/admin/accounts", data={"username": "bez-tokenu", "password": NEW_PASSWORD,
                                                   "password_confirm": NEW_PASSWORD})

    assert response.status_code in (400, 403)
    assert "bez-tokenu" not in owner.get("/admin/accounts").text


def test_admin_does_not_see_other_accounts_data(owner):
    """Admin stránka ukazuje len zoznam kont, nie ich dáta."""

    assert create(owner, "novy-zivnostnik").status_code == 303

    other, _ = login("novy-zivnostnik", NEW_PASSWORD)
    token = _csrf_token(other, "/zakaznici")
    other.post("/customers", data={"csrf_token": token, "name": "TAJNY-ZAKAZNIK-X"}, follow_redirects=False)

    assert "TAJNY-ZAKAZNIK-X" not in owner.get("/admin/accounts").text
    assert "TAJNY-ZAKAZNIK-X" not in owner.get("/zakaznici").text
