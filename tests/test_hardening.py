"""Technický audit (krok 10): hash hesiel, limity, hlavičky, session, SQLite."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hashlib

import pytest
from fastapi.testclient import TestClient

import auth
import main
import tenancy
from main import app


def fake_request(ip="1.2.3.4", session=None):

    return SimpleNamespace(client=SimpleNamespace(host=ip), session=session if session is not None else {})


# ---------- hash hesiel ----------

def test_new_hash_has_versioned_format_and_600k_iterations():

    stored = auth.hash_password("tajne-heslo")

    prefix, iterations, salt, digest = stored.split("$")

    assert prefix == "pbkdf2_sha256"
    assert int(iterations) == 600_000
    assert auth.verify_password("tajne-heslo", stored)
    assert not auth.verify_password("ine-heslo", stored)
    assert not auth.needs_rehash(stored)


def legacy_hash(password, salt="ab" * 16):

    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()

    return f"{salt}${digest}"


def test_legacy_hash_still_verifies_and_needs_rehash():

    stored = legacy_hash("stare-heslo")

    assert auth.verify_password("stare-heslo", stored)
    assert not auth.verify_password("zle", stored)
    assert auth.needs_rehash(stored)


@pytest.mark.parametrize("broken", [
    "", "bezdolara", "a$b$c", "pbkdf2_sha256$abc$" + "ab" * 16 + "$" + "cd" * 32,
    "pbkdf2_sha256$99999999999$" + "ab" * 16 + "$" + "cd" * 32,      # extrémny počet iterácií
    "pbkdf2_sha256$600000$kratka$" + "cd" * 32,
    "xyz$" + "cd" * 32,
])
def test_broken_hash_is_rejected_without_crash(broken):

    assert auth.verify_password("x", broken) is False
    assert auth.needs_rehash(broken) is False


def test_login_upgrades_legacy_hash(monkeypatch):

    # priamy test logiky: needs_rehash -> nový hash sa dá overiť tým istým heslom
    stored = legacy_hash("moje-heslo")

    assert auth.needs_rehash(stored)

    upgraded = auth.hash_password("moje-heslo")

    assert auth.verify_password("moje-heslo", upgraded) and not auth.needs_rehash(upgraded)


# ---------- limity ----------

def test_ip_lockout_after_five_failures():

    request = fake_request()

    for _ in range(auth.MAX_LOGIN_ATTEMPTS):
        auth.register_failed_login(request)

    locked, seconds = auth.is_login_locked(request)

    assert locked and seconds > 0
    assert auth.is_login_locked(fake_request(ip="9.9.9.9"))[0] is False


def test_username_lockout_even_when_ip_changes():

    for i in range(auth.MAX_USERNAME_ATTEMPTS):
        auth.register_failed_login(fake_request(ip=f"10.0.0.{i}"), "Obet")

    # úplne nová IP, rovnaké meno (aj iná veľkosť písmen)
    assert auth.is_login_locked(fake_request(ip="8.8.8.8"), "  obet ")[0] is True
    assert auth.is_login_locked(fake_request(ip="8.8.8.8"), "iny-ucet")[0] is False


def test_successful_login_clears_username_counter():

    request = fake_request()

    for _ in range(3):
        auth.register_failed_login(request, "jano")

    auth.register_successful_login(request, "jano")

    assert "jano" not in auth._failed_username_attempts


def test_registration_limit_per_ip():

    request = fake_request()

    assert auth.registration_retry_after(request) == 0

    for _ in range(auth.MAX_REGISTRATIONS_PER_IP):
        auth.register_registration(request)

    assert auth.registration_retry_after(request) > 0
    assert auth.registration_retry_after(fake_request(ip="7.7.7.7")) == 0


def test_cleanup_drops_expired_entries(monkeypatch):

    auth._registrations["1.1.1.1"] = [0.0]
    auth._failed_login_attempts["2.2.2.2"] = [0.0]
    auth._lockout_until["2.2.2.2"] = 1.0
    monkeypatch.setattr(auth, "_last_cleanup", 0.0)

    auth._cleanup(10_000.0)

    assert "1.1.1.1" not in auth._registrations
    assert "2.2.2.2" not in auth._failed_login_attempts
    assert "2.2.2.2" not in auth._lockout_until


def test_cleanup_drops_expired_reset_requests_but_keeps_fresh(monkeypatch):

    auth._reset_requests["stara"] = [0.0, 10.0]
    auth._reset_requests["zmiesana"] = [0.0, 9_000.0]
    auth._reset_requests["cerstva"] = [9_500.0]
    monkeypatch.setattr(auth, "_last_cleanup", 0.0)

    auth._cleanup(10_000.0)         # okno žiadostí o obnovu je 3600 s

    assert "stara" not in auth._reset_requests
    assert auth._reset_requests["zmiesana"] == [9_000.0]
    assert auth._reset_requests["cerstva"] == [9_500.0]


def test_reset_request_check_itself_cleans_other_ips(monkeypatch):
    """Upratanie sa spúšťa pri samotnej žiadosti o obnovu, nielen pri prihlásení/registrácii."""

    now = 100_000.0
    monkeypatch.setattr(auth.time, "time", lambda: now)
    monkeypatch.setattr(auth, "_last_cleanup", 0.0)

    for number in range(500):
        auth._reset_requests[f"10.0.{number // 250}.{number % 250}"] = [now - auth.RESET_WINDOW_SECONDS - 1]

    assert auth.reset_request_allowed(fake_request(ip="9.9.9.9")) is True

    assert list(auth._reset_requests) == ["9.9.9.9"]


def test_reset_request_limit_still_enforced_after_cleanup(monkeypatch):

    monkeypatch.setattr(auth, "_last_cleanup", 0.0)
    request = fake_request(ip="8.8.8.8")

    results = [auth.reset_request_allowed(request) for _ in range(auth.MAX_RESET_REQUESTS_PER_IP + 1)]

    assert results == [True] * auth.MAX_RESET_REQUESTS_PER_IP + [False]


# ---------- session ----------

def test_login_rotates_session_content():

    request = fake_request(session={"_csrf_token": "stary", "cudzie": "x"})

    auth.login_user(request, "firma", "Firma", "hash-hesla")

    assert request.session == {
        "account_slug": "firma",
        "username": "Firma",
        "pwv": auth.password_fingerprint("hash-hesla"),
    }


# ---------- hlavičky a dokumentácia ----------

def test_security_headers_present_on_every_response():

    client = TestClient(app)

    for path in ("/health", "/login", "/neexistuje"):

        headers = client.get(path).headers

        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in headers["permissions-policy"]


def test_hsts_only_when_https_only_session(monkeypatch):

    # testy bežia so SESSION_HTTPS_ONLY=false -> HSTS sa neposiela
    assert "strict-transport-security" not in TestClient(app).get("/health").headers
    assert main.SESSION_HTTPS_ONLY is False


def test_api_docs_are_disabled_by_default():

    client = TestClient(app)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


# ---------- SQLite ----------

def test_account_engine_has_busy_timeout(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path)
    tenancy._engine_cache.pop("auditslug", None)
    (tmp_path / "auditslug").mkdir()

    try:

        engine = tenancy.get_account_engine("auditslug")

        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 15000
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    finally:
        tenancy.discard_account_engine("auditslug")


def test_accounts_engine_has_busy_timeout():

    from accounts_db import accounts_engine

    with accounts_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 15000


# ---------- Content-Security-Policy ----------

def test_csp_is_sent_with_html_pages_only():

    client = TestClient(app)

    html = client.get("/login")
    csp = html.headers["content-security-policy"]

    for directive in ("default-src 'self'", "object-src 'none'", "base-uri 'self'",
                      "form-action 'self'", "frame-ancestors 'none'",
                      "https://fonts.googleapis.com", "https://fonts.gstatic.com"):
        assert directive in csp

    assert "http:" not in csp and "*" not in csp.replace("'unsafe-inline'", "")
    script_src = [d for d in csp.split("; ") if d.startswith("script-src ")][0]
    assert "unsafe-inline" not in script_src

    # JSON a ostatné odpovede CSP nenesú (PDF viewer v prehliadači by mohol zlyhať)
    assert "content-security-policy" not in client.get("/health").headers
