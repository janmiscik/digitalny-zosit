"""Monitoring záloh a migrácií: /health/details."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import auto_backup
import monitoring
import offsite_backup
import tenancy
from main import app
from tests.test_multi_tenant import isolated_tenancy, register  # noqa: F401

TOKEN = "monitor-token-123"
SLUG = "firma"


@pytest.fixture
def client(monkeypatch):

    monkeypatch.setenv("MONITOR_TOKEN", TOKEN)
    monkeypatch.setenv("BACKUP_ENABLED", "true")
    monkeypatch.setenv("BACKUP_INTERVAL_HOURS", "24")
    for name in offsite_backup.REQUIRED:
        monkeypatch.delenv(name, raising=False)

    http = TestClient(app)
    assert register(http, SLUG).status_code == 303

    return http


def get(client, token=TOKEN, query=False):

    if query:
        return client.get(f"/health/details?token={token}")

    return client.get("/health/details", headers={"Authorization": f"Bearer {token}"})


def write_status(**fields):

    auto_backup._write_status(SLUG, fields)


def now_iso(delta=timedelta(0)):

    return (auto_backup.utc_now() - delta).isoformat()


def test_disabled_without_token(client, monkeypatch):

    monkeypatch.delenv("MONITOR_TOKEN")

    assert client.get("/health/details").status_code == 404


def test_rejects_missing_or_wrong_token(client):

    assert client.get("/health/details").status_code == 401
    assert get(client, "zly-token").status_code == 401
    assert get(client, "").status_code == 401


def test_ok_when_backup_is_fresh_and_schema_current(client):

    write_status(last_success_at=now_iso())

    response = get(client)

    assert response.status_code == 200
    body = response.json()

    assert (body["status"], body["accounts"], body["problems"]) == ("ok", 1, [])


def test_query_token_is_accepted(client):

    write_status(last_success_at=now_iso())

    assert get(client, query=True).status_code == 200


def test_never_backed_up_is_degraded(client):

    # registrácia mohla zálohu vytvoriť - vymažeme stav
    auto_backup.status_path(SLUG).unlink(missing_ok=True)

    response = get(client)

    assert response.status_code == 503
    assert response.json()["problems"][0]["kind"] == "backup"


def test_stale_backup_is_degraded(client):

    write_status(last_success_at=now_iso(timedelta(hours=60)))

    body = get(client).json()

    assert body["status"] == "degraded"
    assert {"account": SLUG, "kind": "backup", "detail": "posledná záloha je zastaraná"} in body["problems"]


def test_last_error_is_reported_without_leaking_its_text(client):

    write_status(last_success_at=now_iso(), last_error="/app/data/secret/path: chyba disku")

    response = get(client)

    assert response.status_code == 503
    assert "secret" not in response.text and "/app" not in response.text


def test_backups_disabled_by_config_is_not_a_backup_problem(client, monkeypatch):

    monkeypatch.setenv("BACKUP_ENABLED", "false")
    auto_backup.status_path(SLUG).unlink(missing_ok=True)

    assert get(client).status_code == 200


def test_pending_migration_is_degraded(client, monkeypatch):

    write_status(last_success_at=now_iso())
    monkeypatch.setattr(tenancy, "account_needs_migration", lambda slug: True)

    body = get(client).json()

    assert any(p["kind"] == "migration" for p in body["problems"])


def test_migration_check_error_does_not_crash_monitoring(client, monkeypatch):

    write_status(last_success_at=now_iso())

    def boom(slug):
        raise RuntimeError("tajna chyba")

    monkeypatch.setattr(tenancy, "account_needs_migration", boom)

    response = get(client)

    assert response.status_code == 503
    assert "tajna" not in response.text


def _drop_trigger(sql):

    import sqlite3

    connection = sqlite3.connect(tenancy.account_database_path(SLUG))
    connection.execute(sql)
    connection.commit()
    connection.close()


def test_healthy_account_has_no_guard_problem(client):

    write_status(last_success_at=now_iso())

    assert not [p for p in get(client).json()["problems"] if p["kind"] == "guards"]


def test_missing_guard_trigger_is_degraded(client):

    write_status(last_success_at=now_iso())
    _drop_trigger("DROP TRIGGER invoices_no_delete_issued")

    body = get(client).json()
    guards = [p for p in body["problems"] if p["kind"] == "guards"]

    assert guards and "invoices_no_delete_issued" in guards[0]["detail"]


def test_weakened_guard_trigger_is_degraded(client):

    write_status(last_success_at=now_iso())
    _drop_trigger("DROP TRIGGER audit_log_no_delete")
    _drop_trigger("CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log WHEN 0 BEGIN SELECT 1; END")

    guards = [p for p in get(client).json()["problems"] if p["kind"] == "guards"]

    assert guards and "audit_log_no_delete" in guards[0]["detail"]


def configure_offsite(monkeypatch):

    monkeypatch.setenv("OFFSITE_BACKUP_BUCKET", "b")
    monkeypatch.setenv("OFFSITE_BACKUP_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("OFFSITE_BACKUP_SECRET_ACCESS_KEY", "s")


@pytest.mark.parametrize("offsite, expected", [
    (None, "zatiaľ neprebehla"),
    ({"ok": False, "at": "x"}, "zlyhalo"),
    ({"ok": True, "at": "2020-01-01T00:00:00+00:00"}, "zastaraná"),
])
def test_offsite_problems_when_enabled(client, monkeypatch, offsite, expected):

    configure_offsite(monkeypatch)
    write_status(last_success_at=now_iso(), **({"offsite": offsite} if offsite else {}))

    body = get(client).json()

    assert any(p["kind"] == "offsite" and expected in p["detail"] for p in body["problems"])


def test_offsite_fresh_and_ok_is_fine(client, monkeypatch):

    configure_offsite(monkeypatch)
    write_status(last_success_at=now_iso(), offsite={"ok": True, "at": now_iso()})

    assert get(client).status_code == 200


def test_plain_health_is_unaffected_by_stale_backups(client):

    write_status(last_success_at=now_iso(timedelta(days=30)))

    assert client.get("/health").json() == {"status": "ok"}


def test_unrecovered_interrupted_restore_is_degraded(client):

    import backup_utils

    write_status(last_success_at=now_iso())
    backup_utils.journal_path(SLUG).write_text("{}", encoding="utf-8")

    body = get(client).json()

    assert any(p["kind"] == "restore" for p in body["problems"])


# ---------- chyby servera, disk, plánovač ----------

@pytest.fixture(autouse=True)
def clean_error_counter():

    monitoring._errors.clear()

    yield

    monitoring._errors.clear()


def fresh_backup_status():

    write_status(last_success_at=now_iso(), last_error=None)


def test_many_server_errors_make_status_degraded(client, monkeypatch):

    fresh_backup_status()
    monkeypatch.setenv("MONITOR_MAX_ERRORS_PER_HOUR", "3")

    for _ in range(2):
        monitoring.record_server_error()

    assert get(client).status_code == 200

    monitoring.record_server_error()

    response = get(client)

    assert response.status_code == 503
    assert any(p["kind"] == "errors" for p in response.json()["problems"])


def test_old_errors_are_not_counted():

    monitoring._errors.extend([1000.0, 1001.0])

    assert monitoring.recent_server_errors() == 0
    assert monitoring.recent_server_errors(now=1002.0) == 2


def test_unhandled_exception_is_counted():

    import main

    boom = TestClient(main.app, raise_server_exceptions=False)

    @main.app.get("/__boom_test", include_in_schema=False)
    def boom_route():
        raise RuntimeError("test")

    try:

        before = monitoring.recent_server_errors()
        assert boom.get("/__boom_test").status_code in (303, 500)
        assert monitoring.recent_server_errors() == before + 1

    finally:

        # testovacia trasa sa nesmie dostať do ostatných testov
        main.app.router.routes[:] = [
            route for route in main.app.router.routes if getattr(route, "path", "") != "/__boom_test"
        ]


def test_low_disk_space_is_reported(client, monkeypatch):

    fresh_backup_status()

    class Usage:
        total = 1000 * 1024 * 1024
        free = 50 * 1024 * 1024

    monkeypatch.setattr(monitoring.shutil, "disk_usage", lambda path: Usage)

    response = get(client)

    assert response.status_code == 503
    assert any(p["kind"] == "disk" for p in response.json()["problems"])


def test_enough_disk_space_is_fine(client, monkeypatch):

    class Usage:
        total = 10_000 * 1024 * 1024
        free = 5_000 * 1024 * 1024

    monkeypatch.setattr(monitoring.shutil, "disk_usage", lambda path: Usage)

    assert monitoring.disk_problem() is None


def test_dead_scheduler_is_reported(client, monkeypatch):

    fresh_backup_status()

    class Dead:
        def is_alive(self):
            return False

    monkeypatch.setattr(auto_backup, "_scheduler", Dead())

    response = get(client)

    assert response.status_code == 503
    assert any(p["kind"] == "scheduler" for p in response.json()["problems"])


def test_error_counter_survives_restart(client, monkeypatch):

    monitoring.record_server_error()
    monitoring.record_server_error()

    # simulácia reštartu: pamäť sa vynuluje, súbor ostane
    monitoring._errors.clear()

    assert monitoring.recent_server_errors() == 2


def test_old_persisted_errors_are_ignored(client):

    import time

    monitoring._error_file().parent.mkdir(parents=True, exist_ok=True)
    monitoring._error_file().write_text(f"1000.0\n{time.time()}\nnezmysel\n", encoding="utf-8")

    assert monitoring.recent_server_errors() == 1


def test_error_file_is_trimmed_and_write_failures_are_harmless(client, monkeypatch):

    for _ in range(monitoring.ERROR_FILE_MAX_LINES + 50):
        monitoring.record_server_error()

    assert len(monitoring._read_error_file()) <= monitoring.ERROR_FILE_MAX_LINES

    monkeypatch.setattr(monitoring, "_error_file", lambda: (_ for _ in ()).throw(OSError("disk")))

    monitoring.record_server_error()   # nesmie vyhodiť výnimku


def test_runtime_info_is_reported(client):

    write_status(last_success_at=now_iso())

    runtime = get(client).json()["runtime"]

    assert set(runtime) == {"user", "uid", "environment"}


def test_production_running_as_root_is_degraded(client, monkeypatch):

    write_status(last_success_at=now_iso())
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(monitoring.os, "geteuid", lambda: 0, raising=False)

    body = get(client).json()

    assert any(p["kind"] == "runtime" for p in body["problems"])
    assert body["runtime"]["environment"] == "production" and body["runtime"]["uid"] == 0


def test_production_as_normal_user_and_dev_as_root_are_fine(client, monkeypatch):

    write_status(last_success_at=now_iso())

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(monitoring.os, "geteuid", lambda: 1000, raising=False)
    assert not [p for p in get(client).json()["problems"] if p["kind"] == "runtime"]

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(monitoring.os, "geteuid", lambda: 0, raising=False)
    assert not [p for p in get(client).json()["problems"] if p["kind"] == "runtime"]


def test_startup_summary_reports_state_without_leaking_the_token(monkeypatch):

    monkeypatch.delenv("MONITOR_TOKEN", raising=False)
    assert "NIE JE" in monitoring.startup_summary()

    monkeypatch.setenv("MONITOR_TOKEN", "super-tajny-token-123")
    summary = monitoring.startup_summary()

    assert "nastavený" in summary and "21" in summary
    assert "super-tajny" not in summary

    import hashlib

    assert hashlib.sha256(b"super-tajny-token-123").hexdigest()[:8] in summary
