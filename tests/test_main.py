import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)


from main import app


client = TestClient(app)


def test_dashboard():

    response = client.get("/")

    assert response.status_code == 200

def test_get_customers():

    response = client.get("/customers")

    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_get_jobs():

    response = client.get("/jobs")

    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_customer_detail():

    response = client.get("/customers/1")

    assert response.status_code == 200

def test_customer_not_found():

    response = client.get("/customers/999999")

    assert response.status_code == 200
    assert response.json() == {
        "error": "Zákazník neexistuje"
    }

def test_create_customer():

    response = client.post(
        "/customers",
        data={
            "name": "Testovací zákazník",
            "phone": "0900123456",
            "email": "test@example.com",
            "address": "Testovacia 1",
            "note": "Test"
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"

def test_create_job():

    response = client.post(
        "/jobs",
        data={
            "title": "Testovacia zákazka",
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

    assert response.status_code == 200
    assert response.json() == {
        "error": "Zákazka neexistuje"
    }

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

    assert response.status_code == 200
    assert response.json() == {
        "error": "Zákazník neexistuje"
    }

def test_edit_job_not_found():

    response = client.get("/jobs/999999/edit")

    assert response.status_code == 200
    assert response.json() == {
        "error": "Zákazka neexistuje"
    }

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