"""Migrácie v jednom procese bežia jedna po druhej (alembic nie je thread-safe)."""

import os
import sqlite3
import threading
import time

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import tenancy


def test_migrations_never_overlap(monkeypatch, tmp_path):

    active = 0
    peak = 0
    guard = threading.Lock()

    def fake_upgrade(cfg, revision):

        nonlocal active, peak

        with guard:
            active += 1
            peak = max(peak, active)

        time.sleep(0.05)

        with guard:
            active -= 1

    monkeypatch.setattr(tenancy.command, "upgrade", fake_upgrade)

    threads = [
        threading.Thread(target=tenancy.upgrade_database_file, args=(tmp_path / f"db{i}.db",))
        for i in range(8)
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert peak == 1


def test_concurrent_real_migrations_all_succeed(tmp_path):

    errors = []

    def run(index):

        try:
            tenancy.upgrade_database_file(tmp_path / f"real{index}.db")

        except Exception as exc:   # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert errors == []

    for index in range(6):

        connection = sqlite3.connect(tmp_path / f"real{index}.db")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.close()

        assert {"customers", "invoices", "audit_log"} <= tables
