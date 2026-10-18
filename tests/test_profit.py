"""Zisk zo zákazky: tržba bez DPH, náklady podľa druhu, marža (profit_utils.py)."""

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
from database import Base, get_db
from main import app
from models import Customer, Invoice, InvoiceItem, Job, JobCost
from profit_utils import job_profit

test_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_database():

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestingSessionLocal()
    db.add(Customer(name="Zákazník"))
    db.flush()
    db.add(Job(title="Oprava strechy", customer_id=1))
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

_counter = {"n": 0}


def add_invoice(status="Odoslaná", price="1000", vat=23, proforma=False, credit_note=False):

    _counter["n"] += 1

    db = TestingSessionLocal()

    invoice = Invoice(
        invoice_number=f"2026{_counter['n']:03d}",
        customer_id=1,
        job_id=1,
        status=status,
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        is_proforma=proforma,
        is_credit_note=credit_note,
    )

    db.add(invoice)
    db.flush()

    db.add(InvoiceItem(
        invoice_id=invoice.id, description="Práca", quantity=Decimal("1"),
        unit="ks", unit_price=Decimal(price), vat_rate=vat,
    ))

    db.commit()
    db.close()


def add_cost(amount, category="material", hours=None):

    db = TestingSessionLocal()

    db.add(JobCost(
        job_id=1, description="Náklad", amount=Decimal(amount),
        cost_date=date.today(), category=category,
        hours=Decimal(hours) if hours else None,
    ))

    db.commit()
    db.close()


def profit():

    db = TestingSessionLocal()
    job = db.query(Job).first()
    result = job_profit(job)
    db.close()

    return result


# ---------- výpočet ----------

def test_empty_job_has_no_data_and_no_margin():

    result = profit()

    assert result["revenue"] == 0
    assert result["profit"] == 0
    assert result["margin"] is None
    assert result["has_data"] is False


def test_revenue_is_without_vat():

    add_invoice(price="1000", vat=23)       # s DPH 1230, základ 1000

    assert profit()["revenue"] == Decimal("1000.00")


def test_only_issued_invoices_count_and_credit_note_reduces():

    add_invoice(status="Uhradená", price="1000")
    add_invoice(status="Odoslaná", price="500")
    add_invoice(status="Návrh", price="9999")
    add_invoice(status="Stornovaná", price="9999")
    add_invoice(status="Odoslaná", price="9999", proforma=True)
    add_invoice(status="Odoslaná", price="200", credit_note=True)

    result = profit()

    assert result["revenue"] == Decimal("1300.00")        # 1000 + 500 - 200
    assert result["revenue_paid"] == Decimal("1000.00")
    assert result["revenue_pending"] == Decimal("300.00")


def test_costs_split_by_category_with_hours():

    add_invoice(price="1000", vat=0)
    add_cost("200", "material")
    add_cost("100", "material")
    add_cost("150", "praca", hours="5")
    add_cost("50", "ine")

    result = profit()

    assert result["costs_by_category"]["material"] == Decimal("300")
    assert result["costs_by_category"]["praca"] == Decimal("150")
    assert result["costs_by_category"]["ine"] == Decimal("50")
    assert result["total_costs"] == Decimal("500")
    assert result["profit"] == Decimal("500.00")
    assert result["margin"] == Decimal("50.0")
    assert result["hours"] == Decimal("5")


def test_loss_gives_negative_profit_and_margin():

    add_invoice(price="100", vat=0)
    add_cost("150")

    result = profit()

    assert result["profit"] == Decimal("-50.00")
    assert result["margin"] == Decimal("-50.0")


def test_costs_without_revenue_have_no_margin():

    add_cost("80")

    result = profit()

    assert result["profit"] == Decimal("-80")
    assert result["margin"] is None
    assert result["has_data"] is True


def test_unknown_category_counts_as_other():

    add_cost("40", category="neznamy")

    assert profit()["costs_by_category"]["ine"] == Decimal("40")


def test_margin_rounded_to_one_decimal():

    add_invoice(price="300", vat=0)
    add_cost("100")

    assert profit()["margin"] == Decimal("66.7")


# ---------- pridávanie nákladov ----------

def costs():

    db = TestingSessionLocal()
    rows = db.query(JobCost).all()
    result = [(c.category, c.amount, c.hours, c.description) for c in rows]
    db.close()

    return result


def test_add_material_cost():

    response = client.post(
        "/jobs/1/costs",
        data={"category": "material", "amount": "120.50", "description": "Rúry"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert costs() == [("material", Decimal("120.50"), None, "Rúry")]


def test_add_work_cost_computes_amount_from_hours_and_rate():

    client.post(
        "/jobs/1/costs",
        data={"category": "praca", "hours": "7.5", "hourly_rate": "20"},
        follow_redirects=False,
    )

    (category, amount, hours, description), = costs()

    assert category == "praca"
    assert amount == Decimal("150.00")
    assert hours == Decimal("7.5")
    assert description == "Práca"          # predvolený popis


def test_work_cost_requires_hours_and_rate():

    assert client.post("/jobs/1/costs", data={"category": "praca", "hours": "5"}).status_code == 422
    assert client.post("/jobs/1/costs", data={"category": "praca", "hourly_rate": "20"}).status_code == 422
    assert costs() == []


def test_cost_validation():

    assert client.post("/jobs/1/costs", data={"category": "material"}).status_code == 422
    assert client.post("/jobs/1/costs", data={"category": "zly", "amount": "5"}).status_code == 422
    assert client.post("/jobs/1/costs", data={"category": "material", "amount": "-5"}).status_code == 422
    assert client.post("/jobs/1/costs", data={"category": "material", "amount": "abc"}).status_code == 422
    assert client.post("/jobs/99/costs", data={"category": "material", "amount": "5"}).status_code == 404
    assert costs() == []


def test_cost_without_category_defaults_to_material():

    client.post("/jobs/1/costs", data={"amount": "10", "description": "x"}, follow_redirects=False)

    assert costs()[0][0] == "material"


# ---------- stránky ----------

def test_edit_job_page_shows_profit_breakdown():

    add_invoice(status="Uhradená", price="1000", vat=23)
    add_invoice(status="Odoslaná", price="500", vat=0)
    add_cost("300", "material")
    add_cost("150", "praca", hours="5")

    page = client.get("/jobs/1/edit").text

    assert "Tržba (bez DPH)" in page
    assert "1500.00 €" in page          # 1000 + 500 bez DPH
    assert "450.00 €" in page           # náklady
    assert "1050.00 €" in page          # zisk
    assert "70.0 %" in page             # marža
    assert "500.00 €" in page and "neuhradené" in page
    assert "(5.00 h)" in page or "(5 h)" in page


def test_edit_job_page_without_data_renders():

    page = client.get("/jobs/1/edit").text

    assert "Náklady a zisk" in page
    assert 'id="costCategory"' in page


def test_jobs_list_shows_profit_only_when_there_is_data():

    assert "Zisk " not in client.get("/zakazky").text

    add_invoice(price="1000", vat=0)
    add_cost("250")

    page = client.get("/zakazky").text

    assert "Zisk 750.00 €" in page
    assert "marža 75.0 %" in page
