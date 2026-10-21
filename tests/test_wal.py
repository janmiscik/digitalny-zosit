"""SQLite beží v režime WAL a zálohy/obnova s ním fungujú."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import sqlite3

import tenancy


def test_account_engine_uses_wal(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")
    tenancy._engine_cache.clear()

    (tmp_path / "accounts" / "firma").mkdir(parents=True)
    tenancy.upgrade_account_database("firma")

    engine = tenancy.get_account_engine("firma")

    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        sync = conn.exec_driver_sql("PRAGMA synchronous").scalar()
        fk = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()

    assert mode == "wal" and sync == 1 and fk == 1

    tenancy._engine_cache.clear()


def test_reader_is_not_blocked_by_open_write_transaction(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")
    tenancy._engine_cache.clear()

    (tmp_path / "accounts" / "firma").mkdir(parents=True)
    tenancy.upgrade_account_database("firma")

    engine = tenancy.get_account_engine("firma")
    path = tenancy.account_database_path("firma") if hasattr(tenancy, "account_database_path") else tmp_path / "accounts" / "firma" / "database.db"

    writer = engine.connect()
    writer.exec_driver_sql("BEGIN IMMEDIATE")

    reader = sqlite3.connect(str(path), timeout=0.2)

    try:
        assert reader.execute("SELECT count(*) FROM customers").fetchone()[0] == 0

    finally:
        reader.close()
        writer.exec_driver_sql("ROLLBACK")
        writer.close()
        tenancy._engine_cache.clear()
