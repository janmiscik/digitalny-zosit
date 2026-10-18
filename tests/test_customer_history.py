"""História zákazníka: súhrn, poznámky, časová os, fotky (customer_history.py)."""

import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audit_log import AuditLog
from auth import require_login_api, require_login_page
from csrf import verify_csrf
from customer_history import (
    MAX_NOTE_LENGTH,
    build_timeline,
    customer_photos,
    customer_summary,
    format_note_time,
)
from database import Base, get_db
from main import app
from models import Customer, CustomerNote, Invoice, InvoiceItem, Job, JobCost, JobPhoto, Quote

test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

TODAY = date.today()
_counter = {"n": 0}


@pytest.fixture(autouse=True)
def setup_test_database():

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestingSessionLocal()
    db.add(Customer(name="Prvý zákazník"))
    db.add(Customer(name="Druhý zákazník"))
    db.add(Job(title="Oprava strechy", customer_id=1, status="Prebieha"))
    db.add(Job(title="Hotová práca", customer_id=1, status="Hotová"))
    db.commit()
    db.close()

    def override_get_db():

        db = TestingSessionLocal()

        try:
            yield db

        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_login_page] = lambda: "testuser"
    app.dependency_overrides[require_login_api] = lambda: "testuser"
    app.dependency_overrides[verify_csrf] = lambda: None

    yield

    app.dependency_overrides.clear()


client = TestClient(app)


def add_invoice(status="Odoslaná", price="1000", issue_days_ago=10, paid_days_ago=None,
                job_id=None, proforma=False, credit_note=False, customer_id=1):

    _counter["n"] += 1

    db = TestingSessionLocal()

    invoice = Invoice(
        invoice_number=f"2026{_counter['n']:03d}",
        customer_id=customer_id,
        job_id=job_id,
        status=status,
        issue_date=TODAY - timedelta(days=issue_days_ago),
        due_date=TODAY + timedelta(days=14),
        paid_date=TODAY - timedelta(days=paid_days_ago) if paid_days_ago is not None else None,
        is_proforma=proforma,
        is_credit_note=credit_note,
    )

    db.add(invoice)
    db.flush()

    db.add(InvoiceItem(
        invoice_id=invoice.id, description="Práca", quantity=Decimal("1"),
        unit="ks", unit_price=Decimal(price), vat_rate=23,
    ))

    db.commit()
    db.close()


def load_customer(customer_id=1):

    db = TestingSessionLocal()

    return db, db.query(Customer).filter(Customer.id == customer_id).first()


# ---------- súhrn ----------

def test_summary_for_new_customer():

    db, customer = load_customer(2)
    summary = customer_summary(customer)
    db.close()

    assert summary["jobs_total"] == 0
    assert summary["revenue"] == 0
    assert summary["average_payment_days"] is None
    assert summary["has_job_profit"] is False


def test_summary_revenue_without_vat_and_job_counts():

    add_invoice(status="Uhradená", price="1000", issue_days_ago=20, paid_days_ago=10)
    add_invoice(status="Odoslaná", price="500")
    add_invoice(status="Návrh", price="9999")
    add_invoice(status="Odoslaná", price="9999", proforma=True)
    add_invoice(status="Odoslaná", price="100", credit_note=True)

    db, customer = load_customer()
    summary = customer_summary(customer)
    db.close()

    assert summary["jobs_total"] == 2
    assert summary["jobs_open"] == 1
    assert summary["revenue"] == Decimal("1400.00")      # 1000 + 500 - 100, bez DPH


def test_summary_average_payment_days():

    add_invoice(status="Uhradená", issue_days_ago=30, paid_days_ago=20)      # 10 dní
    add_invoice(status="Uhradená", issue_days_ago=40, paid_days_ago=10)      # 30 dní

    db, customer = load_customer()
    summary = customer_summary(customer)
    db.close()

    assert summary["average_payment_days"] == 20


def test_summary_job_profit():

    add_invoice(price="1000", job_id=1)

    db = TestingSessionLocal()
    db.add(JobCost(job_id=1, description="Materiál", amount=Decimal("400"),
                   cost_date=TODAY, category="material"))
    db.commit()
    db.close()

    db, customer = load_customer()
    summary = customer_summary(customer)
    db.close()

    assert summary["has_job_profit"] is True
    assert summary["jobs_profit"] == Decimal("600.00")
    assert summary["jobs_margin"] == Decimal("60.0")


# ---------- fotky a časová os ----------

def add_photos(job_id, count, photo_type="pred", days_ago=1):

    db = TestingSessionLocal()

    for index in range(count):

        db.add(JobPhoto(
            job_id=job_id, filename=f"f{job_id}{photo_type}{days_ago}{index}.jpg",
            photo_type=photo_type, uploaded_at=TODAY - timedelta(days=days_ago),
        ))

    db.commit()
    db.close()


def test_customer_photos_collects_all_jobs_newest_first():

    add_photos(1, 2, days_ago=5)
    add_photos(2, 1, photo_type="po", days_ago=1)

    db, customer = load_customer()
    photos = customer_photos(customer)
    db.close()

    assert len(photos) == 3
    assert photos[0]["job_title"] == "Hotová práca"
    assert photos[0]["type"] == "po"


def test_timeline_events_order_and_exclusions():

    add_invoice(status="Uhradená", issue_days_ago=30, paid_days_ago=5)
    add_invoice(status="Návrh", issue_days_ago=1)
    add_photos(1, 3, days_ago=2)

    db = TestingSessionLocal()
    db.add(Quote(quote_number="CP2026001", customer_id=1, status="Odoslaná",
                 issue_date=TODAY - timedelta(days=40)))
    db.add(CustomerNote(customer_id=1, text="Volal kvôli termínu"))
    db.commit()
    db.close()

    db, customer = load_customer()
    events = build_timeline(customer)
    db.close()

    texts = [event["text"] for event in events]

    assert texts[0].startswith("Poznámka")                       # dnes
    assert any("uhradená" in text for text in texts)
    assert any("3 fotky (pred) k zákazke Oprava strechy" in text for text in texts)
    assert any("Ponuka č. CP2026001" in text for text in texts)
    assert not any("2026002" in text for text in texts)           # návrh preskočený

    dates = [event["date"] for event in events]

    assert dates == sorted(dates, reverse=True)


def test_timeline_is_limited():

    for _ in range(30):
        add_invoice()

    db, customer = load_customer()

    assert len(build_timeline(customer, limit=20)) == 20

    db.close()


# ---------- poznámky ----------

def notes(customer_id=1):

    db = TestingSessionLocal()
    result = [n.text for n in db.query(CustomerNote).filter(CustomerNote.customer_id == customer_id).all()]
    db.close()

    return result


def test_add_note_and_audit():

    response = client.post(
        "/customers/1/notes", data={"text": "  Kľúče u suseda  "}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/customers/1#poznamky"
    assert notes() == ["Kľúče u suseda"]

    db = TestingSessionLocal()
    actions = [entry.action for entry in db.query(AuditLog).all()]
    db.close()

    assert "customer.note_add" in actions


def test_note_validation():

    assert client.post("/customers/1/notes", data={"text": "   "}).status_code == 422
    assert client.post("/customers/1/notes", data={"text": "x" * (MAX_NOTE_LENGTH + 1)}).status_code == 422
    assert client.post("/customers/99/notes", data={"text": "ahoj"}).status_code == 404
    assert notes() == []


def test_delete_note_only_for_own_customer():

    client.post("/customers/1/notes", data={"text": "moja"})

    db = TestingSessionLocal()
    note_id = db.query(CustomerNote).first().id
    db.close()

    assert client.post(f"/customers/2/notes/{note_id}/delete").status_code == 404
    assert notes() == ["moja"]

    response = client.post(f"/customers/1/notes/{note_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert notes() == []
    assert client.post(f"/customers/1/notes/{note_id}/delete").status_code == 404


def test_notes_do_not_leak_between_customers():

    client.post("/customers/1/notes", data={"text": "len pre prvého"})

    assert "len pre prvého" in client.get("/customers/1").text
    assert "len pre prvého" not in client.get("/customers/2").text


def test_note_time_is_converted_to_local_time():

    from datetime import timezone

    # 10:00 UTC v zime = 11:00 v Bratislave
    assert format_note_time(datetime(2026, 1, 15, 10, 0)) == "15.01.2026 11:00"


# ---------- stránka ----------

def test_customer_page_renders_for_empty_customer():

    page = client.get("/customers/2").text

    assert "Poznámky" in page
    assert "Časová os" not in page
    assert "Fotky zo zákaziek" not in page


def test_customer_page_shows_everything_in_one_place():

    add_invoice(status="Uhradená", price="1000", issue_days_ago=20, paid_days_ago=10, job_id=1)
    add_photos(1, 2)
    client.post("/customers/1/notes", data={"text": "Preferuje SMS"})

    page = client.get("/customers/1").text

    assert "1000.00 €" in page                   # tržba bez DPH
    assert "priemerná doba platby" in page
    assert "10 dní" in page
    assert "Preferuje SMS" in page
    assert "Časová os" in page
    assert "Fotky zo zákaziek (2)" in page
    assert "/jobs/photos/f1pred10.jpg" in page
    assert "Oprava strechy" in page


def test_note_text_is_escaped():

    client.post("/customers/1/notes", data={"text": "<script>alert(1)</script>"})

    page = client.get("/customers/1").text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
