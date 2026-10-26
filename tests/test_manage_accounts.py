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


# ---------- --create-account (kontá zakladá majiteľ servera) ----------

def test_create_account_provisions_a_complete_working_account(isolated, capsys):

    import db_guards

    manage_accounts.create_account("Novy Zivnostnik", "Zelen4-Konvalinka")

    db = isolated()
    account = db.query(Account).one()
    db.close()

    assert account.username == "Novy Zivnostnik"
    assert verify_password("Zelen4-Konvalinka", account.password_hash)
    assert not tenancy.account_needs_migration(account.slug)
    assert db_guards.missing_triggers(tenancy.account_database_path(account.slug)) == set()
    assert tenancy.account_uploads_dir(account.slug).is_dir()
    assert "Hotovo" in capsys.readouterr().out


@pytest.mark.parametrize("username, password", [
    ("ab", "Zelen4-Konvalinka"),            # krátke meno
    ("firma-a", "kratke1"),                  # krátke heslo
    ("firma-a", "password123"),              # bežné heslo
    ("firma-a", "firma-a"),                  # heslo = meno
])
def test_create_account_refuses_weak_input_and_creates_nothing(isolated, username, password):

    with pytest.raises(SystemExit):
        manage_accounts.create_account(username, password)

    db = isolated()
    assert db.query(Account).count() == 0
    db.close()

    assert not (tenancy.ACCOUNTS_DATA_DIR).exists() or not list(tenancy.ACCOUNTS_DATA_DIR.iterdir())


def test_create_account_refuses_duplicate_username(isolated):

    manage_accounts.create_account("firma-a", "Zelen4-Konvalinka")

    with pytest.raises(SystemExit):
        manage_accounts.create_account("firma-a", "Ina-Silna-Veta-77")

    db = isolated()
    assert db.query(Account).count() == 1
    db.close()


def test_create_account_rolls_back_when_provisioning_fails(isolated, monkeypatch):

    def boom(slug):
        raise RuntimeError("disk plný")

    monkeypatch.setattr(tenancy, "provision_account", boom)

    with pytest.raises(SystemExit):
        manage_accounts.create_account("firma-a", "Zelen4-Konvalinka")

    db = isolated()
    assert db.query(Account).count() == 0
    db.close()


def test_created_account_can_log_in_even_when_registration_is_closed(isolated, monkeypatch):

    import re

    from fastapi.testclient import TestClient

    from main import app

    monkeypatch.setenv("REGISTRATION_ENABLED", "false")
    app.dependency_overrides[accounts_db.get_accounts_db] = lambda: isolated()

    try:
        manage_accounts.create_account("firma-a", "Zelen4-Konvalinka")

        client = TestClient(app)
        token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
        response = client.post("/login", data={"csrf_token": token, "username": "firma-a",
                                               "password": "Zelen4-Konvalinka"}, follow_redirects=False)

        assert response.status_code == 303 and response.headers["location"] == "/"
        assert client.get("/register").status_code == 403

    finally:
        app.dependency_overrides.clear()
