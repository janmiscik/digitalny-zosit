"""
Regresia: trasy bez get_db (fotky zákaziek, logo/podpis) musia poznať
aktuálne konto aj pri skutočnom prihlásení. Predtým
require_login_page bola sync dependency - ContextVar sa nastavil vo
vlákne s kópiou kontextu a obsluha trasy skončila na RuntimeError.
"""

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import accounts_db
import tenancy
from main import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    accounts_db.AccountsBase.metadata.create_all(bind=engine)

    def override():

        db = Session()

        try:
            yield db

        finally:
            db.close()

    app.dependency_overrides[accounts_db.get_accounts_db] = override
    tenancy._engine_cache.clear()

    yield

    app.dependency_overrides.clear()
    tenancy._engine_cache.clear()


def logged_in_client(username):

    client = TestClient(app)

    token = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/register").text).group(1)

    response = client.post(
        "/register",
        data={"csrf_token": token, "username": username,
              "password": "Bezpecne-heslo-9", "password_confirm": "Bezpecne-heslo-9"},
        follow_redirects=False,
    )

    assert response.status_code == 303

    return client, tenancy.slugify_username(username)


def put_photo(slug, name, content=b"\xff\xd8\xff\xe0jpeg-bytes"):

    folder = tenancy.account_uploads_dir(slug) / "job_photos"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(content)


def test_job_photo_is_served_after_real_login():

    client, slug = logged_in_client("firmaA")

    put_photo(slug, "foto1.jpg")

    response = client.get("/jobs/photos/foto1.jpg")

    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8")


def test_job_photo_of_other_account_is_not_served():

    client_a, slug_a = logged_in_client("firmaA")
    put_photo(slug_a, "tajne.jpg")

    client_b, _ = logged_in_client("firmaB")

    assert client_b.get("/jobs/photos/tajne.jpg").status_code == 404


def test_job_photo_requires_login():

    client, slug = logged_in_client("firmaA")
    put_photo(slug, "foto1.jpg")

    anonymous = TestClient(app)

    assert anonymous.get("/jobs/photos/foto1.jpg", follow_redirects=False).status_code == 303
