"""Migrácie nad reálnymi dátami, zlyhanie jedného konta, súbežný prístup k databáze."""

import os
import sqlite3
import threading
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

import auto_backup
import manage_accounts
import tenancy


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")
    monkeypatch.setenv("BACKUP_MIN_FREE_MB", "1")
    tenancy._engine_cache.clear()

    yield tmp_path

    tenancy._engine_cache.clear()


def config(slug):

    cfg = Config(str(tenancy.PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(tenancy.PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", tenancy.account_database_url(slug))

    return cfg


def head():

    return ScriptDirectory.from_config(config("x")).get_current_head()


def make_dirs(slug):

    tenancy.account_dir(slug).mkdir(parents=True, exist_ok=True)
    tenancy.account_uploads_dir(slug).mkdir(parents=True, exist_ok=True)
    tenancy.account_backups_dir(slug).mkdir(parents=True, exist_ok=True)


def revisions():
    """Všetky revízie od najstaršej po head."""

    script = ScriptDirectory.from_config(config("x"))

    return [rev.revision for rev in reversed(list(script.walk_revisions()))]


def version_of(slug):

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))
    value = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    conn.close()

    return value


# ---------- migrácie nad dátami ----------

def test_migration_chain_has_single_head_and_no_gaps():

    script = ScriptDirectory.from_config(config("x"))

    assert len(script.get_heads()) == 1
    assert len(revisions()) >= 19


def test_empty_database_upgrades_from_scratch_and_twice():

    make_dirs("a")

    tenancy.upgrade_account_database("a")
    tenancy.upgrade_account_database("a")                   # idempotentné

    assert version_of("a") == head()


def test_data_survives_upgrade_from_every_intermediate_revision():
    """Pre každú revíziu: schéma sa postaví na tej revízii, vloží sa zákazník a
    firma, potom sa dopne na head - dáta musia ostať a databáza byť v poriadku."""

    failures = []

    for index, revision in enumerate(revisions()):

        slug = f"r{index}"
        make_dirs(slug)

        command.upgrade(config(slug), revision)

        conn = sqlite3.connect(str(tenancy.account_database_path(slug)))

        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        if "customers" in tables:
            conn.execute("INSERT INTO customers (name) VALUES ('Zachovaný zákazník')")
            conn.commit()

        conn.close()

        try:

            tenancy.upgrade_account_database(slug)

            conn = sqlite3.connect(str(tenancy.account_database_path(slug)))

            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

            if "customers" in tables:
                assert conn.execute("SELECT COUNT(*) FROM customers WHERE name='Zachovaný zákazník'").fetchone()[0] == 1

            assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head()
            conn.close()

        except Exception as exc:                            # noqa: BLE001
            failures.append((revision, repr(exc)))

    assert not failures, failures


def test_upgrade_with_real_invoice_data_keeps_amounts(storage):
    """Faktúra vytvorená na schéme pred poslednými migráciami sa po migrácii číta rovnako."""

    slug = "faktury"
    make_dirs(slug)

    command.upgrade(config(slug), "b5d7f9a1c324")

    conn = sqlite3.connect(str(tenancy.account_database_path(slug)))
    conn.execute("INSERT INTO customers (name) VALUES ('Z')")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(invoices)")]

    values = {
        "invoice_number": "2026001", "customer_id": 1, "status": "Odoslaná",
        "issue_date": "2026-03-15", "due_date": "2026-03-29", "payment_method": "Prevodom",
        "reverse_charge": 0, "is_proforma": 0, "is_credit_note": 0,
    }
    used = {k: v for k, v in values.items() if k in columns}

    conn.execute(
        f"INSERT INTO invoices ({','.join(used)}) VALUES ({','.join('?' * len(used))})",
        list(used.values()),
    )
    conn.execute(
        "INSERT INTO invoice_items (invoice_id, description, quantity, unit, unit_price, vat_rate) "
        "VALUES (1, 'Práca', 3, 'h', 33.33, 23)"
    )
    conn.commit()
    conn.close()

    tenancy.upgrade_account_database(slug)

    engine = tenancy.get_account_engine(slug)

    from sqlalchemy.orm import sessionmaker
    from models import Invoice
    from invoice_utils import calculate_invoice_totals

    session = sessionmaker(bind=engine)()
    invoice = session.query(Invoice).first()

    assert invoice.invoice_number == "2026001"
    assert invoice.reminder_count == 0
    assert str(calculate_invoice_totals(invoice.items)["total_gross"]) == "122.99"   # 99,99 + 23,00
    session.close()


# ---------- zlyhanie migrácie ----------

def fake_account(slug):

    return SimpleNamespace(username=slug, slug=slug)


def test_upgrade_all_continues_after_failing_account_and_exits_nonzero(monkeypatch, capsys):

    for slug in ("dobre1", "pokazene", "dobre2"):
        make_dirs(slug)

    monkeypatch.setattr(manage_accounts, "list_accounts", lambda: [fake_account(s) for s in ("dobre1", "pokazene", "dobre2")])

    real = tenancy.upgrade_account_database

    def selective(slug):

        if slug == "pokazene":
            raise RuntimeError("zlá migrácia")

        real(slug)

    monkeypatch.setattr(tenancy, "upgrade_account_database", selective)

    with pytest.raises(SystemExit) as exit_info:
        manage_accounts.upgrade_all()

    assert exit_info.value.code == 1
    assert version_of("dobre1") == head()
    assert version_of("dobre2") == head()                  # po chybe sa pokračuje ďalej

    output = capsys.readouterr().out

    assert "pokazene" in output and "zlá migrácia" in output


def test_startup_migration_failure_of_one_account_does_not_stop_others(monkeypatch):

    for slug in ("a", "b"):
        make_dirs(slug)
        command.upgrade(config(slug), "d7f9b1c3e546")

    monkeypatch.setattr(auto_backup, "account_slugs", lambda: ["a", "b"])
    monkeypatch.delenv("AUTO_MIGRATE_ACCOUNTS", raising=False)

    real = tenancy.upgrade_account_database

    def selective(slug):

        if slug == "a":
            raise RuntimeError("padla")

        real(slug)

    monkeypatch.setattr(tenancy, "upgrade_account_database", selective)

    assert auto_backup.upgrade_accounts_on_startup() == ["b"]
    assert version_of("a") == "d7f9b1c3e546"              # nedotknuté
    assert version_of("b") == head()
    assert any(tenancy.account_backups_dir("a").glob("*.zip")), "poistná záloha pred migráciou chýba"


def test_data_is_intact_after_failed_migration_of_new_revision(storage):
    """Migrácia, ktorá padne uprostred, nesmie poškodiť existujúce dáta."""

    make_dirs("x")
    command.upgrade(config("x"), "d7f9b1c3e546")

    conn = sqlite3.connect(str(tenancy.account_database_path("x")))
    conn.execute("INSERT INTO customers (name) VALUES ('Prežije')")
    conn.commit()
    conn.close()

    # zlomená migrácia: customer_notes už existuje s iným tvarom a CREATE padne
    conn = sqlite3.connect(str(tenancy.account_database_path("x")))
    conn.execute("CREATE TABLE customer_notes (id INTEGER)")
    conn.commit()
    conn.close()

    tenancy.upgrade_account_database("x")                  # guard v migrácii ju preskočí, nesmie spadnúť

    conn = sqlite3.connect(str(tenancy.account_database_path("x")))

    assert conn.execute("SELECT name FROM customers").fetchall() == [("Prežije",)]
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


# ---------- súbežný prístup ----------

def test_concurrent_writes_during_backups_do_not_lock_or_lose_rows(storage):

    slug = "subeh"
    tenancy.provision_account(slug)

    engine = tenancy.get_account_engine(slug)
    errors = []
    stop = threading.Event()

    def writer(worker):

        try:

            for i in range(30):

                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "INSERT INTO customers (name) VALUES (?)", (f"w{worker}-{i}",)
                    )

        except Exception as exc:                            # noqa: BLE001
            errors.append(repr(exc))

    def backuper():

        try:

            while not stop.is_set():

                result = auto_backup.run_backup(slug, "manual")

                if not result.ok:
                    errors.append(f"záloha: {result.error}")

        except Exception as exc:                            # noqa: BLE001
            errors.append(repr(exc))

    backup_thread = threading.Thread(target=backuper)
    workers = [threading.Thread(target=writer, args=(n,)) for n in range(6)]

    backup_thread.start()

    for thread in workers:
        thread.start()

    for thread in workers:
        thread.join(timeout=120)

    stop.set()
    backup_thread.join(timeout=60)

    assert not errors, errors[:3]

    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM customers").scalar() == 6 * 30

    # posledná záloha sa dá overiť (je konzistentná)
    result = auto_backup.verify_existing_backup(slug, auto_backup.list_backups(slug)[0].name)

    assert result.ok


# ---------- migrácia databázy kont (accounts.db) ----------

def test_accounts_db_migration_adds_email_and_keeps_accounts(tmp_path):
    """Beží tak ako start.sh: samostatný proces s ACCOUNTS_DATABASE_URL (alembic_accounts/env.py
    berie adresu z premennej, nie z konfigurácie)."""

    import subprocess
    import sys

    db_path = tmp_path / "accounts.db"
    env = {**os.environ, "ACCOUNTS_DATABASE_URL": f"sqlite:///{db_path}"}

    def upgrade(revision):

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic_accounts.ini", "upgrade", revision],
            cwd=str(tenancy.PROJECT_ROOT), env=env, capture_output=True, text=True,
        )

        assert result.returncode == 0, result.stderr[-500:]

    upgrade("4b7cf1298419")                              # stav pred pridaním e-mailu

    conn = sqlite3.connect(str(db_path))
    assert "email" not in [row[1] for row in conn.execute("PRAGMA table_info(accounts)")]

    conn.execute(
        "INSERT INTO accounts (username, slug, password_hash, created_at) "
        "VALUES ('jano', 'jano', 'x', '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    upgrade("head")
    upgrade("head")                                      # idempotentné

    conn = sqlite3.connect(str(db_path))

    assert conn.execute("SELECT username, slug, email FROM accounts").fetchall() == [("jano", "jano", None)]
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
