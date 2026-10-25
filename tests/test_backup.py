"""
Testy pre zálohu a obnovu CELEJ appky (databáza + uploads/) ako ZIP.

KRITICKY DÔLEŽITÉ: backup_utils.py pracuje PRIAMO so súborom databázy
a s priečinkom uploads AKTUÁLNEHO konta (tenancy.py, obchádza
SQLAlchemy session), takže tieto testy vždy cez patch_tenant_paths
(tests/conftest.py) nasmerujú tenancy na dočasné umiestnenia - inak by
hrozilo, že testy prepíšu/zálohujú skutočné dáta appky.
"""

import io
import os
import sqlite3
import sys
import zipfile
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "")


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)


import pytest

import tenancy
from fastapi.testclient import TestClient

import backup_utils
from auth import require_login_page
from csrf import verify_csrf
from database import Base
from main import app


@pytest.fixture
def temp_env(tmp_path, monkeypatch, patch_tenant_paths):
    """
    Vytvorí dočasný SQLite súbor s plnou schémou appky + dočasný
    uploads/ priečinok, a nasmeruje naň backup_utils (cez tenancy.py -
    patch_tenant_paths, viď tests/conftest.py) - žiadny test v tomto
    súbore sa nikdy nedotkne skutočných dát appky.
    """

    db_path = tmp_path / "live.db"

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    conn.execute("INSERT INTO alembic_version VALUES ('test-head')")
    conn.commit()
    conn.close()

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    backups_dir = tmp_path / "backups"

    patch_tenant_paths(
        db_path=db_path,
        uploads=uploads_dir,
        backups=backups_dir
    )

    # tenancy.discard_account_engine() (volá ho backup_utils pri obnove
    # namiesto pôvodného engine.dispose()) pracuje nad PRÁZDNOU cache -
    # žiadny skutočný engine pre "test-tenant" slug v nej nie je, takže
    # je to v testoch neškodné.

    return {"db_path": db_path, "uploads_dir": uploads_dir, "backups_dir": backups_dir}


def override_login():
    return "testuser"


@pytest.fixture(autouse=True)
def override_auth():

    app.dependency_overrides[require_login_page] = override_login

    # Tieto testy neoverujú CSRF ochranu (na to je tests/test_csrf.py) -
    # tu ju obídeme, nech sa sústredia len na vlastnú business logiku.
    app.dependency_overrides[verify_csrf] = lambda: None

    yield

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def skip_schema_migration(monkeypatch):
    """Tieto testy používajú syntetickú DB s vymyslenou revíziou ('test-head') -
    reálna migrácia po obnove sa testuje v test_disaster_recovery.py."""

    monkeypatch.setattr(tenancy, "upgrade_database_file", lambda path: None)


client = TestClient(app)


# =========================================
# create_backup_bytes - obsahuje DB aj uploads/
# =========================================

def test_backup_zip_contains_database(temp_env):

    result = backup_utils.create_backup_bytes()

    with zipfile.ZipFile(io.BytesIO(result)) as zf:

        assert "database.db" in zf.namelist()
        assert zf.read("database.db")[:16] == b"SQLite format 3\x00"


def test_backup_zip_contains_uploaded_files(temp_env):

    uploads_dir = temp_env["uploads_dir"]

    (uploads_dir / "logo.png").write_bytes(b"fake-logo-bytes")

    job_photos_dir = uploads_dir / "job_photos"
    job_photos_dir.mkdir()
    (job_photos_dir / "job1-abc123.jpg").write_bytes(b"fake-photo-bytes")

    result = backup_utils.create_backup_bytes()

    with zipfile.ZipFile(io.BytesIO(result)) as zf:

        names = zf.namelist()

        assert "uploads/logo.png" in names
        assert "uploads/job_photos/job1-abc123.jpg" in names
        assert zf.read("uploads/logo.png") == b"fake-logo-bytes"


def test_backup_zip_works_without_uploads_dir(temp_env, monkeypatch, patch_tenant_paths):
    """Ak uploads/ ešte vôbec neexistuje (čerstvá inštalácia appky bez
    nahraných súborov), záloha sa má stále vytvoriť bez chyby."""

    patch_tenant_paths(
        db_path=temp_env["db_path"],
        uploads=temp_env["uploads_dir"] / "does-not-exist",
        backups=temp_env["backups_dir"]
    )

    result = backup_utils.create_backup_bytes()

    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        assert "database.db" in zf.namelist()


def test_create_backup_missing_db_file_raises_404(temp_env, monkeypatch, patch_tenant_paths):

    patch_tenant_paths(
        db_path=temp_env["db_path"].parent / "does-not-exist.db",
        uploads=temp_env["uploads_dir"],
        backups=temp_env["backups_dir"]
    )

    with pytest.raises(Exception) as exc_info:
        backup_utils.create_backup_bytes()

    assert "404" in str(exc_info.value) or "neexistuje" in str(exc_info.value)


# =========================================
# _validate_backup_file
# =========================================

def test_validate_backup_rejects_empty_file():

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(b"")


def test_validate_backup_rejects_non_zip_file():

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(b"not a real zip file at all")


def test_validate_backup_rejects_zip_without_database(tmp_path):

    fake_zip_path = tmp_path / "fake.zip"

    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("uploads/logo.png", b"just a photo, no db")

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(fake_zip_path.read_bytes())


def test_validate_backup_rejects_zip_with_invalid_database(tmp_path):

    fake_zip_path = tmp_path / "fake2.zip"

    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.writestr("database.db", b"not actually sqlite")

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(fake_zip_path.read_bytes())


def test_validate_backup_rejects_db_missing_required_tables(tmp_path):

    unrelated_db = tmp_path / "unrelated.db"

    conn = sqlite3.connect(str(unrelated_db))
    conn.execute("CREATE TABLE something_else (id INTEGER)")
    conn.commit()
    conn.close()

    fake_zip_path = tmp_path / "fake3.zip"

    with zipfile.ZipFile(fake_zip_path, "w") as zf:
        zf.write(unrelated_db, "database.db")

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(fake_zip_path.read_bytes())


def test_validate_backup_accepts_valid_backup(temp_env):

    valid_bytes = backup_utils.create_backup_bytes()

    # nesmie vyhodiť výnimku
    backup_utils._validate_backup_file(valid_bytes)


# =========================================
# Ochrana proti "zip bomb"
# =========================================

def test_validate_backup_rejects_too_many_entries(tmp_path, monkeypatch):

    monkeypatch.setattr(backup_utils, "MAX_BACKUP_ENTRY_COUNT", 5)

    fake_zip_path = tmp_path / "too_many_entries.zip"

    with zipfile.ZipFile(fake_zip_path, "w") as zf:

        for i in range(10):
            zf.writestr(f"uploads/photo{i}.png", b"x")

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(fake_zip_path.read_bytes())


def test_validate_backup_rejects_oversized_single_entry(tmp_path, monkeypatch):
    """
    Kontrola sa opiera o nekomprimovanú veľkosť z metadát ZIPu (rýchlo
    dostupná bez rozbaľovania) - nižšie nastavíme umelo nízky limit,
    nech test nemusí reálne generovať stovky MB dát.
    """

    monkeypatch.setattr(
        backup_utils,
        "MAX_BACKUP_UNCOMPRESSED_ENTRY_BYTES",
        1000
    )

    fake_zip_path = tmp_path / "oversized_entry.zip"

    # Vysoko komprimovateľné dáta (samé nuly) - malý ZIP súbor,
    # ale nad nastaveným limitom po rozbalení. Presne princíp zip bomb.
    with zipfile.ZipFile(fake_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("uploads/huge.png", b"\x00" * 5000)

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(fake_zip_path.read_bytes())


def test_validate_backup_rejects_oversized_total_size(tmp_path, monkeypatch):
    """Žiadny jednotlivý súbor limit neprekročí, ale súčet áno."""

    monkeypatch.setattr(
        backup_utils,
        "MAX_BACKUP_UNCOMPRESSED_ENTRY_BYTES",
        10_000
    )
    monkeypatch.setattr(
        backup_utils,
        "MAX_BACKUP_UNCOMPRESSED_TOTAL_BYTES",
        15_000
    )

    fake_zip_path = tmp_path / "oversized_total.zip"

    with zipfile.ZipFile(fake_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:

        for i in range(3):
            # 3x 8000 bajtov = 24 000 > limit 15 000 spolu, hoci každý
            # jednotlivo (8000) je pod limitom jedného súboru (10 000).
            zf.writestr(f"uploads/photo{i}.png", b"\x00" * 8000)

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(fake_zip_path.read_bytes())


def test_validate_backup_within_limits_still_accepted(temp_env, monkeypatch):
    """Poistka, že novo pridaná kontrola nezačala zamietať aj bežné,
    v rámci limitov ležiace zálohy."""

    monkeypatch.setattr(backup_utils, "MAX_BACKUP_ENTRY_COUNT", 5)
    monkeypatch.setattr(
        backup_utils,
        "MAX_BACKUP_UNCOMPRESSED_ENTRY_BYTES",
        200_000
    )
    monkeypatch.setattr(
        backup_utils,
        "MAX_BACKUP_UNCOMPRESSED_TOTAL_BYTES",
        500_000
    )

    valid_bytes = backup_utils.create_backup_bytes()

    # nesmie vyhodiť výnimku
    backup_utils._validate_backup_file(valid_bytes)


# =========================================
# restore_from_upload
# =========================================

def test_restore_overwrites_live_database(temp_env):

    other_db_path = temp_env["db_path"].parent / "other.db"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Customer

    other_engine = create_engine(f"sqlite:///{other_db_path}")
    Base.metadata.create_all(bind=other_engine)

    conn = sqlite3.connect(str(other_db_path))
    conn.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    conn.execute("INSERT INTO alembic_version VALUES ('test-head')")
    conn.commit()
    conn.close()

    OtherSession = sessionmaker(bind=other_engine)
    other_db = OtherSession()
    other_db.add(Customer(name="Zákazník Z Obnovenej Zálohy"))
    other_db.commit()
    other_db.close()

    zip_bytes_io = io.BytesIO()
    with zipfile.ZipFile(zip_bytes_io, "w") as zf:
        zf.write(other_db_path, "database.db")

    backup_utils.restore_from_upload(zip_bytes_io.getvalue())

    conn = sqlite3.connect(str(temp_env["db_path"]))
    cursor = conn.execute("SELECT name FROM customers")
    names = [row[0] for row in cursor.fetchall()]
    conn.close()

    assert "Zákazník Z Obnovenej Zálohy" in names


def test_restore_replaces_uploads_directory(temp_env):

    uploads_dir = temp_env["uploads_dir"]

    # súbor, ktorý existuje PRED obnovou a NIE JE v zálohe -> musí zmiznúť
    (uploads_dir / "stary-subor-mimo-zalohy.png").write_bytes(b"old")

    valid_backup = backup_utils.create_backup_bytes()

    # do zálohy (v pamäti) pridáme nový súbor a znova zabalíme
    with zipfile.ZipFile(io.BytesIO(valid_backup)) as zf:
        db_bytes = zf.read("database.db")

    new_zip_io = io.BytesIO()
    with zipfile.ZipFile(new_zip_io, "w") as zf:
        zf.writestr("database.db", db_bytes)
        zf.writestr("uploads/novy-subor.png", b"new-content")

    backup_utils.restore_from_upload(new_zip_io.getvalue())

    assert not (uploads_dir / "stary-subor-mimo-zalohy.png").exists()
    assert (uploads_dir / "novy-subor.png").exists()
    assert (uploads_dir / "novy-subor.png").read_bytes() == b"new-content"


def test_restore_creates_safety_backup_before_overwriting(temp_env):

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Customer

    engine = create_engine(f"sqlite:///{temp_env['db_path']}")
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(Customer(name="Pôvodný zákazník pred obnovou"))
    db.commit()
    db.close()

    valid_backup = backup_utils.create_backup_bytes()

    backup_utils.restore_from_upload(valid_backup)

    assert backup_utils.backups_dir().exists()

    safety_backups = list(backup_utils.backups_dir().glob("pred-obnovou-*.zip"))

    assert len(safety_backups) == 1

    with zipfile.ZipFile(safety_backups[0]) as zf:

        db_bytes = zf.read("database.db")

    tmp_check = temp_env["db_path"].parent / "check.db"
    tmp_check.write_bytes(db_bytes)

    conn = sqlite3.connect(str(tmp_check))
    cursor = conn.execute("SELECT name FROM customers")
    names = [row[0] for row in cursor.fetchall()]
    conn.close()

    assert "Pôvodný zákazník pred obnovou" in names


def test_restore_writes_audit_log_entry_into_restored_db(temp_env):
    """
    Log o obnove sa musí zapísať AŽ do (novej, práve obnovenej) DB -
    logovať by malo zmysel len tam, keďže samotná obnova prepíše celý
    predchádzajúci obsah audit_log tabuľky spolu so zvyškom dát.
    """

    valid_backup = backup_utils.create_backup_bytes()

    safety_backup_path = backup_utils.restore_from_upload(valid_backup)

    conn = sqlite3.connect(str(temp_env["db_path"]))

    cursor = conn.execute(
        "SELECT action, entity_type, detail FROM audit_log "
        "ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None

    action, entity_type, detail = row

    assert action == "backup.restore"
    assert entity_type == "backup"
    assert safety_backup_path.name in detail


def test_restore_of_backup_without_audit_log_table_still_succeeds(temp_env):
    """
    Staršia záloha (spravená appkou pred zavedením audit logu) nemá
    audit_log tabuľku vôbec - zápis logu po obnove sa má ticho
    preskočiť, nie zhodiť inak úspešnú obnovu.
    """

    from sqlalchemy import create_engine

    old_db_path = temp_env["db_path"].parent / "old_without_audit_log.db"

    engine = create_engine(f"sqlite:///{old_db_path}")
    # Zámerne BEZ audit_log - simuluje schému spred tejto funkcie.
    tables_without_audit_log = [
        table for table in Base.metadata.sorted_tables
        if table.name != "audit_log"
    ]
    Base.metadata.create_all(bind=engine, tables=tables_without_audit_log)

    conn = sqlite3.connect(str(old_db_path))
    conn.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    conn.execute("INSERT INTO alembic_version VALUES ('test-head')")
    conn.commit()
    conn.close()

    zip_bytes_io = io.BytesIO()
    with zipfile.ZipFile(zip_bytes_io, "w") as zf:
        zf.write(old_db_path, "database.db")

    # nesmie vyhodiť výnimku, aj keď cieľová DB nemá audit_log tabuľku
    backup_utils.restore_from_upload(zip_bytes_io.getvalue())

    conn = sqlite3.connect(str(temp_env["db_path"]))
    tables = {
        row[0] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()

    assert "audit_log" not in tables


def test_restore_rejects_invalid_file(temp_env):

    with pytest.raises(Exception):
        backup_utils.restore_from_upload(b"totally invalid content")


def test_restore_blocks_path_traversal_in_uploads(temp_env):
    """Zlomyseľná záloha so súborom mimo uploads/ (napr. cez ../) sa
    nesmie zapísať mimo UPLOADS_DIR."""

    valid_backup = backup_utils.create_backup_bytes()

    with zipfile.ZipFile(io.BytesIO(valid_backup)) as zf:
        db_bytes = zf.read("database.db")

    malicious_zip_io = io.BytesIO()
    with zipfile.ZipFile(malicious_zip_io, "w") as zf:
        zf.writestr("database.db", db_bytes)
        zf.writestr("uploads/../../evil.txt", b"pwned")

    backup_utils.restore_from_upload(malicious_zip_io.getvalue())

    escaped_file = temp_env["uploads_dir"].parent.parent / "evil.txt"

    assert not escaped_file.exists()


# =========================================
# HTTP ENDPOINTS
# =========================================

def test_download_backup_endpoint(temp_env):

    response = client.get("/settings/backup")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.zip"')

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert "database.db" in zf.namelist()


def test_download_backup_requires_login(temp_env):

    del app.dependency_overrides[require_login_page]

    try:

        response = client.get("/settings/backup", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = override_login


def test_restore_endpoint_success(temp_env):

    valid_backup = backup_utils.create_backup_bytes()

    response = client.post(
        "/settings/restore",
        files={"backup_file": ("zaloha.zip", valid_backup, "application/zip")},
        follow_redirects=False
    )

    assert response.status_code == 303
    assert "restored=1" in response.headers["location"]


def test_restore_endpoint_rejects_invalid_file(temp_env):

    response = client.post(
        "/settings/restore",
        files={"backup_file": ("fake.zip", b"not a zip", "application/zip")}
    )

    assert response.status_code == 422


def test_restore_endpoint_requires_file(temp_env):

    response = client.post("/settings/restore", data={})

    assert response.status_code == 422


def test_restore_endpoint_requires_login(temp_env):

    del app.dependency_overrides[require_login_page]

    try:

        response = client.post(
            "/settings/restore",
            files={"backup_file": ("zaloha.zip", b"x", "application/zip")},
            follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = override_login


def test_save_automatic_backup_does_not_overwrite_on_same_second_collision(
    temp_env, monkeypatch
):
    """
    Dve volania v tej istej sekunde (napr. dvojklik na Obnoviť) sa
    predtým pokúsili zapísať do rovnakého názvu súboru - druhé volanie
    by tak ticho prepísalo prvú bezpečnostnú zálohu.
    """

    from datetime import datetime as real_datetime

    fixed_now = real_datetime(2026, 9, 28, 20, 4, 12)

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(backup_utils, "datetime", FrozenDatetime)

    first_path = backup_utils.save_automatic_backup()
    second_path = backup_utils.save_automatic_backup()

    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()
    assert first_path.stat().st_size > 0
    assert second_path.stat().st_size > 0

    safety_backups = sorted(backup_utils.backups_dir().glob("pred-obnovou-*.zip"))

    assert len(safety_backups) == 2


# ---------- obnova zo šifrovanej zálohy (.zip.enc) ----------

ENC_PASSWORD = "toto-je-dlhe-nahodne-heslo-123"


def encrypted_backup_bytes(password=ENC_PASSWORD):

    import tempfile

    import backup_crypto

    with tempfile.TemporaryDirectory() as directory:

        plain = Path(directory) / "z.zip"
        plain.write_bytes(backup_utils.create_backup_bytes())
        encrypted = Path(directory) / "z.zip.enc"
        backup_crypto.encrypt_file(plain, encrypted, password)

        return encrypted.read_bytes()


def test_restore_accepts_encrypted_backup_with_form_password(temp_env, monkeypatch):

    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

    response = client.post(
        "/settings/restore",
        data={"backup_password": ENC_PASSWORD},
        files={"backup_file": ("zaloha.zip.enc", encrypted_backup_bytes(), "application/octet-stream")},
        follow_redirects=False
    )

    assert response.status_code == 303
    assert "restored=1" in response.headers["location"]


def test_restore_uses_server_password_when_form_field_is_empty(temp_env, monkeypatch):

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", ENC_PASSWORD)

    response = client.post(
        "/settings/restore",
        data={"backup_password": ""},
        files={"backup_file": ("zaloha.zip.enc", encrypted_backup_bytes(), "application/octet-stream")},
        follow_redirects=False
    )

    assert response.status_code == 303


def test_restore_detects_encryption_by_content_not_file_name(temp_env, monkeypatch):

    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

    response = client.post(
        "/settings/restore",
        data={"backup_password": ENC_PASSWORD},
        files={"backup_file": ("premenovane.zip", encrypted_backup_bytes(), "application/zip")},
        follow_redirects=False
    )

    assert response.status_code == 303


def test_restore_encrypted_without_any_password_is_refused(temp_env, monkeypatch):

    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

    response = client.post(
        "/settings/restore",
        files={"backup_file": ("zaloha.zip.enc", encrypted_backup_bytes(), "application/octet-stream")}
    )

    assert response.status_code == 422
    assert "heslo" in response.text.lower()


def test_restore_with_wrong_password_changes_nothing_and_does_not_echo_it(temp_env, monkeypatch):

    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    wrong = "uplne-ine-heslo-uplne-ine-1"

    before = backup_utils.create_backup_bytes()

    response = client.post(
        "/settings/restore",
        data={"backup_password": wrong},
        files={"backup_file": ("zaloha.zip.enc", encrypted_backup_bytes(), "application/octet-stream")}
    )

    assert response.status_code == 422
    assert wrong not in response.text and ENC_PASSWORD not in response.text

    # databáza ostala nedotknutá: ďalšia záloha je stále platná a obnoviteľná
    assert zipfile.is_zipfile(io.BytesIO(before))


def test_restore_plain_zip_ignores_a_password_field(temp_env):

    response = client.post(
        "/settings/restore",
        data={"backup_password": "hocijake"},
        files={"backup_file": ("zaloha.zip", backup_utils.create_backup_bytes(), "application/zip")},
        follow_redirects=False
    )

    assert response.status_code == 303


def test_restore_corrupted_encrypted_file_is_refused(temp_env, monkeypatch):

    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

    blob = bytearray(encrypted_backup_bytes())
    blob[len(blob) // 2] ^= 1

    response = client.post(
        "/settings/restore",
        data={"backup_password": ENC_PASSWORD},
        files={"backup_file": ("zaloha.zip.enc", bytes(blob), "application/octet-stream")}
    )

    assert response.status_code == 422


# ---------- obnova: pôvodné dáta ostanú pri KAŽDOM zlyhaní, úspech ich naozaj nahradí ----------

import hashlib


def live_state(env):
    """Odtlačok živých dát: všetky riadky DB + všetky súbory uploads."""

    digest = hashlib.sha256()

    connection = sqlite3.connect(str(env["db_path"]))

    try:
        for line in connection.iterdump():
            digest.update(line.encode("utf-8"))

    finally:
        connection.close()

    for path in sorted(p for p in env["uploads_dir"].rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(env["uploads_dir"])).encode())
        digest.update(path.read_bytes())

    return digest.hexdigest()


def set_live_data(env, customer_name, upload_text):

    connection = sqlite3.connect(str(env["db_path"]))
    connection.execute("DELETE FROM customers")
    connection.execute("INSERT INTO customers (name) VALUES (?)", (customer_name,))
    connection.commit()
    connection.close()

    for path in env["uploads_dir"].glob("*"):
        path.unlink()

    (env["uploads_dir"] / "logo.png").write_text(upload_text)


def customer_names(env):

    connection = sqlite3.connect(str(env["db_path"]))

    try:
        return [row[0] for row in connection.execute("SELECT name FROM customers")]

    finally:
        connection.close()


def broken_database_zip():

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("database.db", b"toto nie je sqlite databaza" * 50)

    return buffer.getvalue()


def truncated(data):

    return data[: len(data) // 2]


def traversal_zip():

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("database.db", b"x")
        zf.writestr("../../vonku.txt", b"utek")

    return buffer.getvalue()


@pytest.mark.parametrize("case", [
    "wrong_password", "corrupted_encrypted", "truncated_encrypted", "garbage", "truncated_zip",
    "broken_database", "traversal", "empty",
])
def test_failed_restore_keeps_live_data_untouched(temp_env, monkeypatch, case):

    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

    set_live_data(temp_env, "PRODUKCNY-ZAKAZNIK", "produkcne-logo")
    good_backup = backup_utils.create_backup_bytes()

    payloads = {
        "wrong_password": (encrypted_backup_bytes(), "zlé-heslo-ktoré-nie-je-to-pravé-1"),
        "corrupted_encrypted": (bytes(bytearray(encrypted_backup_bytes())[:200]
                                      + bytes([bytearray(encrypted_backup_bytes())[200] ^ 1])
                                      + bytearray(encrypted_backup_bytes())[201:]), ENC_PASSWORD),
        "truncated_encrypted": (truncated(encrypted_backup_bytes()), ENC_PASSWORD),
        "garbage": (b"nahodny odpad" * 100, ""),
        "truncated_zip": (truncated(good_backup), ""),
        "broken_database": (broken_database_zip(), ""),
        "traversal": (traversal_zip(), ""),
        "empty": (b"", ""),
    }

    data, password = payloads[case]

    # zálohu s inými dátami vyrobíme PRED poškodením - ostáva len živý stav na kontrolu
    before = live_state(temp_env)
    backups_before = sorted(p.name for p in temp_env["backups_dir"].glob("*")) if temp_env["backups_dir"].exists() else []

    response = client.post(
        "/settings/restore",
        data={"backup_password": password},
        files={"backup_file": ("zaloha.zip.enc", data, "application/octet-stream")},
        follow_redirects=False,
    )

    assert response.status_code in (400, 422), (case, response.status_code, response.text[:200])
    assert password not in response.text or not password

    assert live_state(temp_env) == before, "živé dáta sa zmenili po neúspešnej obnove"
    assert customer_names(temp_env) == ["PRODUKCNY-ZAKAZNIK"]
    assert not (temp_env["uploads_dir"].parent / "vonku.txt").exists()
    assert not (temp_env["uploads_dir"].parent.parent / "vonku.txt").exists()

    backups_after = sorted(p.name for p in temp_env["backups_dir"].glob("*")) if temp_env["backups_dir"].exists() else []
    assert backups_after == backups_before, "neúspešná obnova nesmie zanechať súbory"


def test_successful_encrypted_restore_replaces_data_and_keeps_safety_copy(temp_env, monkeypatch):

    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)

    set_live_data(temp_env, "STARY-STAV", "logo-zo-zalohy")
    encrypted = encrypted_backup_bytes()

    set_live_data(temp_env, "NOVSI-STAV-PRED-OBNOVOU", "ine-logo")

    response = client.post(
        "/settings/restore",
        data={"backup_password": ENC_PASSWORD},
        files={"backup_file": ("zaloha.zip.enc", encrypted, "application/octet-stream")},
        follow_redirects=False,
    )

    assert response.status_code == 303

    assert customer_names(temp_env) == ["STARY-STAV"]
    assert (temp_env["uploads_dir"] / "logo.png").read_text() == "logo-zo-zalohy"

    # poistná kópia stavu tesne pred obnovou existuje a obsahuje novší stav
    safety = sorted(temp_env["backups_dir"].glob("pred-obnovou-*.zip"))

    assert safety

    with zipfile.ZipFile(safety[-1]) as zf:
        extracted = temp_env["uploads_dir"].parent / "safety.db"
        extracted.write_bytes(zf.read("database.db"))

    connection = sqlite3.connect(str(extracted))

    try:
        assert [row[0] for row in connection.execute("SELECT name FROM customers")] == ["NOVSI-STAV-PRED-OBNOVOU"]

    finally:
        connection.close()
