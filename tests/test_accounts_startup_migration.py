"""
Lokálny štart po aktualizácii kódu: accounts.db bez nových stĺpcov sa doplní
sama (predtým appka padla s "no such column: accounts.is_active").
"""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import accounts_db

PREVIOUS_REVISION = "5c8e0a1b3d24"   # tesne pred pridaním accounts.is_active


@pytest.fixture
def old_accounts_db(tmp_path, monkeypatch):

    path = tmp_path / "accounts.db"
    url = f"sqlite:///{path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    monkeypatch.setenv("AUTO_MIGRATE_ACCOUNTS", "true")
    monkeypatch.setattr(accounts_db, "ACCOUNTS_DATABASE_URL", url)
    monkeypatch.setattr(accounts_db, "accounts_engine", engine)
    monkeypatch.setattr(accounts_db, "_accounts_db_path", path)

    command.upgrade(Config(str(accounts_db._ACCOUNTS_ALEMBIC_INI)), PREVIOUS_REVISION)

    yield path, engine

    engine.dispose()


def columns(engine):

    return {column["name"] for column in inspect(engine).get_columns("accounts")}


def test_outdated_accounts_database_is_upgraded_with_safety_copy(old_accounts_db):

    path, engine = old_accounts_db

    assert accounts_db.accounts_schema_state() == "outdated"
    assert "is_active" not in columns(engine)

    assert accounts_db.upgrade_accounts_database() is True

    engine.dispose()

    assert accounts_db.accounts_schema_state() == "ok"
    assert "is_active" in columns(engine)
    assert list(path.parent.glob("accounts.db.pre-upgrade-*"))


def test_current_database_is_left_alone(old_accounts_db):

    path, engine = old_accounts_db

    accounts_db.upgrade_accounts_database()

    before = sorted(path.parent.iterdir())

    assert accounts_db.upgrade_accounts_database() is False
    assert sorted(path.parent.iterdir()) == before


def test_upgrade_can_be_disabled_and_then_fails_with_clear_message(old_accounts_db, monkeypatch):

    monkeypatch.setenv("AUTO_MIGRATE_ACCOUNTS", "false")

    with pytest.raises(RuntimeError, match="alembic -c alembic_accounts.ini upgrade head"):
        accounts_db.upgrade_accounts_database()

    assert accounts_db.accounts_schema_state() == "outdated"


def test_uninitialized_database_is_reported(tmp_path, monkeypatch):

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr(accounts_db, "accounts_engine", engine)

    assert accounts_db.accounts_schema_state() == "uninitialized"
    assert accounts_db.upgrade_accounts_database() is False
