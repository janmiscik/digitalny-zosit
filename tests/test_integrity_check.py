"""
Testy pre kontrolu integrity medzi DB a súbormi na disku
(integrity_check.py) a stránku /settings/integrity-check.
"""

import os
import sys
from datetime import date
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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import integrity_check
from auth import require_login_api, require_login_page
from csrf import verify_csrf
from database import Base, get_db
from main import app
from models import AuditLog, Company, Customer, Job, JobPhoto


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine
)


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    uploads_dir = tmp_path / "uploads"
    job_photos_dir = uploads_dir / "job_photos"
    uploads_dir.mkdir()
    job_photos_dir.mkdir()

    # integrity_check.py si UPLOADS_DIR/JOB_PHOTOS_DIR/ALLOWED_EXTENSIONS
    # naimportoval priamo (`from uploads_utils import ...`) - patchuje sa
    # preto na integrity_check module, nie na uploads_utils (rovnaký
    # princíp ako pri iných moduloch v tomto projekte).
    monkeypatch.setattr(integrity_check, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(integrity_check, "JOB_PHOTOS_DIR", job_photos_dir)

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
    app.dependency_overrides[verify_csrf] = lambda: None

    yield {
        "uploads_dir": uploads_dir,
        "job_photos_dir": job_photos_dir
    }

    app.dependency_overrides.clear()


client = TestClient(app)


def db_session():
    return TestingSessionLocal()


# =========================================
# check_integrity() - priame jednotky
# =========================================

def test_clean_when_nothing_referenced_and_no_files():

    db = db_session()
    report = integrity_check.check_integrity(db)
    db.close()

    assert report.is_clean
    assert report.missing_files == []
    assert report.orphan_files == []


def test_detects_missing_logo_file(setup_test_env):

    db = db_session()
    db.add(Company(name="Firma", logo_filename="logo.png"))
    db.commit()

    report = integrity_check.check_integrity(db)
    db.close()

    assert not report.is_clean
    assert len(report.missing_files) == 1
    assert report.missing_files[0].filename == "logo.png"
    assert report.missing_files[0].description == "Logo firmy"


def test_no_missing_file_when_logo_exists_on_disk(setup_test_env):

    (setup_test_env["uploads_dir"] / "logo.png").write_bytes(b"x")

    db = db_session()
    db.add(Company(name="Firma", logo_filename="logo.png"))
    db.commit()

    report = integrity_check.check_integrity(db)
    db.close()

    assert report.is_clean


def test_detects_missing_job_photo(setup_test_env):

    db = db_session()

    customer = Customer(name="Zákazník")
    db.add(customer)
    db.commit()

    job = Job(title="Zákazka", status="Nová", customer_id=customer.id)
    db.add(job)
    db.commit()

    job_id = job.id

    db.add(JobPhoto(
        job_id=job.id,
        filename="job1-abc.jpg",
        photo_type="pred",
        uploaded_at=date.today()
    ))
    db.commit()

    report = integrity_check.check_integrity(db)
    db.close()

    assert len(report.missing_files) == 1
    assert report.missing_files[0].filename == "job1-abc.jpg"
    assert str(job_id) in report.missing_files[0].description


def test_detects_orphan_top_level_file(setup_test_env):

    (setup_test_env["uploads_dir"] / "signature.png").write_bytes(b"x")

    db = db_session()
    report = integrity_check.check_integrity(db)
    db.close()

    assert len(report.orphan_files) == 1
    assert report.orphan_files[0].relative_path == "signature.png"


def test_ignores_non_image_files_in_uploads_dir(setup_test_env):
    """Napr. .gitkeep alebo iný súbor nepatriaci medzi obrázky sa
    nesmie hlásiť ako osirotený - ALLOWED_EXTENSIONS filter."""

    (setup_test_env["uploads_dir"] / "readme.txt").write_bytes(b"poznamka")

    db = db_session()
    report = integrity_check.check_integrity(db)
    db.close()

    assert report.orphan_files == []


def test_detects_orphan_job_photo(setup_test_env):

    (setup_test_env["job_photos_dir"] / "job99-orphan.jpg").write_bytes(b"x")

    db = db_session()
    report = integrity_check.check_integrity(db)
    db.close()

    assert len(report.orphan_files) == 1
    assert report.orphan_files[0].relative_path == "job_photos/job99-orphan.jpg"


def test_referenced_file_is_not_flagged_as_orphan(setup_test_env):

    (setup_test_env["uploads_dir"] / "logo.png").write_bytes(b"x")

    db = db_session()
    db.add(Company(name="Firma", logo_filename="logo.png"))
    db.commit()

    report = integrity_check.check_integrity(db)
    db.close()

    assert report.is_clean


# =========================================
# cleanup_orphan_files()
# =========================================

def test_cleanup_deletes_only_orphan_files(setup_test_env):

    uploads_dir = setup_test_env["uploads_dir"]

    (uploads_dir / "logo.png").write_bytes(b"referencovane")
    (uploads_dir / "old-signature.png").write_bytes(b"osirotene")

    db = db_session()
    db.add(Company(name="Firma", logo_filename="logo.png"))
    db.commit()

    deleted = integrity_check.cleanup_orphan_files(db)
    db.close()

    assert deleted == ["old-signature.png"]
    assert (uploads_dir / "logo.png").exists()
    assert not (uploads_dir / "old-signature.png").exists()


def test_cleanup_returns_empty_list_when_nothing_to_clean(setup_test_env):

    db = db_session()
    deleted = integrity_check.cleanup_orphan_files(db)
    db.close()

    assert deleted == []


# =========================================
# HTTP routes
# =========================================

def test_integrity_check_page_shows_clean_banner():

    response = client.get("/settings/integrity-check")

    assert response.status_code == 200
    assert "Všetko v poriadku" in response.text


def test_integrity_check_page_lists_orphan_file(setup_test_env):

    (setup_test_env["uploads_dir"] / "orphan.png").write_bytes(b"x")

    response = client.get("/settings/integrity-check")

    assert response.status_code == 200
    assert "orphan.png" in response.text


def test_integrity_check_cleanup_endpoint_deletes_and_logs(setup_test_env):

    uploads_dir = setup_test_env["uploads_dir"]

    (uploads_dir / "orphan.png").write_bytes(b"x")

    response = client.post(
        "/settings/integrity-check/cleanup",
        follow_redirects=False
    )

    assert response.status_code == 303
    assert not (uploads_dir / "orphan.png").exists()

    db = db_session()
    entry = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    db.close()

    assert entry.action == "integrity.cleanup_orphans"
    assert "orphan.png" in entry.detail


def test_integrity_check_page_requires_login():

    app.dependency_overrides.pop(require_login_page, None)

    try:
        response = client.get(
            "/settings/integrity-check",
            follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:
        app.dependency_overrides[require_login_page] = lambda: "testuser"
