"""Kontrola SECRET_KEY pri štarte: chýbajúci, krátky, predvolený a nenáhodný kľúč."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

import security_config
from security_config import (
    resolve_environment,
    secret_key_problems,
    validate_environment,
    validate_secret_key,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STRONG_HEX = secrets.token_hex(32)
STRONG_URLSAFE = secrets.token_urlsafe(48)


@pytest.mark.parametrize("value", [STRONG_HEX, STRONG_URLSAFE, "  " + STRONG_HEX + "  ", secrets.token_hex(16)])
def test_random_keys_are_accepted(value):

    assert secret_key_problems(value) == []


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_is_reported(value):

    assert secret_key_problems(value) == ["SECRET_KEY nie je nastavený."]


@pytest.mark.parametrize("value", [
    "change-me", "changeme", "Change-Me", "CHANGE_ME", "change me", "secret", "your-secret-key",
    "test-secret-key", "ci-test-secret", "password", "dev", "default", "example", "placeholder",
])
def test_known_default_values_are_rejected(value):

    assert any("predvolená" in problem for problem in secret_key_problems(value))


@pytest.mark.parametrize("value", [
    "change-me" * 6,                          # dlhé, ale stále predvolená hodnota
    "xxx-change-me-" + secrets.token_hex(20),  # predvolená hodnota ukrytá v dlhšom reťazci
])
def test_default_hidden_in_long_value_is_rejected(value):

    assert any("predvolená" in problem for problem in secret_key_problems(value))


@pytest.mark.parametrize("value", ["a1b2c3", secrets.token_hex(8), "x" * 31])
def test_short_key_is_rejected(value):

    assert any("kratší" in problem for problem in secret_key_problems(value))


@pytest.mark.parametrize("value", ["a" * 64, "ab" * 40, "0123456789" * 6])
def test_non_random_key_is_rejected(value):

    assert any("rôznych znakov" in problem for problem in secret_key_problems(value))


def test_production_refuses_weak_key():

    with pytest.raises(RuntimeError, match="SECRET_KEY je slabý"):
        validate_secret_key("change-me", enforce=True)

    with pytest.raises(RuntimeError, match="token_hex"):
        validate_secret_key("krátky", enforce=True)


def test_missing_key_fails_even_in_development():

    with pytest.raises(RuntimeError, match="nie je nastavený"):
        validate_secret_key(None, enforce=False)


def test_development_only_warns_about_weak_key(caplog):

    with caplog.at_level("WARNING", logger=security_config.logger.name):
        validate_secret_key("test-secret-key", enforce=False)

    assert "SECRET_KEY je slabý" in caplog.text


def test_strong_key_passes_in_production():

    validate_secret_key(STRONG_HEX, enforce=True)


# ---------- skutočný štart appky (import main v samostatnom procese) ----------

def start_app(tmp_path, secret, https_only, app_env=None):

    env = {k: v for k, v in os.environ.items() if k not in ("SECRET_KEY", "SESSION_HTTPS_ONLY", "APP_ENV")}
    env.update({
        "ACCOUNTS_DATABASE_URL": f"sqlite:///{tmp_path / 'accounts.db'}",
        "ACCOUNTS_DATA_DIR": str(tmp_path / "accounts"),
        "SESSION_HTTPS_ONLY": https_only,
    })

    # Prázdna hodnota (nie "nenastavené"): load_dotenv() by inak doplnil
    # hodnoty z lokálneho .env vývojára a test by závisel od jeho počítača.
    env["SECRET_KEY"] = secret if secret is not None else ""
    env["APP_ENV"] = app_env if app_env is not None else ""

    # accounts.db musí mať schému, inak appka pri štarte upozorní na migrácie
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic_accounts.ini", "upgrade", "head"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, check=True,
    )

    return subprocess.run(
        [sys.executable, "-c", "import main"],
        cwd=PROJECT_ROOT, env=env,
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("secret", [None, "", "change-me", "krátky-kľúč", "test-secret-key"])
def test_app_refuses_to_start_in_production_with_bad_key(tmp_path, secret):

    result = start_app(tmp_path, secret, "true")

    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_app_starts_in_production_with_random_key(tmp_path):

    result = start_app(tmp_path, STRONG_HEX, "true")

    assert result.returncode == 0, result.stderr


def test_app_still_starts_locally_with_weak_key(tmp_path):

    result = start_app(tmp_path, "test-secret-key", "false", app_env="development")

    assert result.returncode == 0, result.stderr


def test_default_https_only_is_production_mode(tmp_path):
    """Bez SESSION_HTTPS_ONLY je predvolené true, teda slabý kľúč sa odmietne."""

    env_value = ""      # prázdne => predvolená hodnota "" sa nepovažuje za vypnutie
    result = start_app(tmp_path, "change-me", env_value)

    assert result.returncode != 0


# ---------- APP_ENV: produkčný režim nezávisí od HTTPS cookie ----------

STRONG = STRONG_HEX
WEAK = "change-me"


@pytest.mark.parametrize("value", [None, "", "  ", "production", "PRODUCTION", "prod"])
def test_production_is_default_and_explicit(value):

    assert resolve_environment(value) == "production"


@pytest.mark.parametrize("value", ["development", "dev", "local", "test", " Development "])
def test_development_values(value):

    assert resolve_environment(value) == "development"


@pytest.mark.parametrize("value", ["produktion", "staging", "1"])
def test_unknown_app_env_is_error(value):

    with pytest.raises(RuntimeError, match="APP_ENV"):
        resolve_environment(value)


@pytest.mark.parametrize("https_only", [True, False])
def test_production_rejects_weak_key_regardless_of_https_flag(https_only):
    """Jadro opravy: SESSION_HTTPS_ONLY=false nesmie obísť kontrolu kľúča."""

    with pytest.raises(RuntimeError):
        validate_environment("production", WEAK, https_only)


def test_unset_app_env_with_https_off_and_weak_key_is_refused():

    with pytest.raises(RuntimeError):
        validate_environment(None, WEAK, False)


def test_production_requires_https_cookie():

    with pytest.raises(RuntimeError, match="SESSION_HTTPS_ONLY=false"):
        validate_environment("production", STRONG, False)


def test_production_strong_key_https_ok():

    assert validate_environment("production", STRONG, True) == "production"


@pytest.mark.parametrize("https_only", [True, False])
def test_development_weak_key_only_warns(https_only, caplog):

    with caplog.at_level("WARNING", logger=security_config.logger.name):
        assert validate_environment("development", WEAK, https_only) == "development"

    assert "SECRET_KEY je slabý" in caplog.text


@pytest.mark.parametrize("https_only", [True, False])
def test_development_missing_key_still_fails(https_only):

    with pytest.raises(RuntimeError, match="nie je nastavený"):
        validate_environment("development", None, https_only)


def test_app_refuses_start_in_production_with_https_off_and_weak_key(tmp_path):

    result = start_app(tmp_path, WEAK, "false", app_env="production")

    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr or "SESSION_HTTPS_ONLY" in result.stderr


def test_app_refuses_start_without_app_env_and_https_off(tmp_path):

    result = start_app(tmp_path, WEAK, "false")

    assert result.returncode != 0


def test_app_refuses_start_in_production_with_https_off(tmp_path):

    result = start_app(tmp_path, STRONG, "false", app_env="production")

    assert result.returncode != 0
    assert "SESSION_HTTPS_ONLY" in result.stderr
