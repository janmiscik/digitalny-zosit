import os
import sys
from datetime import date
from pathlib import Path


# =========================================
# ENV PREMENNÉ (musia byť nastavené PRED importom main.py)
# =========================================

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

from auth import require_login_api, require_login_page
from database import Base, get_db
from main import app
from models import Customer, Job


# =========================================
# TEST DATABASE
# =========================================

TEST_DATABASE_URL = "sqlite://"


test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={
        "check_same_thread": False
    },
    poolclass=StaticPool
)


TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine
)


# =========================================
# TEST FIXTURE
# =========================================

@pytest.fixture(autouse=True)
def setup_test_database():

    Base.metadata.drop_all(
        bind=test_engine
    )

    Base.metadata.create_all(
        bind=test_engine
    )

    db = TestingSessionLocal()

    customer = Customer(
        name="Testovací zákazník",
        phone="0900123456",
        email="test@example.com",
        address="Testovacia 1",
        note="Test"
    )

    db.add(customer)

    db.commit()

    db.refresh(customer)

    job = Job(
        title="Testovacia zákazka",
        description="Testovací popis",
        status="Nová",
        due_date=date(2026, 8, 20),
        customer_id=customer.id
    )

    db.add(job)

    db.commit()

    db.close()

    def override_get_db():

        db = TestingSessionLocal()

        try:
            yield db

        finally:
            db.close()

    # Testujeme business logiku CRUD operácií, nie samotnú autentifikáciu
    # (tá má vlastné testy v test_auth.py) - preto login vyžadovanie tu obídeme.
    def override_login():
        return "testuser"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_login_page] = override_login
    app.dependency_overrides[require_login_api] = override_login

    yield

    app.dependency_overrides.clear()


client = TestClient(app)


# =========================================
# DASHBOARD
# =========================================

def test_dashboard():

    response = client.get("/")

    assert response.status_code == 200


def test_customers_list_page():

    response = client.get("/zakaznici")

    assert response.status_code == 200
    assert "Testovací zákazník" in response.text


def test_invoices_list_page():

    response = client.get("/faktury")

    assert response.status_code == 200


def test_invoices_list_page_filtered_by_status():

    response = client.get("/faktury?status=Návrh")

    assert response.status_code == 200


def test_jobs_list_page():

    response = client.get("/zakazky")

    assert response.status_code == 200
    assert "Testovacia zákazka" in response.text


def test_jobs_list_page_filtered_by_status():

    response = client.get("/zakazky?status=Nová")

    assert response.status_code == 200


def test_jobs_list_page_filtered_by_overdue():

    response = client.get("/zakazky?when=overdue")

    assert response.status_code == 200


def test_jobs_list_page_filtered_by_no_date():

    response = client.get("/zakazky?when=no_date")

    assert response.status_code == 200


# =========================================
# CUSTOMERS
# =========================================

def test_get_customers():

    response = client.get("/customers")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_customer_detail():

    response = client.get("/customers/1")

    assert response.status_code == 200


def test_customer_not_found():

    response = client.get("/customers/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Zákazník neexistuje"


def test_create_customer():

    response = client.post(
        "/customers",
        data={
            "name": "Nový zákazník",
            "phone": "0900111222",
            "email": "novy@example.com",
            "address": "Nová 1",
            "note": "Test"
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/zakaznici"


def test_create_customer_missing_name():

    response = client.post(
        "/customers",
        data={
            "name": "",
            "phone": "0900111222"
        },
        follow_redirects=False
    )

    assert response.status_code == 422


def test_update_customer():

    response = client.post(
        "/customers/1/edit",
        data={
            "name": "Upravený zákazník",
            "phone": "0911222333",
            "email": "upraveny@example.com",
            "address": "Nová adresa 5",
            "note": "Upravená poznámka"
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customers/1"


def test_update_customer_not_found():

    response = client.post(
        "/customers/999999/edit",
        data={
            "name": "Neexistujúci zákazník",
            "phone": "0900111222",
            "email": "none@example.com",
            "address": "Nikde 1",
            "note": "Test"
        }
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Zákazník neexistuje"


def test_get_customers_by_search():

    response = client.get(
        "/customers?search=Testovací"
    )

    assert response.status_code == 200

    customers = response.json()

    for customer in customers:

        assert "testovací" in customer["name"].lower()


def test_get_customers_sorted_by_name():

    response = client.get("/customers")

    assert response.status_code == 200

    customers = response.json()

    names = [
        customer["name"]
        for customer in customers
    ]

    assert names == sorted(names)


# =========================================
# JOBS
# =========================================

def test_get_jobs():

    response = client.get("/jobs")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_create_job():

    response = client.post(
        "/jobs",
        data={
            "title": "Nová zákazka",
            "description": "Testovací popis",
            "status": "Nová",
            "due_date": "2026-08-20",
            "customer_id": 1
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customers/1"


def test_job_not_found():

    response = client.get("/jobs/999999/edit")

    assert response.status_code == 404
    assert response.json()["detail"] == "Zákazka neexistuje"


def test_update_job():

    response = client.post(
        "/jobs/1/edit",
        data={
            "title": "Upravená zákazka",
            "description": "Upravený popis",
            "status": "Prebieha",
            "due_date": "2026-08-25"
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customers/1"


def test_update_job_status():

    response = client.post(
        "/jobs/1/status",
        data={
            "status": "Hotová"
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customers/1"

    response = client.get("/jobs")

    assert response.status_code == 200

    jobs = response.json()

    job = next(
        job
        for job in jobs
        if job["id"] == 1
    )

    assert job["status"] == "Hotová"


def test_update_job_status_not_found():

    response = client.post(
        "/jobs/999999/status",
        data={
            "status": "Hotová"
        }
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Zákazka neexistuje"


def test_update_job_status_invalid():

    response = client.post(
        "/jobs/1/status",
        data={
            "status": "Neexistujúci stav"
        }
    )

    assert response.status_code == 422


def test_create_job_customer_not_found():

    response = client.post(
        "/jobs",
        data={
            "title": "Testovacia zákazka",
            "description": "Testovací popis",
            "status": "Nová",
            "due_date": "2026-08-20",
            "customer_id": 999999
        },
        follow_redirects=False
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Zákazník neexistuje"


def test_create_job_invalid_due_date():

    response = client.post(
        "/jobs",
        data={
            "title": "Zákazka s zlým dátumom",
            "description": "Test",
            "status": "Nová",
            "due_date": "nie je dátum",
            "customer_id": 1
        },
        follow_redirects=False
    )

    assert response.status_code == 400


def test_edit_job_not_found():

    response = client.get("/jobs/999999/edit")

    assert response.status_code == 404
    assert response.json()["detail"] == "Zákazka neexistuje"


def test_invalid_job_status():

    response = client.get("/jobs")

    assert response.status_code == 200

    jobs = response.json()

    for job in jobs:

        assert job["status"] in {
            "Nová",
            "Dohodnutá",
            "Prebieha",
            "Hotová"
        }


def test_create_job_agreed():

    response = client.post(
        "/jobs",
        data={
            "title": "Dohodnutá zákazka",
            "description": "Test",
            "status": "Dohodnutá",
            "due_date": "2026-08-25",
            "customer_id": 1
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customers/1"


def test_job_status_enum():

    from schemas import JobStatus

    assert JobStatus.NEW.value == "Nová"
    assert JobStatus.AGREED.value == "Dohodnutá"
    assert JobStatus.IN_PROGRESS.value == "Prebieha"
    assert JobStatus.DONE.value == "Hotová"


def test_get_jobs_by_status():

    response = client.get(
        "/jobs?status=Hotová"
    )

    assert response.status_code == 200

    jobs = response.json()

    for job in jobs:

        assert job["status"] == "Hotová"


def test_get_jobs_by_customer():

    response = client.get(
        "/jobs?customer_id=1"
    )

    assert response.status_code == 200

    jobs = response.json()

    for job in jobs:

        assert job["customer_id"] == 1


def test_get_jobs_by_customer_and_status():

    response = client.get(
        "/jobs?customer_id=1&status=Hotová"
    )

    assert response.status_code == 200

    jobs = response.json()

    for job in jobs:

        assert job["customer_id"] == 1
        assert job["status"] == "Hotová"


def test_get_jobs_sorted_by_due_date():

    response = client.get("/jobs")

    assert response.status_code == 200

    jobs = response.json()

    dates = [
        job["due_date"]
        for job in jobs
        if job["due_date"] is not None
    ]

    assert dates == sorted(dates)
