"""
Testy nasadenia (Railway): /health endpoint, štart na porte z $PORT,
migrácie existujúcich kont pri štarte a konfigurácia disku (volume).
"""

import os
import sys
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

import main
import manage_accounts
import tenancy


client = TestClient(main.app)


# =========================================
# /health
# =========================================

def test_health_is_public_and_returns_ok():

    response = client.get("/health", follow_redirects=False)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_503_when_accounts_database_is_unavailable(monkeypatch):

    class BrokenEngine:

        def connect(self):
            raise RuntimeError("disk nie je pripojený")

    monkeypatch.setattr(main, "accounts_engine", BrokenEngine())

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error"}
    # detail chyby sa nesmie dostať von
    assert "disk" not in response.text


# =========================================
# PORT + VOLUME (Dockerfile, start.sh, railway.json)
# =========================================

def test_start_script_listens_on_port_variable():

    script = (PROJECT_ROOT / "start.sh").read_text()

    assert '--port "${PORT:-8000}"' in script
    assert "--port 8000" not in script


def test_start_script_migrates_accounts_db_then_all_accounts_then_starts_app():

    script = (PROJECT_ROOT / "start.sh").read_text()

    accounts_migration = script.index("alembic -c alembic_accounts.ini upgrade head")
    upgrade_all = script.index("manage_accounts.py --upgrade-all")
    app_start = script.index("uvicorn main:app")

    assert accounts_migration < upgrade_all < app_start
    assert "set -e" in script


def test_dockerfile_points_data_into_volume_path_without_volume_keyword():

    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text()

    # Railway nepodporuje instrukciu VOLUME - volume sa pripája v službe
    instructions = [
        line.strip().split()[0].upper()
        for line in dockerfile.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "VOLUME" not in instructions
    assert "ACCOUNTS_DATABASE_URL=sqlite:////app/data/accounts.db" in dockerfile
    assert "ACCOUNTS_DATA_DIR=/app/data/accounts" in dockerfile


def test_railway_config_uses_health_endpoint():

    import json

    config = json.loads((PROJECT_ROOT / "railway.json").read_text())

    assert config["deploy"]["healthcheckPath"] == "/health"


# =========================================
# MIGRÁCIE EXISTUJÚCICH KONT (manage_accounts --upgrade-all)
# =========================================

def _business_config(slug: str) -> Config:

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", tenancy.account_database_url(slug))

    return cfg


def _current_revision(slug: str) -> str | None:

    engine = create_engine(tenancy.account_database_url(slug))

    try:

        with engine.connect() as connection:
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar()

    finally:
        engine.dispose()


def test_upgrade_all_migrates_existing_account_to_head(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    slug = "stare-konto"

    # konto, ktoré vzniklo so STARŠOU verziou appky (o jednu migráciu pozadu)
    tenancy.provision_account(slug)
    command.downgrade(_business_config(slug), "-1")

    head = ScriptDirectory.from_config(_business_config(slug)).get_current_head()

    assert _current_revision(slug) != head

    class FakeAccount:
        id = 1
        username = "stare"

    FakeAccount.slug = slug

    monkeypatch.setattr(manage_accounts, "list_accounts", lambda: [FakeAccount()])

    manage_accounts.upgrade_all()

    assert _current_revision(slug) == head

    tenancy.discard_account_engine(slug)
