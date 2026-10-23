"""Docker (neprivilegovaný používateľ, .dockerignore), proxy a ochrana tokenu obnovy hesla."""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import pytest
from fastapi.testclient import TestClient

import log_filters
from main import app

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker-entrypoint.sh"


# ---------- Dockerfile ----------

def test_dockerfile_creates_unprivileged_user_and_uses_entrypoint():

    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert re.search(r"useradd .*\bapp\b", text)
    assert 'ENTRYPOINT ["./docker-entrypoint.sh"]' in text
    assert 'CMD ["sh", "./start.sh"]' in text
    assert "PYTHONDONTWRITEBYTECODE=1" in text


def test_entrypoint_drops_privileges_and_checks_write_access():

    text = ENTRYPOINT.read_text(encoding="utf-8")

    assert "setpriv" in text and "--reuid" in text
    assert "find " in text and "chown -R" in text
    assert "REQUIRE_NON_ROOT" in text
    assert os.access(ENTRYPOINT, os.X_OK)


def test_start_script_warns_about_wildcard_proxy_trust_outside_railway():

    text = (ROOT / "start.sh").read_text(encoding="utf-8")

    assert "FORWARDED_ALLOW_IPS" in text and "RAILWAY_" in text


# ---------- .dockerignore ----------

def ignored(patterns, name):

    for pattern in patterns:

        pattern = pattern.rstrip("/")

        if pattern.startswith("**/"):
            pattern = pattern[3:]

        if Path(name).match(pattern) or name == pattern or name.startswith(pattern + "/"):
            return True

    return False


def test_dockerignore_keeps_secrets_data_and_backups_out_of_the_image():

    patterns = [
        line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith(("#", "!"))
    ]

    for sensitive in (".env", ".env.local", "data", "backups", "uploads", "accounts", "x.db", "x.db-wal",
                      "x.db-shm", "zaloha.zip", "restore-journal.json", ".git", "tests", "requirements-dev.txt"):

        assert ignored(patterns, sensitive), sensitive


def test_dockerignore_does_not_hide_files_the_app_needs():

    patterns = [
        line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith(("#", "!"))
    ]

    for needed in ("main.py", "start.sh", "docker-entrypoint.sh", "requirements.txt", "alembic.ini",
                   "alembic_accounts.ini", "alembic", "alembic_accounts", "templates", "static",
                   "routers", "diagnose_storage.py", "manage_accounts.py", "static/fonts/DejaVuSans.ttf"):

        assert not ignored(patterns, needed), needed


# ---------- skutočný beh entrypointu (len ako root, kde sa dá vytvoriť používateľ) ----------

@pytest.fixture
def open_tmp():
    """Dočasný adresár, cez ktorý sa dá prejsť aj ako iný používateľ (pytest tmp_path má práva 0700)."""

    import tempfile

    path = Path(tempfile.mkdtemp(prefix="zosit-entry-"))
    os.chmod(path, 0o755)

    yield path

    shutil.rmtree(path, ignore_errors=True)


def can_run_as_root():

    return os.geteuid() == 0 and shutil.which("setpriv") and shutil.which("useradd")


@pytest.mark.skipif(not can_run_as_root(), reason="vyžaduje root, setpriv a useradd")
def test_entrypoint_runs_command_as_unprivileged_user_and_fixes_volume(open_tmp):

    tmp_path = open_tmp

    user = "zosit_test_user"
    subprocess.run(["useradd", "--no-create-home", "--shell", "/usr/sbin/nologin", user],
                   check=False, capture_output=True)

    try:

        data = tmp_path / "data"
        (data / "accounts").mkdir(parents=True)
        (data / "accounts" / "stary-subor").write_text("x")
        os.chmod(tmp_path, 0o755)
        os.chmod(data, 0o755)

        env = {**os.environ, "APP_USER": user, "APP_DATA_DIR": str(data)}

        result = subprocess.run(
            [str(ENTRYPOINT), "sh", "-c", f"id -un; touch '{data}/accounts/novy'"],
            env=env, capture_output=True, text=True,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert user in result.stdout.splitlines()
        assert (data / "accounts" / "stary-subor").stat().st_uid == (data / "accounts" / "novy").stat().st_uid
        assert (data / "accounts" / "novy").stat().st_uid != 0

    finally:

        subprocess.run(["userdel", user], check=False, capture_output=True)


@pytest.mark.skipif(not can_run_as_root(), reason="vyžaduje root, setpriv a useradd")
def test_entrypoint_refuses_to_start_when_require_non_root_and_volume_not_writable(open_tmp):

    tmp_path = open_tmp

    data = tmp_path / "data"
    data.mkdir()
    os.chmod(tmp_path, 0o755)
    os.chmod(data, 0o755)

    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "chown").write_text("#!/bin/sh\nexit 1\n")
    os.chmod(stub / "chown", 0o755)

    env = {**os.environ, "APP_USER": "nobody", "APP_DATA_DIR": str(data),
           "PATH": f"{stub}:{os.environ['PATH']}", "REQUIRE_NON_ROOT": "true"}

    result = subprocess.run([str(ENTRYPOINT), "true"], env=env, capture_output=True, text=True)

    assert result.returncode == 1
    assert "nespúšťam" in result.stdout


# ---------- token obnovy hesla ----------

@pytest.mark.parametrize("path", ["/reset-password/abc.def.ghi", "/forgot-password"])
def test_reset_pages_are_not_cached_and_send_no_referrer(path):

    response = TestClient(app).get(path)

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["pragma"] == "no-cache"


def test_other_pages_keep_the_default_referrer_policy():

    response = TestClient(app).get("/login")

    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "no-store" not in response.headers.get("cache-control", "")


@pytest.mark.parametrize("raw, expected", [
    ("/reset-password/abc.DEF-123_x", "/reset-password/[skryte]"),
    ("/reset-password/abc.def?x=1", "/reset-password/[skryte]?x=1"),
    ("/zakaznici", "/zakaznici"),
    ("/forgot-password", "/forgot-password"),
])
def test_redact_function(raw, expected):

    assert log_filters.redact(raw) == expected


def test_access_log_never_contains_the_reset_token(caplog):

    token = "SECRET-TOKEN-abc.123.xyz"

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5555", "POST", f"/reset-password/{token}", "1.1", 303), None,
    )

    log_filters.RedactResetTokens().filter(record)

    assert token not in record.getMessage()
    assert "/reset-password/[skryte]" in record.getMessage()


def test_filter_is_installed_on_uvicorn_access_logger_once():

    log_filters.install()
    log_filters.install()

    filters = [f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, log_filters.RedactResetTokens)]

    assert len(filters) == 1


def test_real_request_to_reset_link_does_not_log_token(caplog):
    """Aj keby niečo zalogovalo URL cez uvicorn.access, token tam nesmie byť."""

    token = "TAJNY-TOKEN-1234"
    logger = logging.getLogger("uvicorn.access")
    logger.propagate = True

    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:1", "GET", f"/reset-password/{token}", "1.1", 400)

    assert token not in caplog.text
