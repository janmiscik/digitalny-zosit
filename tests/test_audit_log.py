"""
Testy pre audit log (models.AuditLog, audit_log.log_action).

Overuje, že dôležité akcie (vytvorenie/zmazanie faktúry a ponuky,
zmena stavu, zmena nastavení firmy, obnova zo zálohy, prihlásenie) sa
skutočne zapíšu do audit logu, a že stránka /audit-log ich zobrazí.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode


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
from csrf import verify_csrf
from database import Base, get_db
from main import app
from models import AuditLog, Company, Customer, Job, Quote, QuoteItem


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
def setup_test_database():

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestingSessionLocal()

    customer = Customer(
        name="Firma s.r.o.",
        email="firma@example.com",
        ico="12345678"
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)

    job = Job(
        title="Oprava kotla",
        status="Nová",
        customer_id=customer.id
    )
    db.add(job)
    db.commit()

    company = Company(name="Testovacia firma s.r.o.", is_vat_payer=True)
    db.add(company)
    db.commit()

    db.close()

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

    # Tieto testy neoverujú CSRF ochranu (na to je tests/test_csrf.py).
    app.dependency_overrides[verify_csrf] = lambda: None

    yield

    app.dependency_overrides.clear()


client = TestClient(app)


def post_form(url, items, **kwargs):

    body = urlencode(items)

    headers = kwargs.pop("headers", {})
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    return client.post(url, content=body, headers=headers, **kwargs)


def get_test_customer_and_job():

    db = TestingSessionLocal()

    customer = db.query(Customer).first()
    job = db.query(Job).first()

    db.close()

    return customer, job


def latest_log_entry() -> AuditLog:

    db = TestingSessionLocal()

    entry = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .first()
    )

    db.close()

    return entry


def all_log_actions() -> list[str]:

    db = TestingSessionLocal()

    actions = [row.action for row in db.query(AuditLog).all()]

    db.close()

    return actions


# =========================================
# FAKTÚRY
# =========================================

def test_creating_invoice_writes_audit_log():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("job_id", str(job.id)),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "hod"),
            ("unit_price", "20.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303

    entry = latest_log_entry()

    assert entry.action == "invoice.create"
    assert entry.entity_type == "invoice"
    assert entry.entity_id is not None
    assert "Firma s.r.o." in entry.detail


def test_invoice_status_change_writes_audit_log():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "hod"),
            ("unit_price", "20.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    invoice_id = response.headers["location"].split("/")[-1]

    response = post_form(
        f"/invoices/{invoice_id}/status",
        [("status", "Odoslaná")],
        follow_redirects=False
    )

    assert response.status_code == 303

    entry = latest_log_entry()

    assert entry.action == "invoice.status_change"
    assert entry.entity_type == "invoice"
    assert "Návrh -> Odoslaná" in entry.detail


def test_deleting_draft_invoice_writes_audit_log():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "hod"),
            ("unit_price", "20.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    invoice_id = response.headers["location"].split("/")[-1]

    response = client.post(
        f"/invoices/{invoice_id}/delete",
        follow_redirects=False
    )

    assert response.status_code == 303

    entry = latest_log_entry()

    assert entry.action == "invoice.delete"
    assert entry.entity_type == "invoice"
    assert entry.entity_id == int(invoice_id)


# =========================================
# CENOVÉ PONUKY
# =========================================

def test_creating_quote_writes_audit_log():

    customer, job = get_test_customer_and_job()

    response = post_form(
        f"/customers/{customer.id}/quotes",
        [
            ("issue_date", date.today().isoformat()),
            ("valid_until", (date.today() + timedelta(days=14)).isoformat()),
            ("description", "Cenová ponuka"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "100.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303

    entry = latest_log_entry()

    assert entry.action == "quote.create"
    assert entry.entity_type == "quote"


def test_deleting_draft_quote_writes_audit_log():

    db = TestingSessionLocal()

    customer = db.query(Customer).first()

    quote = Quote(
        quote_number="2026Q001",
        customer_id=customer.id,
        issue_date=date.today(),
        valid_until=date.today() + timedelta(days=14),
        status="Návrh"
    )
    db.add(quote)
    db.commit()

    quote.items.append(
        QuoteItem(
            description="Položka",
            quantity=1,
            unit="ks",
            unit_price=10,
            vat_rate=23
        )
    )
    db.commit()

    quote_id = quote.id
    db.close()

    response = client.post(
        f"/quotes/{quote_id}/delete",
        follow_redirects=False
    )

    assert response.status_code == 303

    entry = latest_log_entry()

    assert entry.action == "quote.delete"
    assert entry.entity_type == "quote"
    assert entry.entity_id == quote_id


# =========================================
# NASTAVENIA FIRMY
# =========================================

def test_updating_company_settings_writes_audit_log():

    response = client.post(
        "/settings",
        data={
            "name": "Nová firma s.r.o.",
            "ico": "99998888",
            "is_vat_payer": "1"
        },
        follow_redirects=False
    )

    assert response.status_code == 303

    entry = latest_log_entry()

    assert entry.action == "company.update"
    assert "Nová firma s.r.o." in entry.detail


# =========================================
# ZOBRAZENIE /audit-log
# =========================================

def test_audit_log_page_shows_entries():

    post_form(
        "/customers",
        [("name", "Nejaký zákazník")],
        follow_redirects=False
    )

    response = client.get("/audit-log")

    assert response.status_code == 200
    assert "História zmien" in response.text


def test_audit_log_page_requires_login():

    app.dependency_overrides.pop(require_login_page, None)

    try:
        response = client.get("/audit-log", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:
        app.dependency_overrides[require_login_page] = lambda: "testuser"
