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