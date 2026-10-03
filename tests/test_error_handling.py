"""Testy pre globálny handler neošetrených (500) chýb v main.py."""

import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from auth import require_login_page
from csrf import verify_csrf
from database import Base, get_db
from main import app


@pytest.fixture
def client():

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():

        db = TestingSessionLocal()

        try:
            yield db

        finally:
            db.close()

    def override_login():
        return "testuser"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_login_page] = override_login
    app.dependency_overrides[verify_csrf] = lambda: None

    yield TestClient(app, raise_server_exceptions=False)

    app.dependency_overrides.clear()


def _break_login_dependency():
    """Nahradí require_login_page override-om, ktorý vyhodí RuntimeError -
    spoľahlivo sa zavolá pri KAŽDEJ chránenej požiadavke, na rozdiel od
    funkcie volanej len podmienečne (napr. keď existujú faktúry)."""

    def boom():
        raise RuntimeError("simulovaná programátorská chyba")

    app.dependency_overrides[require_login_page] = boom


def test_unhandled_exception_returns_friendly_html_page(client):

    _break_login_dependency()

    response = client.get("/", headers={"Accept": "text/html"})

    assert response.status_code == 500
    assert "Nastala neočakávaná chyba" in response.text
    # Detail skutočnej výnimky sa používateľovi NEUKAZUJE.
    assert "simulovaná programátorská chyba" not in response.text


def test_unhandled_exception_returns_json_for_non_html_clients(client):

    _break_login_dependency()

    response = client.get("/", headers={"Accept": "application/json"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Nastala neočakávaná chyba. Skús to prosím znova."


def test_unhandled_exception_is_logged_with_traceback(client, caplog):

    _break_login_dependency()

    with caplog.at_level(logging.ERROR, logger="main"):
        client.get("/", headers={"Accept": "text/html"})

    matching = [r for r in caplog.records if r.exc_info is not None]

    assert len(matching) >= 1
    assert "RuntimeError" in caplog.text
    assert "simulovaná programátorská chyba" in caplog.text


def test_normal_requests_still_work_after_handler_registered(client):
    """Regresný test - handler sa nesmie aktivovať pri normálnej požiadavke."""

    response = client.get("/", headers={"Accept": "text/html"})

    assert response.status_code == 200
