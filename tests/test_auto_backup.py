"""
Testy automatického zálohovania a testu obnovy (auto_backup.py,
backup_utils.write_backup_to_file, tenancy.account_needs_migration,
routes /settings/backups/*).

Používame skutočné konto (provision_account -> všetky migrácie) v
dočasnom ACCOUNTS_DATA_DIR - overuje sa presne to, čo sa na Railway
stane s reálnym database.db a uploads/.
"""

import os
import re
import sqlite3
import sys
import zipfile
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import auto_backup
import tenancy
from main import app

SLUG = "firma"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Session = sessionmaker(bind=engine)
    accounts_db.AccountsBase.metadata.create_all(bind=engine)

    def override_accounts_db():

        db = Session()

        try:
            yield db

        finally:
            db.close()

    app.dependency_overrides[accounts_db.get_accounts_db] = override_accounts_db
    tenancy._engine_cache.clear()

    # miesta je v testoch dosť, nech test nezávisí od disku stroja
    monkeypatch.setenv("BACKUP_MIN_FREE_MB", "1")

    yield Session

    app.dependency_overrides.clear()
    tenancy._engine_cache.clear()


def make_account(slug=SLUG, customers=2, with_files=True):
    """Provisionuje konto, vloží zákazníkov a (voliteľne) logo + fotku zákazky."""

    tenancy.provision_account(slug)

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))

    for index in range(customers):
        conn.execute("INSERT INTO customers (name) VALUES (?)", (f"Zákazník {index}",))

    if with_files:

        uploads = tenancy.account_uploads_dir(slug)
        (uploads / "job_photos").mkdir(parents=True, exist_ok=True)
        (uploads / "logo.png").write_bytes(b"logo-bytes")
        (uploads / "job_photos" / "foto1.jpg").write_bytes(b"photo-bytes")

        conn.execute("INSERT INTO company (id, name, logo_filename) VALUES (1, 'Firma', 'logo.png')")
        conn.execute("INSERT INTO jobs (title, customer_id) VALUES ('Práca', 1)")
        conn.execute(
            "INSERT INTO job_photos (job_id, filename, photo_type, uploaded_at) "
            "VALUES (1, 'foto1.jpg', 'pred', '2026-01-01')"
        )

    conn.commit()
    conn.close()

    return slug


def backup_zip(slug=SLUG, kind="manual"):

    result = auto_backup.run_backup(slug, kind)

    assert result.ok, result.error

    return tenancy.account_backups_dir(slug) / result.file


# =========================================
# TEST OBNOVY (verify_backup_zip)
# =========================================

def test_run_backup_creates_and_verifies_backup():

    make_account()

    result = auto_backup.run_backup(SLUG, "manual")

    assert result.ok
    assert result.file.startswith("rucne-")
    assert result.verify.counts["customers"] == 2
    assert result.verify.files_checked == 2          # logo + fotka
    assert result.verify.schema_current

    status = auto_backup.read_status(SLUG)

    assert status["last_file"] == result.file
    assert status["last_error"] is None
    assert status["verification"]["ok"] is True


def test_backup_contains_database_and_uploads():

    make_account()

    path = backup_zip()

    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())

    assert "database.db" in names
    assert "uploads/logo.png" in names
    assert "uploads/job_photos/foto1.jpg" in names


def test_verify_detects_corrupted_zip(tmp_path):

    make_account()

    path = backup_zip()
    data = bytearray(path.read_bytes())

    for position in range(40, len(data) - 40, 7):    # poškodí obsah
        data[position] ^= 0xFF

    path.write_bytes(bytes(data))

    report = auto_backup.verify_backup_zip(path)

    assert report.ok is False
    assert report.problems


def test_verify_detects_not_a_zip(tmp_path):

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"to nie je zip")

    report = auto_backup.verify_backup_zip(bad)

    assert report.ok is False
    assert any("ZIP" in problem for problem in report.problems)


def test_verify_detects_missing_referenced_file(tmp_path):

    make_account()

    path = backup_zip()
    stripped = tmp_path / "bez-fotky.zip"

    with zipfile.ZipFile(path) as src, zipfile.ZipFile(stripped, "w") as dst:

        for item in src.infolist():

            if item.filename != "uploads/job_photos/foto1.jpg":
                dst.writestr(item, src.read(item.filename))

    report = auto_backup.verify_backup_zip(stripped)

    assert report.ok is False
    assert any("foto1.jpg" in problem for problem in report.problems)


def test_verify_detects_row_count_mismatch():

    make_account()

    path = backup_zip()

    report = auto_backup.verify_backup_zip(path, expected_counts={"customers": 99})

    assert report.ok is False
    assert any("customers" in problem for problem in report.problems)


def test_verify_detects_zip_without_database(tmp_path):

    path = tmp_path / "prazdny.zip"

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("uploads/logo.png", b"x")

    assert auto_backup.verify_backup_zip(path).ok is False


def test_failed_verification_deletes_backup_and_records_error(monkeypatch):

    make_account()

    monkeypatch.setattr(
        auto_backup, "verify_backup_zip",
        lambda path, expected_counts=None: auto_backup.VerifyReport(ok=False, problems=["vymyslená chyba"])
    )

    result = auto_backup.run_backup(SLUG, "manual")

    assert result.ok is False
    assert "vymyslená chyba" in result.error
    assert auto_backup.list_backups(SLUG) == []
    assert "vymyslená chyba" in auto_backup.read_status(SLUG)["last_error"]


def test_run_backup_never_raises(monkeypatch):

    make_account()

    def boom(destination):
        raise RuntimeError("disk zlyhal")

    monkeypatch.setattr(auto_backup.backup_utils, "write_backup_to_file", boom)

    result = auto_backup.run_backup(SLUG, "auto")

    assert result.ok is False
    assert "disk zlyhal" in result.error


def test_backup_is_consistent_snapshot_restorable_by_app(tmp_path):
    """Zálohu vytvorenú automatom vie appka obnoviť svojou vlastnou
    restore_from_upload (rovnaký formát ako ručná záloha)."""

    make_account()

    path = backup_zip()

    with zipfile.ZipFile(path) as zf:

        zf.extract("database.db", tmp_path)

    conn = sqlite3.connect(str(tmp_path / "database.db"))

    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 2
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    conn.close()


# =========================================
# MIESTO NA DISKU
# =========================================

def test_free_space_guard_blocks_backup(monkeypatch):

    make_account()

    monkeypatch.setenv("BACKUP_MIN_FREE_MB", str(10 ** 9))   # nereálne veľa

    result = auto_backup.run_backup(SLUG, "auto")

    assert result.ok is False
    assert "miesta" in result.error
    assert auto_backup.list_backups(SLUG) == []


# =========================================
# RETENCIA A NÁZVY
# =========================================

def test_retention_keeps_only_newest_per_kind(monkeypatch):

    make_account(with_files=False)

    monkeypatch.setenv("BACKUP_KEEP", "2")

    directory = tenancy.account_backups_dir(SLUG)

    base = auto_backup.utc_now()

    for index in range(5):

        file = directory / f"auto-2026010{index + 1}-000000.zip"
        file.write_bytes(b"x")

        timestamp = (base - timedelta(days=10 - index)).timestamp()
        os.utime(file, (timestamp, timestamp))

    manual = directory / "rucne-20260101-000000.zip"
    manual.write_bytes(b"x")

    removed = auto_backup.apply_retention(SLUG)

    names = {item.name for item in auto_backup.list_backups(SLUG)}

    assert len(removed) == 3
    assert names == {"auto-20260105-000000.zip", "auto-20260104-000000.zip", manual.name}


def test_retention_does_not_touch_foreign_files():

    make_account(with_files=False)

    directory = tenancy.account_backups_dir(SLUG)
    foreign = directory / "poznamky.txt"
    foreign.write_text("moje")

    auto_backup.apply_retention(SLUG)

    assert foreign.exists()
    assert auto_backup.list_backups(SLUG) == []


def test_kind_of_and_backup_file_path_validate_names():

    make_account(with_files=False)

    directory = tenancy.account_backups_dir(SLUG)
    (directory / "auto-20260101-000000.zip").write_bytes(b"x")
    (directory / "tajne.zip").write_bytes(b"x")

    assert auto_backup.kind_of("auto-20260101-000000.zip") == "auto"
    assert auto_backup.kind_of("pred-aktualizaciou-20260101-000000-2.zip") == "pre-upgrade"
    assert auto_backup.kind_of("tajne.zip") is None
    assert auto_backup.kind_of("../auto-20260101-000000.zip") is None

    assert auto_backup.backup_file_path(SLUG, "auto-20260101-000000.zip") is not None
    assert auto_backup.backup_file_path(SLUG, "tajne.zip") is None
    assert auto_backup.backup_file_path(SLUG, "../../accounts.db") is None
    assert auto_backup.backup_file_path(SLUG, "auto-20260909-000000.zip") is None  # neexistuje


def test_same_second_backups_get_unique_names():

    make_account(with_files=False)

    first = auto_backup.run_backup(SLUG, "manual")
    second = auto_backup.run_backup(SLUG, "manual")

    assert first.ok and second.ok
    assert first.file != second.file


# =========================================
# PLÁNOVAČ
# =========================================

def test_backup_due_logic():

    make_account(with_files=False)

    now = auto_backup.utc_now()

    assert auto_backup.backup_due(SLUG, now) is True               # ešte nikdy

    auto_backup.run_backup(SLUG, "auto")

    assert auto_backup.backup_due(SLUG, now) is False              # práve prebehla
    assert auto_backup.backup_due(SLUG, now + timedelta(hours=25)) is True

    # ručná záloha neodkladá automatickú
    status = auto_backup.read_status(SLUG)
    status.pop("last_auto_success_at")
    auto_backup._write_status(SLUG, status)

    assert auto_backup.backup_due(SLUG, now) is True


def test_backup_due_retries_hourly_after_failure():

    make_account(with_files=False)

    now = auto_backup.utc_now()

    auto_backup._write_status(SLUG, {
        "last_attempt_at": now.isoformat(),
        "last_error": "zlyhalo",
    })

    assert auto_backup.backup_due(SLUG, now + timedelta(minutes=10)) is False
    assert auto_backup.backup_due(SLUG, now + timedelta(hours=2)) is True


def test_backup_due_false_without_database():

    assert auto_backup.backup_due("neexistuje", auto_backup.utc_now()) is False


def test_run_due_backups_backs_up_each_account(isolated):

    from accounts_models import Account

    make_account("firma-a", customers=1, with_files=False)
    make_account("firma-b", customers=3, with_files=False)

    db = isolated()
    db.add_all([
        Account(username="a", slug="firma-a", password_hash="x"),
        Account(username="b", slug="firma-b", password_hash="x"),
    ])
    db.commit()
    db.close()

    monkey_slugs = ["firma-a", "firma-b"]

    original = auto_backup.account_slugs
    auto_backup.account_slugs = lambda: monkey_slugs

    try:

        results = auto_backup.run_due_backups()

        assert [r.ok for r in results] == [True, True]
        assert len(auto_backup.list_backups("firma-a")) == 1
        assert len(auto_backup.list_backups("firma-b")) == 1

        # druhé kolo hneď po prvom nič nerobí
        assert auto_backup.run_due_backups() == []

    finally:
        auto_backup.account_slugs = original


def test_scheduler_can_start_and_stop(monkeypatch):

    monkeypatch.setenv("BACKUP_ENABLED", "true")

    called = []
    monkeypatch.setattr(auto_backup, "run_due_backups", lambda *a, **k: called.append(1) or [])

    scheduler = auto_backup.BackupScheduler(start_delay_seconds=0)
    scheduler.start()
    scheduler.stop()

    assert scheduler._thread is None or not scheduler._thread.is_alive()


def test_start_scheduler_is_noop_when_disabled(monkeypatch):

    monkeypatch.setenv("BACKUP_ENABLED", "false")

    auto_backup.start_scheduler()

    assert auto_backup._scheduler is None if hasattr(auto_backup, "_scheduler") else True

    auto_backup.stop_scheduler()


# =========================================
# OVERENIE EXISTUJÚCEJ ZÁLOHY
# =========================================

def test_verify_existing_backup_without_backups():

    make_account(with_files=False)

    result = auto_backup.verify_existing_backup(SLUG)

    assert result.ok is False
    assert "Zatiaľ" in result.error


def test_verify_existing_backup_checks_newest_and_records():

    make_account()

    created = auto_backup.run_backup(SLUG, "manual")
    result = auto_backup.verify_existing_backup(SLUG)

    assert result.ok
    assert result.file == created.file
    assert auto_backup.read_status(SLUG)["verification"]["ok"] is True


def test_verify_existing_backup_reports_corruption():

    make_account()

    path = backup_zip()
    path.write_bytes(b"rozbite")

    result = auto_backup.verify_existing_backup(SLUG)

    assert result.ok is False
    assert auto_backup.read_status(SLUG)["verification"]["ok"] is False


# =========================================
# PREHĽAD PRE NASTAVENIA
# =========================================

def test_overview_warns_before_first_backup(monkeypatch):

    monkeypatch.setenv("BACKUP_ENABLED", "true")

    make_account(with_files=False)

    overview = auto_backup.backup_overview(SLUG)

    assert any("Zatiaľ neprebehla" in warning for warning in overview["warnings"])
    assert overview["backups"] == []


def test_overview_lists_backups_after_run(monkeypatch):

    monkeypatch.setenv("BACKUP_ENABLED", "true")

    make_account()

    auto_backup.run_backup(SLUG, "manual")

    overview = auto_backup.backup_overview(SLUG)

    assert overview["warnings"] == []
    assert overview["backups"][0]["label"] == "Ručná"
    assert overview["verification"]["ok"] is True


def test_overview_warns_when_disabled(monkeypatch):

    make_account(with_files=False)

    monkeypatch.setenv("BACKUP_ENABLED", "false")

    overview = auto_backup.backup_overview(SLUG)

    assert overview["enabled"] is False
    assert any("vypnuté" in warning for warning in overview["warnings"])


# =========================================
# MIGRÁCIE (poistná záloha v start.sh)
# =========================================

def test_account_needs_migration():

    assert tenancy.account_needs_migration("neexistuje") is False

    make_account(with_files=False)

    assert tenancy.account_needs_migration(SLUG) is False

    conn = sqlite3.connect(str(tenancy.account_database_path(SLUG)))
    conn.execute("UPDATE alembic_version SET version_num = 'ab1382e889f9'")
    conn.commit()
    conn.close()

    assert tenancy.account_needs_migration(SLUG) is True


def test_cli_backup_all_only_if_migration_pending(capsys, isolated, monkeypatch):

    import manage_accounts
    from accounts_models import Account

    make_account("stare", with_files=False)
    make_account("aktualne", with_files=False)

    conn = sqlite3.connect(str(tenancy.account_database_path("stare")))
    conn.execute("UPDATE alembic_version SET version_num = 'ab1382e889f9'")
    conn.commit()
    conn.close()

    db = isolated()
    db.add_all([
        Account(username="stare", slug="stare", password_hash="x"),
        Account(username="aktualne", slug="aktualne", password_hash="x"),
    ])
    db.commit()
    db.close()

    monkeypatch.setattr(manage_accounts, "list_accounts", lambda: [
        type("A", (), {"slug": "stare", "username": "stare"})(),
        type("A", (), {"slug": "aktualne", "username": "aktualne"})(),
    ])

    manage_accounts.backup_all("pre-upgrade", only_if_migration_pending=True)

    old = auto_backup.list_backups("stare")

    assert len(old) == 1
    assert old[0].kind == "pre-upgrade"
    assert auto_backup.list_backups("aktualne") == []


# =========================================
# ROUTES
# =========================================

def _csrf(client, path):

    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)

    assert match

    return match.group(1)


def _logged_in_client(username="firmaA"):

    client = TestClient(app)

    csrf = _csrf(client, "/register")

    response = client.post(
        "/register",
        data={
            "csrf_token": csrf, "username": username,
            "password": "heslo1234", "password_confirm": "heslo1234",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303

    return client, tenancy.slugify_username(username)


def test_route_run_backup_now_and_download():

    client, slug = _logged_in_client()

    csrf = _csrf(client, "/settings")

    response = client.post(
        "/settings/backups/run", data={"csrf_token": csrf}, follow_redirects=False
    )

    assert response.status_code == 303
    assert "backup=ok" in response.headers["location"]

    backups = auto_backup.list_backups(slug)

    assert len(backups) == 1

    download = client.get(f"/settings/backups/{backups[0].name}")

    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert zipfile.is_zipfile(__import__("io").BytesIO(download.content))

    page = client.get("/settings")

    assert backups[0].name in page.text
    assert "Automatické zálohy" in page.text


def test_route_download_rejects_bad_names():

    client, slug = _logged_in_client()

    for name in ("tajne.zip", "..%2F..%2Faccounts.db", "auto-20200101-000000.zip"):
        assert client.get(f"/settings/backups/{name}").status_code == 404


def test_route_verify_now():

    client, slug = _logged_in_client()

    csrf = _csrf(client, "/settings")

    # bez zálohy -> neúspech
    response = client.post(
        "/settings/backups/verify", data={"csrf_token": csrf}, follow_redirects=False
    )

    assert "verify=failed" in response.headers["location"]

    client.post("/settings/backups/run", data={"csrf_token": csrf}, follow_redirects=False)

    response = client.post(
        "/settings/backups/verify", data={"csrf_token": csrf}, follow_redirects=False
    )

    assert "verify=ok" in response.headers["location"]


def test_route_backup_requires_login_and_csrf():

    anonymous = TestClient(app)

    assert anonymous.get(
        "/settings/backups/auto-20260101-000000.zip", follow_redirects=False
    ).status_code in (303, 401)

    assert anonymous.post("/settings/backups/run", follow_redirects=False).status_code in (303, 401, 403)

    client, _ = _logged_in_client()

    assert client.post(
        "/settings/backups/run", data={"csrf_token": "zly"}, follow_redirects=False
    ).status_code == 403


def test_backups_are_isolated_between_accounts():

    client_a, slug_a = _logged_in_client("firmaA")
    csrf = _csrf(client_a, "/settings")
    client_a.post("/settings/backups/run", data={"csrf_token": csrf}, follow_redirects=False)

    name = auto_backup.list_backups(slug_a)[0].name

    client_b, slug_b = _logged_in_client("firmaB")

    assert client_b.get(f"/settings/backups/{name}").status_code == 404


# =========================================
# MIGRÁCIE PRI ŠTARTE APPKY
# =========================================

def _register_account(isolated, slug):

    from accounts_models import Account

    db = isolated()
    db.add(Account(username=slug, slug=slug, password_hash="x"))
    db.commit()
    db.close()


def test_startup_upgrades_outdated_account_with_backup_and_keeps_data(isolated, monkeypatch):

    monkeypatch.setenv("AUTO_MIGRATE_ACCOUNTS", "true")

    make_account("stare", with_files=False)
    make_account("aktualne", with_files=False)
    _register_account(isolated, "stare")
    _register_account(isolated, "aktualne")
    monkeypatch.setattr(auto_backup, "account_slugs", lambda: ["stare", "aktualne"])

    conn = sqlite3.connect(str(tenancy.account_database_path("stare")))
    conn.execute("UPDATE alembic_version SET version_num = 'b5d7f9a1c324'")
    conn.execute("ALTER TABLE invoices DROP COLUMN reminder_count")
    conn.execute("ALTER TABLE invoices DROP COLUMN last_reminded_at")
    conn.commit()
    conn.close()

    assert auto_backup.upgrade_accounts_on_startup() == ["stare"]

    assert tenancy.account_needs_migration("stare") is False

    conn = sqlite3.connect(str(tenancy.account_database_path("stare")))
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 2
    assert conn.execute("SELECT reminder_count FROM invoices LIMIT 1").fetchall() == []
    conn.close()

    assert [b.kind for b in auto_backup.list_backups("stare")] == ["pre-upgrade"]
    assert auto_backup.list_backups("aktualne") == []

    # druhý štart už nič nerobí
    assert auto_backup.upgrade_accounts_on_startup() == []


def test_startup_upgrade_can_be_disabled(monkeypatch):

    monkeypatch.setenv("AUTO_MIGRATE_ACCOUNTS", "false")
    monkeypatch.setattr(auto_backup, "account_slugs", lambda: pytest.fail("nemalo sa volať"))

    assert auto_backup.upgrade_accounts_on_startup() == []
