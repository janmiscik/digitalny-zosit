import io
import os
import sys
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD_HASH", "")


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)


import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import require_login_api, require_login_page
from database import Base, get_db
from main import app
from models import Company
from uploads_utils import UPLOADS_DIR, delete_image, image_path


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(bind=test_engine)


def make_png_bytes(color=(255, 0, 0), size=(40, 20)) -> bytes:

    buffer = io.BytesIO()

    img = PILImage.new("RGB", size, color)
    img.save(buffer, format="PNG")

    return buffer.getvalue()


@pytest.fixture(autouse=True)
def setup_test_database():

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

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
    app.dependency_overrides[require_login_api] = override_login

    # Vyčistíme test-obrázky pred aj po teste, aby testy neboli ovplyvnené
    # predchádzajúcimi behmi
    delete_image("logo")
    delete_image("signature")

    yield

    delete_image("logo")
    delete_image("signature")

    app.dependency_overrides.clear()


client = TestClient(app)


# =========================================
# NAHRÁVANIE - SETTINGS ENDPOINT
# =========================================

def test_settings_upload_logo():

    png_bytes = make_png_bytes()

    response = client.post(
        "/settings",
        data={"name": "Firma s logom"},
        files={"logo": ("logo.png", png_bytes, "image/png")},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.logo_filename == "logo.png"
    assert image_path(company.logo_filename) is not None


def test_settings_upload_signature():

    png_bytes = make_png_bytes(color=(0, 0, 255))

    response = client.post(
        "/settings",
        data={"name": "Firma s podpisom"},
        files={"signature": ("podpis.png", png_bytes, "image/png")},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.signature_filename == "signature.png"


def test_settings_upload_invalid_extension():

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.gif", b"not a real gif but bytes", "image/gif")}
    )

    assert response.status_code == 422


def test_settings_upload_too_large():

    # Vytvoríme "súbor" väčší ako 2 MB limit
    huge_bytes = b"0" * (3 * 1024 * 1024)

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", huge_bytes, "image/png")}
    )

    assert response.status_code == 422


def test_settings_remove_logo():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    db = TestingSessionLocal()
    company = db.query(Company).first()
    assert company.logo_filename == "logo.png"
    db.close()


    response = client.post(
        "/settings",
        data={"name": "Firma", "remove_logo": "1"},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.logo_filename is None
    assert image_path("logo.png") is None


def test_settings_replace_logo_removes_old_extension():
    """
    Ak sa logo nahradí súborom v inom formáte (napr. .png -> .jpg),
    starý súbor sa musí odstrániť, nie len ponechať vedľa nového.
    """

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    assert (UPLOADS_DIR / "logo.png").exists()


    buffer = io.BytesIO()
    PILImage.new("RGB", (30, 30), (0, 255, 0)).save(buffer, format="JPEG")
    jpg_bytes = buffer.getvalue()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.jpg", jpg_bytes, "image/jpeg")}
    )

    assert not (UPLOADS_DIR / "logo.png").exists()
    assert (UPLOADS_DIR / "logo.jpg").exists()


def test_settings_form_without_files_keeps_existing_logo():
    """
    Uloženie nastavení bez výberu nového súboru nesmie zmazať
    už nahraté logo.
    """

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    client.post(
        "/settings",
        data={"name": "Firma s aktualizovaným nazvom"}
    )

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.logo_filename == "logo.png"
    assert company.name == "Firma s aktualizovaným nazvom"


# =========================================
# NÁHĽAD V NASTAVENIACH
# =========================================

def test_settings_page_shows_logo_preview():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    response = client.get("/settings")

    assert response.status_code == 200
    assert "/uploads/logo.png" in response.text


# =========================================
# OBRÁZOK JE DOSTUPNÝ CEZ /uploads
# =========================================

def test_uploaded_logo_is_served():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    response = client.get("/uploads/logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


# =========================================
# BEZPEČNOSŤ - PRÍSTUP BEZ PRIHLÁSENIA
#
# Tieto testy zámerne odstránia override pre require_login_page, aby
# overili SKUTOČNÉ správanie (nie testovací "vždy prihlásený" skrat).
# =========================================

def test_uploaded_logo_blocked_without_login():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    del app.dependency_overrides[require_login_page]

    try:

        response = client.get("/uploads/logo.png", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = lambda: "testuser"


def test_uploads_path_traversal_blocked():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    traversal_attempts = [
        "/uploads/..%2Fmain.py",
        "/uploads/..%2f..%2fmain.py",
        "/uploads/%2e%2e%2fmain.py",
    ]

    for path in traversal_attempts:

        response = client.get(path, follow_redirects=False)

        assert response.status_code == 404, f"Zlyhalo pre: {path}"
        assert b"import" not in response.content
        assert b"FastAPI" not in response.content


def test_uploads_rejects_disallowed_extension():

    response = client.get("/uploads/hacker.exe")

    assert response.status_code == 404


def test_uploads_rejects_unknown_filename():

    response = client.get("/uploads/random-file.png")

    assert response.status_code == 404
