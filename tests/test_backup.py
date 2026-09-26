"""
Testy pre zálohu a obnovu CELEJ appky (databáza + uploads/) ako ZIP.

KRITICKY DÔLEŽITÉ: backup_utils.py pracuje PRIAMO so súborom podľa
DATABASE_URL a s priečinkom UPLOADS_DIR (obchádza SQLAlchemy session),
takže tieto testy vždy monkeypatchujú backup_utils.DATABASE_URL aj
backup_utils.UPLOADS_DIR na dočasné umiestnenia - inak by hrozilo, že
testy prepíšu/zálohujú skutočné dáta appky.
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
from fastapi.testclient import TestClient

import backup_utils
from auth import require_login_page
from csrf import verify_csrf
from database import Base
from main import app


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    """
    Vytvorí dočasný SQLite súbor s plnou schémou appky + dočasný
    uploads/ priečinok, a nasmeruje na ne backup_utils - žiadny test v
    tomto súbore sa nikdy nedotkne skutočných dát appky.
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

    monkeypatch.setattr(backup_utils, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(backup_utils, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(backup_utils, "BACKUPS_DIR", tmp_path / "backups")

    # backup_utils.restore_from_upload() volá engine.dispose() na
    # SKUTOČNOM (produkčnom) engine importovanom z database.py - to je
    # neškodné aj v testoch (len zavrie pool nesúvisiaceho engine), takže
    # ho nemusíme mockovať.

    return {"db_path": db_path, "uploads_dir": uploads_dir}


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


def test_backup_zip_works_without_uploads_dir(temp_env, monkeypatch):
    """Ak uploads/ ešte vôbec neexistuje (čerstvá inštalácia appky bez
    nahraných súborov), záloha sa má stále vytvoriť bez chyby."""

    monkeypatch.setattr(
        backup_utils,
        "UPLOADS_DIR",
        temp_env["uploads_dir"] / "does-not-exist"
    )

    result = backup_utils.create_backup_bytes()

    with zipfile.ZipFile(io.BytesIO(result)) as zf:
        assert "database.db" in zf.namelist()


def test_create_backup_missing_db_file_raises_404(temp_env, monkeypatch):

    monkeypatch.setattr(
        backup_utils,
        "DATABASE_URL",
        f"sqlite:///{temp_env['db_path'].parent / 'does-not-exist.db'}"
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

    assert backup_utils.BACKUPS_DIR.exists()

    safety_backups = list(backup_utils.BACKUPS_DIR.glob("pred-obnovou-*.zip"))

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
