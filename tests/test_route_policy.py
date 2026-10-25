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
    # príjem hlásení o porušení CSP od prehliadača (nič nemení, len loguje; obmedzený počet a veľkosť)
    ("POST", "/csp-report"),
}

# Verejné POST trasy, ktoré zámerne nemajú CSRF token (prehliadač ho do hlásení nepridáva).
CSRF_EXEMPT_ROUTES = {"/csp-report"}


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

        if route.path in CSRF_EXEMPT_ROUTES:
            continue

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


@pytest.mark.parametrize("value", ["abc", "-1", "1.5"])
def test_invalid_account_cap_is_a_config_error_not_unlimited(monkeypatch, value):

    from auth import check_registration_config

    monkeypatch.setenv("MAX_ACCOUNTS", value)

    with pytest.raises(RuntimeError, match="MAX_ACCOUNTS"):
        check_registration_config()


@pytest.mark.parametrize("value", ["flase", "maybe", "2"])
def test_invalid_registration_flag_is_a_config_error(monkeypatch, value):

    from auth import check_registration_config

    monkeypatch.setenv("REGISTRATION_ENABLED", value)

    with pytest.raises(RuntimeError, match="REGISTRATION_ENABLED"):
        check_registration_config()


def test_valid_registration_config_passes(monkeypatch):

    from auth import check_registration_config

    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    monkeypatch.setenv("MAX_ACCOUNTS", "0")
    check_registration_config()


def test_app_refuses_to_start_with_invalid_cap(tmp_path):

    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "MAX_ACCOUNTS": "abc", "SECRET_KEY": "k" * 8 + "Zq9" * 10,
           "APP_ENV": "development", "SESSION_HTTPS_ONLY": "false",
           "ACCOUNTS_DATABASE_URL": f"sqlite:///{tmp_path / 'a.db'}",
           "ACCOUNTS_DATA_DIR": str(tmp_path / "acc")}

    result = subprocess.run([sys.executable, "-c", "import main"], cwd=root, env=env,
                            capture_output=True, text=True)

    assert result.returncode != 0 and "MAX_ACCOUNTS" in result.stderr


def test_concurrent_registrations_never_exceed_the_cap(tmp_path, monkeypatch):

    import threading

    from accounts_models import Account  # noqa: F401

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")
    monkeypatch.setenv("MAX_ACCOUNTS", "2")

    # súborová DB s oddelenými spojeniami (ako v produkcii), nie jedno zdieľané
    engine = create_engine(f"sqlite:///{tmp_path / 'acc.db'}", connect_args={"check_same_thread": False, "timeout": 15})
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

    codes = []

    def worker(index):

        codes.append(register(f"firma-{index}").status_code)

    try:

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert account_count(Session) == 2, codes
        assert sorted(codes).count(303) == 2, codes

    finally:

        app.dependency_overrides.clear()
        tenancy._engine_cache.clear()


@pytest.mark.parametrize("password", ["  password  ", "PASSWORD123", "Heslo123", "ｐａｓｓｗｏｒｄ１２３"])
def test_password_policy_ignores_case_padding_and_fullwidth(password):

    assert password_problem(password, "firma") is not None


def test_username_comparison_ignores_case_and_unicode_form():

    assert password_problem("ＦＩＲＭＡ-Novák", "firma-novák") is not None


def test_same_policy_applies_to_change_and_reset_passwords():

    from routers import auth as auth_routes

    # všetky tri cesty (registrácia, zmena, obnova) volajú tú istú funkciu
    source = open(auth_routes.__file__, encoding="utf-8").read()

    assert source.count("password_problem(") == 3


def test_long_passphrases_and_unicode_are_allowed():

    assert password_problem("Dlhé heslo s medzerami a diakritikou: žltá čerešňa 2026", "firma") is None
    assert password_problem("pŽ1xQ" * 20, "firma") is None


def test_weak_password_cannot_register(isolated):

    response = register("firma-a", password="password123")

    assert response.status_code == 422
    assert account_count(isolated) == 0


@pytest.mark.parametrize("password", ["Slovensko123", "  QWERTZ123 ", "1Q2W3E4R5T", "Leto2026", "ＰＡＳＳＷＯＲＤ１２３"])
def test_extended_common_password_list(password):

    assert password_problem(password, "firma") is not None


def _file_registry(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    engine = create_engine(f"sqlite:///{tmp_path / 'acc.db'}", connect_args={"check_same_thread": False, "timeout": 15})
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

    return Session


def _run_parallel(usernames):

    import threading

    results = {}

    def worker(name, index):
        results[(name, index)] = register(name).status_code

    threads = [threading.Thread(target=worker, args=(name, index)) for index, name in enumerate(usernames)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    return results


def test_parallel_registrations_leave_only_complete_accounts(tmp_path, monkeypatch):
    """Pri veľa súbežných registráciách nad limitom: presne MAX_ACCOUNTS kont, žiadne 5xx,
    každé konto je úplné (priečinky, migrovaná DB, ochranné triggery) a odmietnuté nič nezanechali."""

    import sqlite3

    import db_guards
    from accounts_models import Account

    monkeypatch.setenv("MAX_ACCOUNTS", "3")
    Session = _file_registry(tmp_path, monkeypatch)

    try:
        results = _run_parallel([f"firma-{i}" for i in range(12)])

        codes = list(results.values())

        assert not [c for c in codes if c >= 500], codes
        assert codes.count(303) == 3, codes
        assert account_count(Session) == 3

        db = Session()
        slugs = sorted(a.slug for a in db.query(Account).all())
        db.close()

        directories = sorted(p.name for p in (tmp_path / "accounts").iterdir()) if (tmp_path / "accounts").exists() else []

        assert directories == slugs, f"sirotské priečinky alebo chýbajúce kontá: {directories} vs {slugs}"

        for slug in slugs:

            assert not tenancy.account_needs_migration(slug), slug
            assert tenancy.account_uploads_dir(slug).is_dir() and tenancy.account_backups_dir(slug).is_dir()
            assert db_guards.missing_triggers(tenancy.account_database_path(slug)) == set()

            connection = sqlite3.connect(tenancy.account_database_path(slug))

            try:
                assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

            finally:
                connection.close()

    finally:

        app.dependency_overrides.clear()
        tenancy._engine_cache.clear()


def test_parallel_registrations_of_same_username_create_one_account(tmp_path, monkeypatch):

    monkeypatch.setenv("MAX_ACCOUNTS", "50")
    Session = _file_registry(tmp_path, monkeypatch)

    try:
        codes = list(_run_parallel(["rovnake-meno"] * 8).values())

        assert not [c for c in codes if c >= 500], codes
        assert codes.count(303) == 1, codes
        assert account_count(Session) == 1

        assert len(list((tmp_path / "accounts").iterdir())) == 1

    finally:

        app.dependency_overrides.clear()
        tenancy._engine_cache.clear()
