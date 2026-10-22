"""Nahrávanie záloh mimo servera (S3-kompatibilné úložisko) - s atrapou klienta."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import auto_backup
import offsite_backup
import tenancy
from main import app

ENV = {
    "OFFSITE_BACKUP_BUCKET": "moj-bucket",
    "OFFSITE_BACKUP_ACCESS_KEY_ID": "kluc",
    "OFFSITE_BACKUP_SECRET_ACCESS_KEY": "tajomstvo",
}


class FakeS3:
    """Minimálna atrapa S3: drží objekty v pamäti."""

    def __init__(self, truncate_uploads=False, fail_upload=False):

        self.objects = {}
        self.truncate_uploads = truncate_uploads
        self.fail_upload = fail_upload
        self.counter = 0
        self.corrupt_downloads = False
        self.same_size_corruption = False

    def upload_file(self, filename, bucket, key, ExtraArgs=None):

        if self.fail_upload:
            raise ConnectionError("sieť nedostupná")

        data = open(filename, "rb").read()

        if self.truncate_uploads:
            data = data[:-5]

        if self.same_size_corruption:
            data = bytes([data[0] ^ 1]) + data[1:]

        self.counter += 1
        self.objects[(bucket, key)] = {
            "data": data,
            "meta": (ExtraArgs or {}).get("Metadata", {}),
            "modified": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=self.counter),
        }

    def download_file(self, bucket, key, filename):

        data = self.objects[(bucket, key)]["data"]

        if self.corrupt_downloads:
            data = bytes([data[0] ^ 1]) + data[1:]

        with open(filename, "wb") as handle:
            handle.write(data)

    def head_object(self, Bucket, Key):

        return {"ContentLength": len(self.objects[(Bucket, Key)]["data"])}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):

        items = [
            {"Key": key, "LastModified": value["modified"]}
            for (bucket, key), value in self.objects.items()
            if bucket == Bucket and key.startswith(Prefix)
        ]

        return {"Contents": items, "IsTruncated": False}

    def delete_object(self, Bucket, Key):

        del self.objects[(Bucket, Key)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")
    monkeypatch.setenv("BACKUP_MIN_FREE_MB", "1")

    for name in list(ENV) + ["OFFSITE_BACKUP_ENDPOINT", "OFFSITE_BACKUP_KEEP", "OFFSITE_BACKUP_PREFIX"]:
        monkeypatch.delenv(name, raising=False)

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

    yield

    app.dependency_overrides.clear()
    tenancy._engine_cache.clear()


@pytest.fixture
def s3(monkeypatch):

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    fake = FakeS3()
    monkeypatch.setattr(offsite_backup, "_client", lambda: fake)

    return fake


def make_account(slug="firma"):

    tenancy.provision_account(slug)

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))
    conn.execute("INSERT INTO customers (name) VALUES ('Z')")
    conn.commit()
    conn.close()

    return slug


def local_file(tmp_path, content=b"obsah zalohy", name="rucne-2026-01-01.zip"):

    path = tmp_path / name
    path.write_bytes(content)

    return path


# ---------- zapnutie ----------

def test_disabled_without_env():

    assert offsite_backup.enabled() is False
    assert offsite_backup.upload_backup("firma", __import__("pathlib").Path("x")).ok is False


@pytest.mark.parametrize("missing", list(ENV))
def test_disabled_when_any_required_variable_missing(monkeypatch, missing):

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    monkeypatch.setenv(missing, "  ")

    assert offsite_backup.enabled() is False


# ---------- nahrávanie ----------

def test_upload_success_uses_prefix_and_slug(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("OFFSITE_BACKUP_PREFIX", "/moje/zalohy/")

    result = offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert result.ok and result.key == "moje/zalohy/firma/rucne-2026-01-01.zip"
    assert s3.objects[("moj-bucket", result.key)]["data"] == b"obsah zalohy"


def test_size_mismatch_is_reported_as_failure(monkeypatch, tmp_path, s3):

    s3.truncate_uploads = True

    result = offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert result.ok is False and "nezhoduje" in result.error


def test_network_error_never_raises(s3, tmp_path):

    s3.fail_upload = True

    result = offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert result.ok is False
    assert "ConnectionError" in result.error


def test_retention_keeps_newest_per_account_only(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("OFFSITE_BACKUP_KEEP", "3")

    for index in range(5):
        offsite_backup.upload_backup("firma", local_file(tmp_path, name=f"a{index}.zip"))

    offsite_backup.upload_backup("firma-2", local_file(tmp_path, name="cudzia.zip"))

    keys = sorted(key for (_bucket, key) in s3.objects)

    assert keys == [
        "digitalny-zosit/firma-2/cudzia.zip",              # iné konto sa nedotkne (aj pri podobnom slugu)
        "digitalny-zosit/firma/a2.zip",
        "digitalny-zosit/firma/a3.zip",
        "digitalny-zosit/firma/a4.zip",
    ]


def test_invalid_keep_falls_back_to_default(monkeypatch):

    monkeypatch.setenv("OFFSITE_BACKUP_KEEP", "abc")

    assert offsite_backup._keep() == 30


# ---------- napojenie na zálohovanie ----------

def test_run_backup_uploads_and_records_status(s3):

    slug = make_account()

    result = auto_backup.run_backup(slug, "manual")

    assert result.ok
    assert ("moj-bucket", f"digitalny-zosit/{slug}/{result.file}") in s3.objects

    status = auto_backup.read_status(slug)

    assert status["offsite"]["ok"] is True and status["offsite"]["file"] == result.file


def test_failed_upload_does_not_fail_local_backup(s3):

    s3.fail_upload = True
    slug = make_account()

    result = auto_backup.run_backup(slug, "manual")

    assert result.ok                                        # lokálna záloha je v poriadku
    assert (tenancy.account_backups_dir(slug) / result.file).exists()
    assert auto_backup.read_status(slug)["offsite"]["ok"] is False


def test_uploaded_backup_is_restorable(s3, tmp_path):
    """Obsah v bucketi je presne lokálna záloha, ktorá prejde testom obnovy."""

    slug = make_account()

    result = auto_backup.run_backup(slug, "manual")

    remote = s3.objects[("moj-bucket", f"digitalny-zosit/{slug}/{result.file}")]["data"]
    copy = tmp_path / "z-bucketu.zip"
    copy.write_bytes(remote)

    report = auto_backup.verify_backup_zip(copy)

    assert report.ok and report.counts["customers"] == 1


def test_no_upload_when_disabled():

    slug = make_account()

    auto_backup.run_backup(slug, "manual")

    assert "offsite" not in auto_backup.read_status(slug)


# ---------- upozornenia v Nastaveniach ----------

def test_overview_warnings_for_each_state(s3, monkeypatch):

    slug = make_account()
    monkeypatch.setenv("BACKUP_ENABLED", "true")

    auto_backup.run_backup(slug, "manual")
    assert [w for w in auto_backup.backup_overview(slug)["warnings"]] == []
    assert auto_backup.backup_overview(slug)["offsite_enabled"] is True

    s3.fail_upload = True
    auto_backup.run_backup(slug, "manual")

    assert any("mimo servera zlyhalo" in w for w in auto_backup.backup_overview(slug)["warnings"])

    for name in ENV:
        monkeypatch.delenv(name)

    assert any("len na tom istom disku" in w for w in auto_backup.backup_overview(slug)["warnings"])


def test_real_client_is_built_from_env_without_network(monkeypatch):

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)

    monkeypatch.setenv("OFFSITE_BACKUP_ENDPOINT", "https://s3.example.com")
    monkeypatch.setenv("OFFSITE_BACKUP_REGION", "eu-central-003")

    client = offsite_backup._client()

    assert client.meta.endpoint_url == "https://s3.example.com"
    assert client.meta.region_name == "eu-central-003"


def test_settings_page_shows_offsite_state(s3):

    import re
    from fastapi.testclient import TestClient

    client = TestClient(app)
    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/register").text).group(1)

    client.post("/register", data={"csrf_token": token, "username": "offs", "password": "heslo1234",
                                   "password_confirm": "heslo1234"}, follow_redirects=False)

    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/settings").text).group(1)
    client.post("/settings/backups/run", data={"csrf_token": token}, follow_redirects=False)

    page = client.get("/settings").text

    assert "mimo servera" in page and "✅ nahratá" in page


# ---------- obsah, nielen veľkosť ----------

def test_same_size_but_different_content_is_detected(s3, tmp_path):

    s3.same_size_corruption = True

    result = offsite_backup.upload_backup("firma", local_file(tmp_path))

    assert result.ok is False
    assert "SHA-256" in result.error


def test_upload_stores_sha256_and_marks_verified(s3, tmp_path):

    path = local_file(tmp_path)

    result = offsite_backup.upload_backup("firma", path)

    assert result.ok and result.verified
    assert result.sha256 == offsite_backup.file_sha256(path)
    assert s3.objects[("moj-bucket", result.key)]["meta"] == {"sha256": result.sha256}


def test_validator_failure_marks_backup_as_failed_and_keeps_old_ones(s3, tmp_path, monkeypatch):

    monkeypatch.setenv("OFFSITE_BACKUP_KEEP", "1")

    offsite_backup.upload_backup("firma", local_file(tmp_path, name="stara.zip"))

    result = offsite_backup.upload_backup(
        "firma", local_file(tmp_path, name="nova.zip"),
        validator=lambda path: ["poškodená databáza"],
    )

    assert result.ok is False and "testom obnovy" in result.error
    # retencia sa pri neoverenej zálohe nespúšťa - stará záloha ostáva
    assert ("moj-bucket", "digitalny-zosit/firma/stara.zip") in s3.objects


def test_validator_receives_downloaded_copy_and_temp_file_is_removed(s3, tmp_path):

    seen = {}

    def validator(path):

        seen["path"] = path
        seen["data"] = path.read_bytes()
        return []

    result = offsite_backup.upload_backup("firma", local_file(tmp_path), validator=validator)

    assert result.ok and seen["data"] == b"obsah zalohy"
    assert not seen["path"].exists()


def test_run_backup_verifies_downloaded_copy_with_restore_test(s3):

    slug = make_account()

    auto_backup.run_backup(slug, "manual")

    offsite = auto_backup.read_status(slug)["offsite"]

    assert offsite["ok"] and offsite["verified"] and len(offsite["sha256"]) == 64


def test_run_backup_reports_corrupted_remote_copy(s3):

    s3.same_size_corruption = True
    slug = make_account()

    auto_backup.run_backup(slug, "manual")

    offsite = auto_backup.read_status(slug)["offsite"]

    assert offsite["ok"] is False and "SHA-256" in offsite["error"]


def test_retention_failure_does_not_fail_a_verified_upload(s3, tmp_path, monkeypatch):
    """Bucket so zámkom objektov nedovolí mazať - záloha je napriek tomu úspešná."""

    monkeypatch.setenv("OFFSITE_BACKUP_KEEP", "1")

    def locked(Bucket, Key):
        raise PermissionError("AccessDenied: objekt je zamknutý")

    offsite_backup.upload_backup("firma", local_file(tmp_path, name="a.zip"))
    offsite_backup.upload_backup("firma", local_file(tmp_path, name="b.zip"))   # pokus o retenciu prebehne

    s3.delete_object = locked

    result = offsite_backup.upload_backup("firma", local_file(tmp_path, name="c.zip"))

    assert result.ok and result.verified
    assert "nepodarilo zmazať" in result.warning
    assert ("moj-bucket", "digitalny-zosit/firma/c.zip") in s3.objects


def test_run_backup_records_retention_warning_and_overview_shows_it(s3, monkeypatch):

    monkeypatch.setenv("OFFSITE_BACKUP_KEEP", "1")

    slug = make_account()
    auto_backup.run_backup(slug, "manual")

    def locked(Bucket, Key):
        raise PermissionError("AccessDenied")

    s3.delete_object = locked

    auto_backup.run_backup(slug, "manual")

    status = auto_backup.read_status(slug)["offsite"]

    assert status["ok"] is True and status["warning"]
    assert any("Kópia mimo servera" in w for w in auto_backup.backup_overview(slug)["warnings"])
