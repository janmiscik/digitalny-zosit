"""Pripomienky platieb: výber faktúr, stupne, text správy, evidencia."""

import os
import sys
from datetime import date, timedelta
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
from database import Base, get_db
from main import app
from models import Company, Customer, Invoice, InvoiceItem
from reminder_utils import (
    build_reminder_message,
    mailto_url,
    money,
    reminder_level,
    reminder_rows,
)

test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

TODAY = date.today()


@pytest.fixture(autouse=True)
def setup_test_database():

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestingSessionLocal()
    db.add(Customer(name="Dlžník s.r.o.", email="dlznik@example.com"))
    db.add(Customer(name="Bez Mailu"))
    db.add(Company(id=1, name="Moja Firma", iban="SK3112000000198742637541"))
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


def add_invoice(number, due_in_days, status="Odoslaná", customer=1, price="100",
                proforma=False, credit_note=False):
    """due_in_days záporné = po splatnosti."""

    db = TestingSessionLocal()

    invoice = Invoice(
        invoice_number=number,
        customer_id=customer,
        status=status,
        issue_date=TODAY - timedelta(days=30),
        due_date=TODAY + timedelta(days=due_in_days),
        is_proforma=proforma,
        is_credit_note=credit_note,
    )

    db.add(invoice)
    db.flush()

    db.add(InvoiceItem(
        invoice_id=invoice.id, description="Práca", quantity=Decimal("1"),
        unit="ks", unit_price=Decimal(price), vat_rate=0,
    ))

    db.commit()
    invoice_id = invoice.id
    db.close()

    return invoice_id


def load(invoice_id):

    db = TestingSessionLocal()
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    return db, invoice


# ---------- čistá logika ----------

def test_levels_by_days_overdue():

    assert reminder_level(1)["key"] == "first"
    assert reminder_level(13)["key"] == "first"
    assert reminder_level(14)["key"] == "second"
    assert reminder_level(29)["key"] == "second"
    assert reminder_level(30)["key"] == "final"
    assert reminder_level(0)["key"] == "upcoming"


def test_money_slovak_format():

    assert money(Decimal("1234.5")) == "1 234,50 €"
    assert money(Decimal("50")) == "50,00 €"


def test_rows_only_remindable_invoices_sorted():

    ids = {
        "old": add_invoice("2026001", -40),
        "new": add_invoice("2026002", -3),
        "soon": add_invoice("2026003", 2),
        "later": add_invoice("2026004", 10),
        "draft": add_invoice("2026005", -10, status="Návrh"),
        "paid": add_invoice("2026006", -10, status="Uhradená"),
        "proforma": add_invoice("ZF2026001", -10, proforma=True),
        "credit": add_invoice("2026007", -10, credit_note=True),
    }

    db = TestingSessionLocal()
    rows = reminder_rows(db.query(Invoice).all(), TODAY)
    db.close()

    assert [row["number"] for row in rows["overdue"]] == ["2026001", "2026002"]
    assert [row["number"] for row in rows["upcoming"]] == ["2026003"]
    assert rows["overdue_total"] == Decimal("200")
    assert rows["overdue"][0]["level"]["key"] == "final"


def test_message_tone_changes_with_overdue_days():

    first = add_invoice("2026001", -5)
    second = add_invoice("2026002", -20)
    final = add_invoice("2026003", -45)
    upcoming = add_invoice("2026004", 2)

    texts = {}

    for name, invoice_id in (("first", first), ("second", second), ("final", final), ("upcoming", upcoming)):

        db, invoice = load(invoice_id)
        company = db.query(Company).first()
        texts[name] = build_reminder_message(invoice, company, TODAY)
        db.close()

    assert texts["first"]["subject"].startswith("Pripomienka platby")
    assert texts["second"]["subject"].startswith("Upomienka")
    assert texts["final"]["subject"].startswith("Posledná výzva")
    assert texts["upcoming"]["subject"].startswith("Pripomenutie splatnosti")

    body = texts["first"]["body"]

    assert "2026001" in body
    assert "100,00 €" in body
    assert "SK3112000000198742637541" in body
    assert "Variabilný symbol" in body
    assert "Moja Firma" in body
    assert "do 7 dní" in texts["second"]["body"]


def test_mailto_url_encodes_and_handles_missing_email():

    url = mailto_url("a@b.sk", "Predmet č. 1", "Riadok 1\nRiadok 2 & viac")

    assert url.startswith("mailto:a%40b.sk?subject=")
    assert "%0A" in url
    assert "&body=" in url
    assert "&viac" not in url.split("body=")[1]      # & v texte je zakódovaný
    assert mailto_url(None, "x", "y") is None


# ---------- stránky ----------

def test_reminders_page_lists_overdue_and_upcoming():

    add_invoice("2026001", -20, price="250")
    add_invoice("2026002", 1)
    add_invoice("2026003", 30)

    page = client.get("/pripomienky").text

    assert "2026001" in page
    assert "Druhá upomienka" in page
    assert "250.00 €" in page
    assert "2026002" in page
    assert "zajtra" in page
    assert "2026003" not in page


def test_reminders_page_empty_state():

    page = client.get("/pripomienky").text

    assert "Nič po splatnosti" in page


def test_reminders_page_warns_when_customer_has_no_email():

    add_invoice("2026001", -5, customer=2)

    assert "zákazník nemá e-mail" in client.get("/pripomienky").text


def test_reminder_detail_has_text_and_mailto():

    invoice_id = add_invoice("2026001", -5)

    page = client.get(f"/invoices/{invoice_id}/reminder").text

    assert "2026001" in page
    assert "mailto:dlznik%40example.com" in page
    assert "SK3112000000198742637541" in page


def test_reminder_detail_without_email_has_no_mailto_link():

    invoice_id = add_invoice("2026001", -5, customer=2)

    page = client.get(f"/invoices/{invoice_id}/reminder").text

    assert 'id="mailtoLink"' not in page
    assert "nemá vyplnený e-mail" in page


def test_reminder_refused_for_draft_paid_proforma_and_missing():

    for kwargs in ({"status": "Návrh"}, {"status": "Uhradená"}, {"proforma": True}):

        number = f"X{len(str(kwargs))}{kwargs}"
        invoice_id = add_invoice(number, -5, **kwargs)

        assert client.get(f"/invoices/{invoice_id}/reminder").status_code == 409
        assert client.post(f"/invoices/{invoice_id}/reminder/mark").status_code == 409

    assert client.get("/invoices/9999/reminder").status_code == 404


def test_mark_reminded_counts_dates_and_audits():

    invoice_id = add_invoice("2026001", -5)

    for expected in (1, 2):

        response = client.post(
            f"/invoices/{invoice_id}/reminder/mark", follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/pripomienky")

        db, invoice = load(invoice_id)

        assert invoice.reminder_count == expected
        assert invoice.last_reminded_at == TODAY

        db.close()

    db = TestingSessionLocal()
    actions = [entry.action for entry in db.query(AuditLog).all()]
    db.close()

    assert actions.count("invoice.reminder_sent") == 2

    page = client.get("/pripomienky").text

    assert "Pripomenuté 2×" in page


def test_invoice_detail_shows_reminder_button_only_for_sent():

    sent = add_invoice("2026001", -5)
    paid = add_invoice("2026002", -5, status="Uhradená")

    assert f'href="/invoices/{sent}/reminder"' in client.get(f"/invoices/{sent}").text
    assert f'href="/invoices/{paid}/reminder"' not in client.get(f"/invoices/{paid}").text


def test_dashboard_attention_links_to_reminders():

    add_invoice("2026001", -5)

    assert 'href="/pripomienky"' in client.get("/").text
