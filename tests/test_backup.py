"""
Testy pre zálohu a obnovu databázy.

KRITICKY DÔLEŽITÉ: backup_utils.py pracuje PRIAMO so súborom podľa
DATABASE_URL (obchádza SQLAlchemy session), takže tieto testy musia
vždy monkeypatchnúť backup_utils.DATABASE_URL na dočasný súbor - inak
by hrozilo, že testy prepíšu/zálohujú skutočný digitalny-zosit.db.
"""

import os
import sqlite3
import sys
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
from database import Base
from main import app


@pytest.fixture
def temp_db_path(tmp_path, monkeypatch):
    """
    Vytvorí dočasný SQLite súbor s plnou schémou appky a nasmeruje naň
    backup_utils.DATABASE_URL - žiadny test v tomto súbore sa nikdy
    nedotkne skutočného digitalny-zosit.db.
    """

    db_path = tmp_path / "live.db"

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)

    # alembic_version tabuľka nie je súčasť Base.metadata (spravuje ju
    # alembic) - vytvoríme ju ručne, nech súbor spĺňa REQUIRED_TABLES.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    conn.execute("INSERT INTO alembic_version VALUES ('test-head')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(backup_utils, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(backup_utils, "BACKUPS_DIR", tmp_path / "backups")

    return db_path


def override_login():
    return "testuser"


@pytest.fixture(autouse=True)
def override_auth():

    app.dependency_overrides[require_login_page] = override_login

    yield

    app.dependency_overrides.clear()


client = TestClient(app)


# =========================================
# create_backup_bytes / _validate_backup_file
# =========================================

def test_create_backup_returns_valid_sqlite_bytes(temp_db_path):

    result = backup_utils.create_backup_bytes()

    assert result[:16] == b"SQLite format 3\x00"


def test_create_backup_missing_file_raises_404(temp_db_path, monkeypatch):

    monkeypatch.setattr(
        backup_utils,
        "DATABASE_URL",
        f"sqlite:///{temp_db_path.parent / 'does-not-exist.db'}"
    )

    with pytest.raises(Exception) as exc_info:
        backup_utils.create_backup_bytes()

    assert "404" in str(exc_info.value) or "neexistuje" in str(exc_info.value)


def test_validate_backup_rejects_empty_file():

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(b"")


def test_validate_backup_rejects_non_sqlite_file():

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(b"not a real sqlite database at all")


def test_validate_backup_rejects_sqlite_file_missing_tables(tmp_path):
    """Platný SQLite súbor, ale bez očakávaných tabuliek tejto appky -
    nesmie sa dať použiť na obnovu (mohol by to byť úplne iný súbor)."""

    unrelated_db = tmp_path / "unrelated.db"

    conn = sqlite3.connect(str(unrelated_db))
    conn.execute("CREATE TABLE something_else (id INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(Exception):
        backup_utils._validate_backup_file(unrelated_db.read_bytes())


def test_validate_backup_accepts_valid_backup(temp_db_path):

    valid_bytes = backup_utils.create_backup_bytes()

    # nesmie vyhodiť výnimku
    backup_utils._validate_backup_file(valid_bytes)


# =========================================
# restore_from_upload
# =========================================

def test_restore_overwrites_live_database(temp_db_path):

    # pridáme dáta do "novej" zálohy, ktorú budeme obnovovať
    other_db_path = temp_db_path.parent / "other.db"

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

    upload_bytes = other_db_path.read_bytes()

    backup_utils.restore_from_upload(upload_bytes)

    # živá DB (temp_db_path) musí teraz obsahovať zákazníka zo zálohy
    conn = sqlite3.connect(str(temp_db_path))
    cursor = conn.execute("SELECT name FROM customers")
    names = [row[0] for row in cursor.fetchall()]
    conn.close()

    assert "Zákazník Z Obnovenej Zálohy" in names


def test_restore_creates_safety_backup_before_overwriting(temp_db_path):

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Customer

    engine = create_engine(f"sqlite:///{temp_db_path}")
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(Customer(name="Pôvodný zákazník pred obnovou"))
    db.commit()
    db.close()

    valid_backup = backup_utils.create_backup_bytes()

    backup_utils.restore_from_upload(valid_backup)

    assert backup_utils.BACKUPS_DIR.exists()

    safety_backups = list(backup_utils.BACKUPS_DIR.glob("pred-obnovou-*.db"))

    assert len(safety_backups) == 1

    # bezpečnostná záloha musí obsahovať pôvodné dáta (spred obnovy)
    conn = sqlite3.connect(str(safety_backups[0]))
    cursor = conn.execute("SELECT name FROM customers")
    names = [row[0] for row in cursor.fetchall()]
    conn.close()

    assert "Pôvodný zákazník pred obnovou" in names


def test_restore_rejects_invalid_file(temp_db_path):

    with pytest.raises(Exception):
        backup_utils.restore_from_upload(b"totally invalid content")


# =========================================
# HTTP ENDPOINTS
# =========================================

def test_download_backup_endpoint(temp_db_path):

    response = client.get("/settings/backup")

    assert response.status_code == 200
    assert response.content[:16] == b"SQLite format 3\x00"
    assert "attachment" in response.headers["content-disposition"]


def test_download_backup_requires_login(temp_db_path):

    del app.dependency_overrides[require_login_page]

    try:

        response = client.get("/settings/backup", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = override_login


def test_restore_endpoint_success(temp_db_path):

    valid_backup = backup_utils.create_backup_bytes()

    response = client.post(
        "/settings/restore",
        files={"backup_file": ("zaloha.db", valid_backup, "application/x-sqlite3")},
        follow_redirects=False
    )

    assert response.status_code == 303
    assert "restored=1" in response.headers["location"]


def test_restore_endpoint_rejects_invalid_file(temp_db_path):

    response = client.post(
        "/settings/restore",
        files={"backup_file": ("fake.db", b"not sqlite", "application/x-sqlite3")}
    )

    assert response.status_code == 422


def test_restore_endpoint_requires_file(temp_db_path):

    response = client.post("/settings/restore", data={})

    assert response.status_code == 422


def test_restore_endpoint_requires_login(temp_db_path):

    del app.dependency_overrides[require_login_page]

    try:

        response = client.post(
            "/settings/restore",
            files={"backup_file": ("zaloha.db", b"x", "application/x-sqlite3")},
            follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = override_login
