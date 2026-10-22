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
    assert response.json() == {"status": "ok", "accounts": 1, "problems": []}


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
