"""Štrukturálny audit tras (login + CSRF) a politika registrácie a hesiel."""

import os
import re

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import tenancy
from auth import password_problem, require_login_api, require_login_page
from csrf import verify_csrf
from main import app

# Jediné trasy, ktoré smú byť dostupné bez prihlásenia.
PUBLIC_ROUTES = {
    ("GET", "/register"), ("POST", "/register"),
    ("GET", "/login"), ("POST", "/login"), ("POST", "/logout"),
    ("GET", "/forgot-password"), ("POST", "/forgot-password"),
    ("GET", "/reset-password/{token}"), ("POST", "/reset-password/{token}"),
    ("GET", "/health"), ("GET", "/health/details"),
}


def dependency_calls(dependant, found=None):

    found = found if found is not None else set()

    for sub in dependant.dependencies:
        found.add(sub.call)
        dependency_calls(sub, found)

    return found


def all_routes(routes=None):

    for route in routes if routes is not None else app.routes:

        if isinstance(route, APIRoute):
            yield route

        elif hasattr(route, "original_router"):
            yield from all_routes(route.original_router.routes)


def test_every_route_requires_login_except_the_public_ones():

    unprotected = set()

    for route in all_routes():

        calls = dependency_calls(route.dependant)

        if require_login_page in calls or require_login_api in calls:
            continue

        for method in route.methods - {"HEAD", "OPTIONS"}:
            unprotected.add((method, route.path))

    assert unprotected == PUBLIC_ROUTES


def test_every_state_changing_route_has_csrf_protection():

    missing = []

    for route in all_routes():

        if route.methods - {"GET", "HEAD", "OPTIONS"} and verify_csrf not in dependency_calls(route.dependant):
            missing.append((sorted(route.methods), route.path))

    assert missing == []


def test_api_docs_are_not_exposed_by_default():

    client = TestClient(app)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, follow_redirects=False).status_code in (303, 404)


# ---------- politika hesiel ----------

@pytest.mark.parametrize("password", ["password", "12345678", "Password123", "ADMIN123", "heslo123", "aaaaaaaa", "abababab"])
def test_weak_passwords_are_rejected(password):

    assert password_problem(password, "firma") is not None


def test_password_equal_to_username_is_rejected():

    assert password_problem("Firma-Novak", "firma-novak") is not None


@pytest.mark.parametrize("password", ["stare-heslo1", "Zelen4-Konvalinka", "k7#Pq2!zXw"])
def test_reasonable_passwords_pass(password):

    assert password_problem(password, "firma") is None


# ---------- registrácia ----------

@pytest.fixture
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


def register(username, password="Zelen4-Konvalinka"):

    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/register").text).group(1)

    return client.post("/register", data={
        "csrf_token": token, "username": username, "password": password,
        "password_confirm": password}, follow_redirects=False)


def account_count(Session):

    from accounts_models import Account

    db = Session()
    count = db.query(Account).count()
    db.close()

    return count


def test_registration_is_open_by_default(isolated):

    assert register("firma-a").status_code == 303
    assert account_count(isolated) == 1


@pytest.mark.parametrize("value", ["false", "0", "no", "FALSE"])
def test_registration_can_be_disabled(isolated, monkeypatch, value):

    monkeypatch.setenv("REGISTRATION_ENABLED", value)

    assert register("firma-a").status_code == 403
    assert TestClient(app).get("/register").status_code == 403
    assert account_count(isolated) == 0


def test_account_cap_blocks_new_registrations(isolated, monkeypatch):

    monkeypatch.setenv("MAX_ACCOUNTS", "2")

    assert register("firma-a").status_code == 303
    assert register("firma-b").status_code == 303
    assert register("firma-c").status_code == 403
    assert account_count(isolated) == 2


def test_invalid_cap_means_unlimited(isolated, monkeypatch):

    monkeypatch.setenv("MAX_ACCOUNTS", "abc")

    assert register("firma-a").status_code == 303


def test_weak_password_cannot_register(isolated):

    response = register("firma-a", password="password123")

    assert response.status_code == 422
    assert account_count(isolated) == 0
