"""Obnova prístupu k dátam po strate accounts.db (manage_accounts.py)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import manage_accounts
import tenancy
from accounts_models import Account
from auth import verify_password


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    accounts_db.AccountsBase.metadata.create_all(bind=engine)

    monkeypatch.setattr(manage_accounts, "AccountsSessionLocal", Session)

    yield Session


def test_orphans_listed_and_reattached(isolated, capsys):

    tenancy.provision_account("jozko")

    assert manage_accounts.find_orphan_slugs() == ["jozko"]

    manage_accounts.reattach("jozko", "Jozko", "tajneheslo1")

    db = isolated()
    account = db.query(Account).one()

    assert (account.username, account.slug) == ("Jozko", "jozko")
    assert verify_password("tajneheslo1", account.password_hash)

    db.close()

    assert manage_accounts.find_orphan_slugs() == []


def test_reattach_refuses_unknown_folder_short_password_and_duplicates(isolated):

    tenancy.provision_account("jozko")

    with pytest.raises(SystemExit):
        manage_accounts.reattach("neexistuje", "x", "tajneheslo1")

    with pytest.raises(SystemExit):
        manage_accounts.reattach("jozko", "Jozko", "kratke")

    manage_accounts.reattach("jozko", "Jozko", "tajneheslo1")

    with pytest.raises(SystemExit):          # už má konto
        manage_accounts.reattach("jozko", "Iny", "tajneheslo1")
