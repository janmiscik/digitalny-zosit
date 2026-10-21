"""Obnova po úplnej strate úložiska a odolnosť obnovy (krok „audit po audite“)."""

import io
import os
import re
import shutil
import sqlite3
import zipfile
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import backup_utils
import tenancy
from accounts_models import Account
from main import app

PASSWORD = "heslo1234"
PREVIOUS_REVISION = "d7f9b1c3e546"          # revízia pred customer_notes (head e8a0b2c4d657)


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")
    monkeypatch.setenv("BACKUP_MIN_FREE_MB", "1")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    accounts_db.AccountsBase.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def override_accounts_db():

        db = Session()

        try:
            yield db

        finally:
            db.close()

    app.dependency_overrides[accounts_db.get_accounts_db] = override_accounts_db
    tenancy._engine_cache.clear()

    yield Session

    app.dependency_overrides.clear()
    tenancy._engine_cache.clear()


def csrf(client, path):

    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text).group(1)


def register(username="firma"):

    client = TestClient(app)

    response = client.post(
        "/register",
        data={"csrf_token": csrf(client, "/register"), "username": username,
              "password": PASSWORD, "password_confirm": PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 303

    return client, tenancy.slugify_username(username)


def seed(slug):
    """Faktúra s dvoma sadzbami DPH, zákazník, zákazka s fotkou a logo."""

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))

    conn.execute("INSERT INTO customers (name, ico) VALUES ('Stavebniny s.r.o.', '12345016')")
    conn.execute("INSERT INTO company (id, name, logo_filename) VALUES (1, 'Moja Firma', 'logo.png')")
    conn.execute("INSERT INTO jobs (title, customer_id) VALUES ('Strecha', 1)")
    conn.execute(
        "INSERT INTO invoices (invoice_number, customer_id, status, issue_date, due_date, "
        "payment_method, reverse_charge, is_proforma, is_credit_note, reminder_count) "
        "VALUES ('2026001', 1, 'Odoslaná', '2026-03-15', '2026-03-29', 'Prevodom', 0, 0, 0, 0)"
    )
    conn.execute("INSERT INTO invoice_items (invoice_id, description, quantity, unit, unit_price, vat_rate) "
                 "VALUES (1, 'Práca', 2, 'h', 40, 23)")
    conn.execute("INSERT INTO invoice_items (invoice_id, description, quantity, unit, unit_price, vat_rate) "
                 "VALUES (1, 'Materiál', 1, 'ks', 19.99, 19)")
    conn.execute("INSERT INTO job_photos (job_id, filename, photo_type, uploaded_at) "
                 "VALUES (1, 'foto1.jpg', 'pred', '2026-01-01')")
    conn.commit()
    conn.close()

    uploads = tenancy.account_uploads_dir(slug)
    (uploads / "job_photos").mkdir(parents=True, exist_ok=True)
    (uploads / "logo.png").write_bytes(b"logo-bytes")
    (uploads / "job_photos" / "foto1.jpg").write_bytes(b"photo-bytes")


def db_state(slug):

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))

    state = {
        "customers": conn.execute("SELECT name, ico FROM customers").fetchall(),
        "invoices": conn.execute("SELECT invoice_number, status FROM invoices").fetchall(),
        "items": conn.execute("SELECT description, quantity, unit_price, vat_rate FROM invoice_items ORDER BY id").fetchall(),
        "photos": conn.execute("SELECT filename FROM job_photos").fetchall(),
        "version": conn.execute("SELECT version_num FROM alembic_version").fetchone()[0],
        "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }

    conn.close()

    return state


def restore(client, zip_bytes):

    return client.post(
        "/settings/restore",
        data={"csrf_token": csrf(client, "/settings")},
        files={"backup_file": ("zaloha.zip", zip_bytes, "application/zip")},
        follow_redirects=False,
    )


def head_revision():

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(tenancy.PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(tenancy.PROJECT_ROOT / "alembic"))

    return ScriptDirectory.from_config(cfg).get_current_head()


# ---------- úplná strata úložiska ----------

def test_full_storage_loss_recovery_from_downloaded_backup(storage, tmp_path):

    client, slug = register()
    seed(slug)
    before = db_state(slug)

    response = client.get("/settings/backup")

    assert response.status_code == 200

    downloaded = response.content           # stiahnutá a uložená mimo servera

    # --- strata celého Volume: zmizne accounts.db aj priečinky kont (aj zálohy) ---
    shutil.rmtree(tmp_path / "accounts")
    tenancy._engine_cache.clear()

    db = storage()
    db.query(Account).delete()
    db.commit()
    db.close()

    # --- nové prázdne úložisko: používateľ sa zaregistruje znova a nahrá zálohu ---
    new_client, new_slug = register()

    assert new_slug == slug
    assert db_state(new_slug)["customers"] == []

    response = restore(new_client, downloaded)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?restored=1"

    after = db_state(new_slug)

    assert after == before
    assert after["integrity"] == "ok"
    assert (tenancy.account_uploads_dir(new_slug) / "logo.png").read_bytes() == b"logo-bytes"
    assert (tenancy.account_uploads_dir(new_slug) / "job_photos" / "foto1.jpg").read_bytes() == b"photo-bytes"

    # obnovené dáta sa dajú reálne otvoriť v appke vrátane PDF faktúry
    assert new_client.get("/invoices/1").status_code == 200
    pdf = new_client.get("/invoices/1/pdf")

    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_restore_does_not_leak_into_other_account(storage):

    client_a, slug_a = register("firmaA")
    seed(slug_a)
    backup = client_a.get("/settings/backup").content

    client_b, slug_b = register("firmaB")

    restore(client_b, backup)

    assert db_state(slug_b)["customers"] == db_state(slug_a)["customers"]      # B dostalo, čo nahralo
    assert tenancy.account_database_path(slug_a) != tenancy.account_database_path(slug_b)


# ---------- poškodená záloha neničí živé dáta ----------

@pytest.mark.parametrize("make_bad", [
    lambda good: good[: len(good) // 2],                          # useknutý súbor
    lambda good: b"nie-je-zip",
    lambda good: b"",
])
def test_damaged_backup_is_rejected_and_live_data_untouched(storage, make_bad):

    client, slug = register()
    seed(slug)
    good = client.get("/settings/backup").content
    before = db_state(slug)

    response = restore(client, make_bad(good))

    assert response.status_code in (422, 400)
    assert db_state(slug) == before
    assert (tenancy.account_uploads_dir(slug) / "logo.png").exists()


# ---------- záloha zo staršej verzie appky ----------

def downgrade_backup(zip_bytes):
    """Z hotovej zálohy spraví zálohu „zo staršej verzie“: bez customer_notes."""

    source = zipfile.ZipFile(io.BytesIO(zip_bytes))
    out = io.BytesIO()

    with zipfile.ZipFile(out, "w") as target:

        for name in source.namelist():

            data = source.read(name)

            if name == "database.db" or name.endswith(".db"):

                tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "old_schema_backup.db"
                tmp.write_bytes(data)

                conn = sqlite3.connect(str(tmp))
                conn.execute("DROP TABLE customer_notes")
                conn.execute("UPDATE alembic_version SET version_num = ?", (PREVIOUS_REVISION,))
                conn.commit()
                conn.close()

                data = tmp.read_bytes()
                tmp.unlink()

            target.writestr(name, data)

    return out.getvalue()


def test_restore_of_older_schema_backup_is_migrated_to_head(storage):

    client, slug = register()
    seed(slug)

    old_backup = downgrade_backup(client.get("/settings/backup").content)

    assert restore(client, old_backup).status_code == 303

    state = db_state(slug)

    assert state["version"] == head_revision()
    assert state["customers"] == [("Stavebniny s.r.o.", "12345016")]

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()

    assert "customer_notes" in tables
    assert client.get("/customers/1").status_code == 200           # stránka používa novú tabuľku


def test_failed_migration_leaves_live_data_untouched(storage, monkeypatch):

    client, slug = register()
    seed(slug)
    good = client.get("/settings/backup").content

    # po zálohe sa dáta zmenia - po neúspešnej obnove musia ostať TIETO
    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))
    conn.execute("INSERT INTO customers (name) VALUES ('Nový po zálohe')")
    conn.commit()
    conn.close()

    before = db_state(slug)

    def boom(_path):
        raise RuntimeError("migrácia spadla")

    monkeypatch.setattr(tenancy, "upgrade_database_file", boom)

    response = restore(client, good)

    assert response.status_code == 422
    assert "Pôvodné dáta zostali nedotknuté" in response.text
    assert db_state(slug) == before
    assert (tenancy.account_uploads_dir(slug) / "logo.png").read_bytes() == b"logo-bytes"


def test_damaged_photo_in_zip_is_rejected_before_any_change(storage):

    client, slug = register()
    seed(slug)
    good = client.get("/settings/backup").content

    # Poškodíme obsah fotky v archíve (CRC prestane sedieť), štruktúra ostane platná.
    with zipfile.ZipFile(io.BytesIO(good)) as zf:

        info = zf.getinfo("uploads/job_photos/foto1.jpg")

    data_start = info.header_offset + 30 + len(info.filename.encode()) + 0
    damaged = bytearray(good)
    damaged[data_start] ^= 0xFF
    damaged = bytes(damaged)

    before = db_state(slug)

    response = restore(client, damaged)

    assert response.status_code == 422
    assert "poškodená" in response.text
    assert db_state(slug) == before
    assert (tenancy.account_uploads_dir(slug) / "job_photos" / "foto1.jpg").read_bytes() == b"photo-bytes"


def test_failure_during_swap_restores_database_and_files(storage, monkeypatch):

    import backup_utils

    client, slug = register()
    seed(slug)
    good = client.get("/settings/backup").content

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))
    conn.execute("INSERT INTO customers (name) VALUES ('Nový po zálohe')")
    conn.commit()
    conn.close()
    (tenancy.account_uploads_dir(slug) / "novy-subor.txt").write_bytes(b"novy")

    before = db_state(slug)

    real_move = backup_utils.shutil.move
    calls = {"n": 0}

    def flaky_move(src, dst, *args, **kwargs):

        calls["n"] += 1

        if calls["n"] == 2:                      # po presune starých uploads/, pri nasadení nových
            raise OSError("disk plný")

        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(backup_utils.shutil, "move", flaky_move)

    response = restore(client, good)

    assert response.status_code == 500
    assert "Pôvodné dáta boli vrátené" in response.text
    assert db_state(slug) == before
    assert (tenancy.account_uploads_dir(slug) / "novy-subor.txt").read_bytes() == b"novy"
    assert (tenancy.account_uploads_dir(slug) / "logo.png").read_bytes() == b"logo-bytes"


def test_no_staging_leftovers_after_restore(storage):

    client, slug = register()
    seed(slug)

    restore(client, client.get("/settings/backup").content)

    assert not list(tenancy.account_database_path(slug).parent.glob("restore-*"))


def test_restore_creates_safety_backup_first(storage):

    client, slug = register()
    seed(slug)
    good = client.get("/settings/backup").content

    restore(client, good)

    assert list(tenancy.account_backups_dir(slug).glob("*.zip")), "poistná záloha pred obnovou chýba"


def test_corrupt_logo_does_not_break_pdf(storage):

    client, slug = register()
    seed(slug)                                             # logo.png obsahuje nezmysly

    for path in ("/invoices/1/pdf",):

        response = client.get(path)

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")
