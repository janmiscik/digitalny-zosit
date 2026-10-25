"""
Ochranné triggery v UŽ EXISTUJÚCICH databázach: po aktualizácii appky (migrácie) a po
obnove starej zálohy musia byť nainštalované, nielen pri vytvorení novej schémy.

Staré konto sa vyrobí skutočne: databáza zmigrovaná na revíziu pred triggermi
(e8a0b2c4d657) s reálnymi dátami (vystavená faktúra, návrh, audit záznamy).
"""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

import db_guards
import tenancy
from tests.test_multi_tenant import isolated_tenancy  # noqa: F401

PRE_GUARDS_REVISION = "e8a0b2c4d657"
ALL_TRIGGERS = set(db_guards.TRIGGERS)


def legacy_database(path: Path) -> None:
    """Databáza na starej revízii (bez triggerov) s dátami."""

    path.parent.mkdir(parents=True, exist_ok=True)

    cfg = Config(str(tenancy.PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(tenancy.PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(cfg, PRE_GUARDS_REVISION)

    connection = sqlite3.connect(path)

    try:
        assert triggers_in(connection) == set()    # naozaj stará schéma bez ochrán

        connection.execute("INSERT INTO customers (id, name) VALUES (1, 'Starý zákazník')")

        for invoice_id, number, status in ((1, "FA-OLD-1", "Odoslaná"), (2, "FA-OLD-2", "Návrh")):
            connection.execute(
                "INSERT INTO invoices (id, invoice_number, customer_id, status, issue_date, due_date) "
                "VALUES (?, ?, 1, ?, '2026-01-01', '2026-01-15')", (invoice_id, number, status))
            connection.execute(
                "INSERT INTO invoice_items (invoice_id, description, quantity, unit, unit_price, vat_rate) "
                "VALUES (?, 'Práca', 1, 'ks', 100, 0)", (invoice_id,))

        connection.execute(
            "INSERT INTO audit_log (created_at, action, entity_type, entity_id, detail) "
            "VALUES ('2026-01-01 10:00:00', 'invoice.create', 'invoice', 1, 'starý záznam')")
        connection.commit()

    finally:
        connection.close()


def triggers_in(connection) -> set[str]:

    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}


def refuses(connection, sql: str) -> bool:

    try:
        connection.execute(sql)
        connection.rollback()

    except sqlite3.DatabaseError:
        connection.rollback()
        return True

    return False


def assert_guards_work(path: Path) -> None:

    connection = sqlite3.connect(path)

    try:
        assert triggers_in(connection) == ALL_TRIGGERS

        assert refuses(connection, "DELETE FROM invoices WHERE id = 1")
        assert refuses(connection, "UPDATE invoices SET status = 'Návrh' WHERE id = 1")
        assert refuses(connection, "UPDATE invoice_items SET unit_price = 1 WHERE invoice_id = 1")
        assert refuses(connection, "DELETE FROM invoice_items WHERE invoice_id = 1")
        assert refuses(connection, "UPDATE audit_log SET detail = 'prepísané'")
        assert refuses(connection, "DELETE FROM audit_log")

        # návrh sa dá aj naďalej meniť a mazať (ochrana nesmie zablokovať bežnú prácu)
        assert not refuses(connection, "UPDATE invoice_items SET unit_price = 150 WHERE invoice_id = 2")
        assert not refuses(connection, "DELETE FROM invoices WHERE id = 2")

        # dáta pôvodnej vystavenej faktúry ostali netknuté
        assert connection.execute("SELECT status FROM invoices WHERE id = 1").fetchone()[0] == "Odoslaná"
        assert connection.execute("SELECT unit_price FROM invoice_items WHERE invoice_id = 1").fetchone()[0] == 100
        assert connection.execute("SELECT detail FROM audit_log").fetchone()[0] == "starý záznam"

    finally:
        connection.close()


def test_update_installs_guards_into_existing_account_database():

    slug = "stare-konto"
    legacy_database(tenancy.account_database_path(slug))

    assert tenancy.account_needs_migration(slug)

    tenancy.upgrade_account_database(slug)

    assert not tenancy.account_needs_migration(slug)

    assert_guards_work(tenancy.account_database_path(slug))


def test_upgrade_is_repeatable_and_does_not_duplicate_or_fail():

    slug = "opakovane"
    legacy_database(tenancy.account_database_path(slug))

    tenancy.upgrade_account_database(slug)
    tenancy.upgrade_account_database(slug)    # druhý beh = nič sa nemení

    assert_guards_work(tenancy.account_database_path(slug))


def test_restored_old_backup_database_gets_guards_too(tmp_path):
    """Obnova starej zálohy: dočasná kópia sa zmigruje na aktuálnu schému aj s ochranami."""

    path = tmp_path / "stara-zaloha.db"
    legacy_database(path)

    tenancy.upgrade_database_file(path)

    assert_guards_work(path)


def test_new_account_has_the_same_guards_as_a_migrated_old_one():

    slug = "nove-konto"
    tenancy.provision_account(slug)

    connection = sqlite3.connect(tenancy.account_database_path(slug))

    try:
        assert triggers_in(connection) == ALL_TRIGGERS

    finally:
        connection.close()


def test_missing_guard_is_detected_not_silently_accepted():
    """Ak trigger chýba (napr. ručný zásah), kontrola integrity to musí vedieť zistiť."""

    slug = "chybajuci"
    tenancy.provision_account(slug)
    path = tenancy.account_database_path(slug)

    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER invoices_no_delete_issued")
    connection.commit()

    assert triggers_in(connection) != ALL_TRIGGERS

    connection.close()

    import monitoring

    # kontrola nastavená v monitoringu/integrity-check musí chýbajúci trigger nahlásiť
    assert hasattr(db_guards, "missing_triggers"), "chýba db_guards.missing_triggers (kontrola ochrán)"
    assert db_guards.missing_triggers(path) == {"invoices_no_delete_issued"}
