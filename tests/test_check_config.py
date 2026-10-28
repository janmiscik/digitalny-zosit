"""tools/check_config.py: kontrola produkčnej konfigurácie podľa spôsobu používania."""

import importlib.util
import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_config", ROOT / "tools" / "check_config.py")
check_config = importlib.util.module_from_spec(spec)
sys.modules["check_config"] = check_config
spec.loader.exec_module(check_config)

SECRET = secrets.token_hex(32)
TOKEN = secrets.token_hex(24)
ENC = secrets.token_hex(16)


def good_env(**overrides):

    env = {
        "APP_ENV": "production", "SECRET_KEY": SECRET, "SESSION_HTTPS_ONLY": "true",
        "REGISTRATION_ENABLED": "false", "SERVER_ADMINS": "majitel", "MONITOR_TOKEN": TOKEN,
        "OFFSITE_BACKUP_BUCKET": "b", "OFFSITE_BACKUP_ACCESS_KEY_ID": "k", "OFFSITE_BACKUP_SECRET_ACCESS_KEY": "s",
        "BACKUP_ENCRYPTION_KEY": ENC,
    }
    env.update(overrides)

    return {key: value for key, value in env.items() if value is not None}


def levels(env, profile="managed"):

    result = {}

    for finding in check_config.check_config(env, profile):
        result.setdefault(finding.name, []).append(finding.level)

    return result


def errors(env, profile="managed"):

    return sorted(name for name, found in levels(env, profile).items() if "ERROR" in found)


def warnings(env, profile="managed"):

    return sorted(name for name, found in levels(env, profile).items() if "WARN" in found)


def test_good_managed_config_has_no_errors_or_warnings():

    assert errors(good_env()) == [] and warnings(good_env()) == []


def test_development_mode_and_weak_secret_are_errors():

    assert "APP_ENV" in errors(good_env(APP_ENV="development"))
    assert "APP_ENV" in errors(good_env(APP_ENV="flase"))
    assert "SECRET_KEY" in errors(good_env(SECRET_KEY="change-me"))
    assert "SECRET_KEY" in errors(good_env(SECRET_KEY=None))
    assert "SESSION_HTTPS_ONLY" in errors(good_env(SESSION_HTTPS_ONLY="false"))


def test_unset_app_env_means_production():

    assert "APP_ENV" not in errors(good_env(APP_ENV=None))


def test_managed_profile_requires_closed_registration_and_admins():

    assert "REGISTRATION_ENABLED" in errors(good_env(REGISTRATION_ENABLED="true"))
    assert "REGISTRATION_ENABLED" in errors(good_env(REGISTRATION_ENABLED="flase"))
    assert "SERVER_ADMINS" in errors(good_env(SERVER_ADMINS=None))
    assert "SERVER_ADMINS" in errors(good_env(SERVER_ADMINS=" , "))


def test_single_profile_requires_closed_registration_but_not_admins():

    assert "REGISTRATION_ENABLED" in errors(good_env(REGISTRATION_ENABLED="true"), "single")
    assert errors(good_env(SERVER_ADMINS=None), "single") == []


def test_self_service_profile_wants_open_registration_with_a_cap():

    env = good_env(REGISTRATION_ENABLED="true")

    assert errors(env, "self-service") == []
    assert "MAX_ACCOUNTS" in warnings(env, "self-service")
    assert "MAX_ACCOUNTS" not in warnings({**env, "MAX_ACCOUNTS": "50"}, "self-service")
    assert "MAX_ACCOUNTS" in errors({**env, "MAX_ACCOUNTS": "-3"}, "self-service")
    assert "MAX_ACCOUNTS" in errors({**env, "MAX_ACCOUNTS": "veľa"}, "self-service")
    assert "REGISTRATION_ENABLED" in warnings(good_env(), "self-service")


def test_backup_settings():

    assert "BACKUP_ENABLED" in errors(good_env(BACKUP_ENABLED="false"))
    assert "OFFSITE_BACKUP_*" in warnings(good_env(OFFSITE_BACKUP_BUCKET=None, OFFSITE_BACKUP_ACCESS_KEY_ID=None,
                                                    OFFSITE_BACKUP_SECRET_ACCESS_KEY=None, BACKUP_ENCRYPTION_KEY=None))
    assert "OFFSITE_BACKUP_*" in errors(good_env(OFFSITE_BACKUP_BUCKET=None))
    assert "BACKUP_ENCRYPTION_KEY" in warnings(good_env(BACKUP_ENCRYPTION_KEY=None))
    assert "BACKUP_ENCRYPTION_KEY" in errors(good_env(BACKUP_ENCRYPTION_KEY="krátke"))


def test_monitoring_and_docs_settings():

    assert "MONITOR_TOKEN" in warnings(good_env(MONITOR_TOKEN=None))
    assert "MONITOR_TOKEN" in warnings(good_env(MONITOR_TOKEN="krátky-token"))
    assert "ENABLE_API_DOCS" in warnings(good_env(ENABLE_API_DOCS="true"))


def test_mail_settings_need_https_base_url():

    mail = {"SMTP_HOST": "smtp.example.com", "SMTP_FROM": "a@example.com"}

    assert "APP_BASE_URL" in errors(good_env(**mail, APP_BASE_URL="http://appka.example.com"))
    assert "APP_BASE_URL" not in errors(good_env(**mail, APP_BASE_URL="https://appka.example.com"))
    assert "SMTP_*" in warnings(good_env(**mail))


def test_forwarded_ips_wildcard_warns_outside_railway():

    assert "FORWARDED_ALLOW_IPS" in warnings(good_env(FORWARDED_ALLOW_IPS="*"))
    assert "FORWARDED_ALLOW_IPS" not in warnings(good_env(FORWARDED_ALLOW_IPS="*", RAILWAY_PROJECT_ID="abc"))


def test_unknown_profile_is_rejected():

    with pytest.raises(ValueError):
        check_config.check_config({}, "nieco")


def test_cli_reads_env_file_exits_with_code_and_never_prints_secrets(tmp_path, capsys):

    path = tmp_path / ".env"
    path.write_text("\n".join(f"{key}={value}" for key, value in good_env().items()), encoding="utf-8")

    assert check_config.main(["x", "--profile", "managed", "--env-file", str(path)]) == 0

    out = capsys.readouterr().out

    assert "Chýb: 0" in out
    for secret in (SECRET, TOKEN, ENC):
        assert secret not in out

    path.write_text(f"SECRET_KEY={SECRET}\nREGISTRATION_ENABLED=true\n", encoding="utf-8")

    assert check_config.main(["x", "--profile", "managed", "--env-file", str(path)]) == 1
    assert SECRET not in capsys.readouterr().out

    assert check_config.main(["x", "--profile", "managed", "--env-file", str(tmp_path / "nie.env")]) == 2


def test_require_non_root_is_reported_with_verification_hint():

    off = [f for f in check_config.check_config(good_env(), "managed") if f.name == "REQUIRE_NON_ROOT"]
    on = [f for f in check_config.check_config(good_env(REQUIRE_NON_ROOT="true"), "managed") if f.name == "REQUIRE_NON_ROOT"]

    assert len(off) == 1 and off[0].level == "OK" and "Najprv over" in off[0].message
    assert len(on) == 1 and on[0].level == "OK" and "zapnuté" in on[0].message
