"""Testy PRAGMA integrity_check v backup_utils (tvorba aj obnova zálohy)."""

import io
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

import backup_utils


def _make_db(path: Path, rows: int = 3000) -> None:

    conn = sqlite3.connect(str(path))

    for table in backup_utils.REQUIRED_TABLES:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")

    conn.execute("CREATE INDEX ix_customers_v ON customers(v)")
    conn.executemany(
        "INSERT INTO customers(v) VALUES (?)",
        [(f"row{i}" * 20,) for i in range(rows)]
    )
    conn.commit()
    conn.close()


def _corrupt_middle(path: Path) -> None:

    data = bytearray(path.read_bytes())
    middle = len(data) // 2

    for offset in range(middle, middle + 4096):
        data[offset] = 0xFF

    path.write_bytes(bytes(data))


def test_healthy_database_passes_integrity_check(tmp_path):

    db_path = tmp_path / "ok.db"
    _make_db(db_path)

    conn = sqlite3.connect(str(db_path))

    assert backup_utils._integrity_check_failure(conn) is None

    conn.close()


def test_backup_creation_refuses_corrupted_database(tmp_path, patch_tenant_paths):

    db_path = tmp_path / "live.db"
    _make_db(db_path)
    _corrupt_middle(db_path)

    patch_tenant_paths(
        db_path=db_path,
        uploads=tmp_path / "uploads",
        backups=tmp_path / "backups"
    )

    with pytest.raises(Exception) as exc_info:
        backup_utils.create_backup_bytes()

    # Poškodenie sa zachytí buď pri zálohovaní (sqlite3.DatabaseError),
    # alebo pri kontrole integrity (HTTPException 500) - v OBOCH
    # prípadoch sa nesmie vytvoriť "úspešná" záloha.
    assert isinstance(
        exc_info.value, (HTTPException, sqlite3.DatabaseError)
    )


def test_restore_validation_rejects_corrupted_backup(tmp_path):

    db_path = tmp_path / "backup.db"
    _make_db(db_path)
    _corrupt_middle(db_path)

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("database.db", db_path.read_bytes())

    with pytest.raises(HTTPException) as exc_info:
        backup_utils._validate_backup_file(buffer.getvalue())

    assert exc_info.value.status_code == 422


def test_restore_validation_reports_integrity_problem(tmp_path, monkeypatch):

    db_path = tmp_path / "backup.db"
    _make_db(db_path, rows=10)

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("database.db", db_path.read_bytes())

    monkeypatch.setattr(
        backup_utils,
        "_integrity_check_failure",
        lambda conn: "*** in database main *** Page 5: btree error"
    )

    with pytest.raises(HTTPException) as exc_info:
        backup_utils._validate_backup_file(buffer.getvalue())

    assert exc_info.value.status_code == 422
    assert "integrity_check" in exc_info.value.detail
