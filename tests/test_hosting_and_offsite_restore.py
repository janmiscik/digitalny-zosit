"""docker-compose pre vlastný server a CLI na stiahnutie záloh z bucketu."""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest

import backup_crypto
import offsite_backup
import offsite_restore
from tests.test_offsite_backup import ENV, FakeS3

ROOT = Path(__file__).resolve().parents[1]
PASS = "toto-je-dlhe-nahodne-heslo-123"


# ---------- docker-compose ----------

def test_compose_pins_production_and_internal_proxy_trust():

    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "APP_ENV: production" in text
    assert 'SESSION_HTTPS_ONLY: "true"' in text
    assert "FORWARDED_ALLOW_IPS: 172.30.0.0/24" in text and "FORWARDED_ALLOW_IPS: \"*\"" not in text
    assert "ACCOUNTS_DATA_DIR: /app/data/accounts" in text
    assert "zosit-data:/app/data" in text
    # appka nie je publikovaná priamo, len Caddy
    assert 'ports:\n      - "80:80"' in text and '"8000:8000"' not in text


def test_caddyfile_proxies_to_app():

    assert "reverse_proxy app:8000" in (ROOT / "Caddyfile").read_text(encoding="utf-8")


def test_downloaded_backups_are_never_committed_or_put_into_the_image():

    for name in (".gitignore", ".dockerignore"):
        assert "obnova/" in (ROOT / name).read_text(encoding="utf-8")


# ---------- offsite_restore ----------

@pytest.fixture
def bucket(monkeypatch):

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    fake = FakeS3()
    monkeypatch.setattr(offsite_backup, "_client", lambda: fake)

    return fake


def put(fake, account, name, data, minutes):

    fake.objects[("moj-bucket", f"digitalny-zosit/{account}/{name}")] = {
        "data": data, "meta": {},
        "modified": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
    }


def test_list_and_download_latest_plain(bucket, tmp_path, capsys):

    put(bucket, "admin", "stara.zip", b"stara", 1)
    put(bucket, "admin", "nova.zip", b"nova", 5)
    put(bucket, "firma", "ina.zip", b"ina", 9)

    rows = offsite_restore.list_backups(bucket, "moj-bucket", "admin")

    assert [r["name"] for r in rows] == ["nova.zip", "stara.zip"]
    assert {r["account"] for r in offsite_restore.list_backups(bucket, "moj-bucket")} == {"admin", "firma"}

    assert offsite_restore.main(["x", "--download", "--account", "admin", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "admin-nova.zip").read_bytes() == b"nova"


def test_download_decrypts_encrypted_backup(bucket, tmp_path, monkeypatch):

    (tmp_path / "plain.zip").write_bytes(b"obsah zalohy" * 100)
    backup_crypto.encrypt_file(tmp_path / "plain.zip", tmp_path / "enc", PASS)
    put(bucket, "admin", "rucne-1.zip.enc", (tmp_path / "enc").read_bytes(), 3)

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", PASS)
    out = tmp_path / "out"

    assert offsite_restore.main(["x", "--download", "--account", "admin", "--out", str(out)]) == 0
    assert (out / "admin-rucne-1.zip").read_bytes() == b"obsah zalohy" * 100


def test_wrong_password_fails_without_leaving_files(bucket, tmp_path, monkeypatch):

    (tmp_path / "plain.zip").write_bytes(b"x" * 500)
    backup_crypto.encrypt_file(tmp_path / "plain.zip", tmp_path / "enc", PASS)
    put(bucket, "admin", "a.zip.enc", (tmp_path / "enc").read_bytes(), 3)

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "ine-heslo-ine-heslo-1234567")
    out = tmp_path / "out"

    assert offsite_restore.main(["x", "--download", "--account", "admin", "--out", str(out)]) == 1
    assert list(out.glob("*")) == []


def test_existing_file_is_never_overwritten(bucket, tmp_path):

    put(bucket, "admin", "a.zip", b"nove", 3)
    (tmp_path / "admin-a.zip").write_bytes(b"stare")

    assert offsite_restore.main(["x", "--download", "--account", "admin", "--out", str(tmp_path)]) == 1
    assert (tmp_path / "admin-a.zip").read_bytes() == b"stare"


def test_unknown_account_and_unsafe_names(bucket, tmp_path):

    assert offsite_restore.main(["x", "--download", "--account", "nikto", "--out", str(tmp_path)]) == 1

    with pytest.raises(ValueError):
        offsite_restore.download_backup(
            bucket, "moj-bucket", {"account": "a", "name": "../../x.zip", "key": "k"}, tmp_path, None
        )


def test_requires_configuration(monkeypatch, tmp_path):

    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    assert offsite_restore.main(["x", "--list"]) == 2
