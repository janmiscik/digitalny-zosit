"""
Testy dashboardu (/): graf tržieb po mesiacoch, zoznam faktúr po
splatnosti, pás "Vyžaduje pozornosť" a dashboard_utils.py.
"""

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

from auth import require_login_api, require_login_page
from csrf import verify_csrf
from dashboard_utils import (
    first_day_of_month_back,
    monthly_revenue_series,
    overdue_invoice_rows,
)
from database import Base, get_db
from main import app
from models import Customer, Invoice, InvoiceItem


test_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_database():

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestingSessionLocal()
    db.add(Customer(name="Dlžník s.r.o."))
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


def add_invoice(number, status, issue_date, due_date, paid_date=None,
                price="100", credit_note=False):
    """Faktúra s jednou položkou, DPH 0 % -> celkom = price."""

    db = TestingSessionLocal()

    customer = db.query(Customer).first()

    invoice = Invoice(
        invoice_number=number,
        customer_id=customer.id,
        status=status,
        issue_date=issue_date,
        due_date=due_date,
        paid_date=paid_date,
        is_credit_note=credit_note,
    )

    db.add(invoice)
    db.flush()

    db.add(InvoiceItem(
        invoice_id=invoice.id,
        description="Práca",
        quantity=Decimal("1"),
        unit="ks",
        unit_price=Decimal(price),
        vat_rate=0,
    ))

    db.commit()
    db.close()


class FakeInvoice:

    def __init__(self, paid_date, total, number="X"):

        self.paid_date = paid_date
        self.issue_date = paid_date
        self.is_credit_note = False
        self.invoice_number = number
        self.id = 1
        self.due_date = paid_date
        self.customer = None
        self.items = [type("I", (), {
            "quantity": Decimal("1"),
            "unit_price": Decimal(total),
            "vat_rate": Decimal("0"),
        })()]


# =========================================
# dashboard_utils
# =========================================

def test_first_day_of_month_back_crosses_year_boundary():

    assert first_day_of_month_back(date(2026, 2, 15), 0) == date(2026, 2, 1)
    assert first_day_of_month_back(date(2026, 2, 15), 1) == date(2026, 1, 1)
    assert first_day_of_month_back(date(2026, 2, 15), 2) == date(2025, 12, 1)
    assert first_day_of_month_back(date(2026, 1, 31), 13) == date(2024, 12, 1)


def test_monthly_series_has_six_months_oldest_first_and_marks_current():

    series = monthly_revenue_series([], date(2026, 2, 15), months=6)

    assert [(s["year"], s["month"]) for s in series] == [
        (2025, 9), (2025, 10), (2025, 11), (2025, 12), (2026, 1), (2026, 2)
    ]
    assert [s["is_current"] for s in series] == [False] * 5 + [True]
    assert all(s["total"] == 0 and s["percent"] == 0 for s in series)


def test_monthly_series_sums_by_paid_date_and_scales_bars():

    today = date(2026, 10, 17)

    invoices = [
        FakeInvoice(date(2026, 10, 2), "300"),
        FakeInvoice(date(2026, 10, 10), "100"),
        FakeInvoice(date(2026, 9, 5), "200"),
        FakeInvoice(date(2026, 3, 1), "999"),   # mimo 6-mesačného okna
    ]

    series = {(s["year"], s["month"]): s for s in monthly_revenue_series(invoices, today)}

    assert series[(2026, 10)]["total"] == Decimal("400")
    assert series[(2026, 10)]["percent"] == 100
    assert series[(2026, 9)]["total"] == Decimal("200")
    assert series[(2026, 9)]["percent"] == 50
    assert (2026, 3) not in series


def test_monthly_series_ignores_future_dates_and_keeps_tiny_month_visible():

    today = date(2026, 10, 17)

    invoices = [
        FakeInvoice(date(2026, 10, 1), "10000"),
        FakeInvoice(date(2026, 9, 1), "1"),
        FakeInvoice(today + timedelta(days=3), "5000"),
    ]

    series = {(s["year"], s["month"]): s for s in monthly_revenue_series(invoices, today)}

    assert series[(2026, 10)]["total"] == Decimal("10000")
    assert series[(2026, 9)]["percent"] == 4


def test_overdue_rows_sorted_by_days_and_limited():

    today = date(2026, 10, 17)

    invoices = []

    for index in range(7):

        invoice = FakeInvoice(today - timedelta(days=index + 1), "50", number=f"N{index}")
        invoice.due_date = today - timedelta(days=index + 1)
        invoices.append(invoice)

    rows = overdue_invoice_rows(invoices, today, limit=5)

    assert len(rows) == 5
    assert rows[0]["days_overdue"] == 7
    assert [r["days_overdue"] for r in rows] == [7, 6, 5, 4, 3]


# =========================================
# HTTP: stránka /
# =========================================

def test_dashboard_shows_revenue_chart_and_current_month_total():

    today = date.today()

    add_invoice("T1", "Uhradená", today, today, paid_date=today, price="250")

    response = client.get("/")

    assert response.status_code == 200
    assert "Príjmy za posledných 6 mesiacov" in response.text
    assert "250.00 €" in response.text
    assert "podľa dátumu úhrady" in response.text


def test_dashboard_lists_overdue_invoices_with_link_and_days():

    today = date.today()

    add_invoice("2026777", "Odoslaná", today - timedelta(days=40),
                today - timedelta(days=10), price="120")

    response = client.get("/")

    assert response.status_code == 200
    assert "Faktúry po splatnosti" in response.text
    assert "2026777" in response.text
    assert "Dlžník s.r.o." in response.text
    # HTML ignoruje zalomenia riadkov - porovnávame bez nadbytočných bielych znakov
    assert "10 dní po termíne" in " ".join(response.text.split())
    assert "120.00 €" in response.text


def test_dashboard_attention_strip_hidden_when_nothing_is_wrong():

    response = client.get("/")

    assert response.status_code == 200
    assert "Vyžaduje pozornosť" not in response.text
    assert "Faktúry po splatnosti" not in response.text


def test_dashboard_attention_strip_shown_for_overdue_invoice():

    today = date.today()

    add_invoice("2026888", "Odoslaná", today - timedelta(days=30),
                today - timedelta(days=5))

    response = client.get("/")

    assert "Vyžaduje pozornosť" in response.text
    assert "faktúra po splatnosti" in response.text


def test_dashboard_credit_note_reduces_month_revenue():

    today = date.today()

    add_invoice("T2", "Uhradená", today, today, paid_date=today, price="300")
    add_invoice("T3", "Uhradená", today, today, paid_date=today, price="100",
                credit_note=True)

    response = client.get("/")

    assert "200.00 €" in response.text
