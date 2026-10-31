"""Čiastočné úhrady faktúr: zostatok, automatické uhradenie, pravidlá, dashboard, upomienky."""

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
from models import AuditLog, Company, Customer, Invoice, InvoicePayment, Job, Quote, QuoteItem


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



def make_invoice(status="Odoslaná", price="100.00", vat="0"):

    customer, job = get_test_customer_and_job()
    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", (date.today() - timedelta(days=10)).isoformat()),
            ("due_date", (date.today() + timedelta(days=4)).isoformat()),
            ("job_id", str(job.id)),
            ("description", "Práca"), ("quantity", "1"), ("unit", "hod"),
            ("unit_price", price), ("vat_rate", vat),
        ],
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = TestingSessionLocal()
    invoice = db.query(Invoice).order_by(Invoice.id.desc()).first()
    invoice.status = status
    db.commit()
    invoice_id = invoice.id
    db.close()

    return invoice_id


def pay(invoice_id, amount, payment_date=None, note=""):

    return post_form(
        f"/invoices/{invoice_id}/payments",
        [("payment_date", (payment_date or date.today()).isoformat()), ("amount", amount), ("note", note)],
        follow_redirects=False,
    )


def load(invoice_id):

    db = TestingSessionLocal()
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    result = (invoice.status, invoice.paid_date, [(p.amount, p.payment_date) for p in invoice.payments])
    db.close()

    return result


def test_partial_payment_keeps_invoice_sent_and_shows_balance():

    from decimal import Decimal

    invoice_id = make_invoice()

    assert pay(invoice_id, "30,50", note="záloha v hotovosti").status_code == 303

    status, paid_date, payments = load(invoice_id)

    assert status == "Odoslaná" and paid_date is None
    assert [amount for amount, _ in payments] == [Decimal("30.50")]

    page = client.get(f"/invoices/{invoice_id}").text

    assert "čiastočne uhradená" in page and "Zostatok: 69.50" in page and "záloha v hotovosti" in page


def test_last_payment_marks_invoice_paid_with_its_date():

    invoice_id = make_invoice()
    first = date.today() - timedelta(days=5)
    last = date.today() - timedelta(days=1)

    assert pay(invoice_id, "40", first).status_code == 303
    assert pay(invoice_id, "60", last).status_code == 303

    status, paid_date, payments = load(invoice_id)

    assert status == "Uhradená" and paid_date == last and len(payments) == 2

    actions = all_log_actions()

    assert actions.count("invoice.payment_added") == 2 and "invoice.status_change" in actions

    # uhradená faktúra už ďalšiu úhradu neprijme
    assert pay(invoice_id, "1").status_code == 422


@pytest.mark.parametrize("amount", ["", "abc", "0", "-1", "100.01", "10.005", "nan", "inf"])
def test_invalid_payment_amounts_are_rejected(amount):

    invoice_id = make_invoice()

    assert pay(invoice_id, amount).status_code == 422
    assert load(invoice_id)[2] == []


def test_payment_dates_are_validated():

    invoice_id = make_invoice()

    assert pay(invoice_id, "10", date.today() + timedelta(days=1)).status_code == 422
    assert pay(invoice_id, "10", date.today() - timedelta(days=11)).status_code == 422
    assert load(invoice_id)[2] == []


@pytest.mark.parametrize("status", ["Návrh", "Stornovaná", "Uhradená"])
def test_payments_only_for_sent_invoices(status):

    invoice_id = make_invoice(status=status)

    assert pay(invoice_id, "10").status_code == 422


def test_no_payments_for_proforma_or_credit_note():

    invoice_id = make_invoice()

    db = TestingSessionLocal()
    db.query(Invoice).filter(Invoice.id == invoice_id).update({"is_proforma": True})
    db.commit()
    db.close()

    assert pay(invoice_id, "10").status_code == 422

    db = TestingSessionLocal()
    db.query(Invoice).filter(Invoice.id == invoice_id).update({"is_proforma": False, "is_credit_note": True})
    db.commit()
    db.close()

    assert pay(invoice_id, "10").status_code == 422


def test_manual_paid_status_adds_missing_balance_as_payment():

    from decimal import Decimal

    invoice_id = make_invoice()
    pay(invoice_id, "25")

    response = post_form(f"/invoices/{invoice_id}/status", [("status", "Uhradená")], follow_redirects=False)

    assert response.status_code == 303

    status, paid_date, payments = load(invoice_id)

    assert status == "Uhradená" and paid_date == date.today()
    assert sum(amount for amount, _ in payments) == Decimal("100.00") and len(payments) == 2


def test_payment_can_be_deleted_only_while_sent():

    invoice_id = make_invoice()
    pay(invoice_id, "40")

    db = TestingSessionLocal()
    payment_id = db.query(InvoicePayment).first().id
    db.close()

    response = post_form(f"/invoices/{invoice_id}/payments/{payment_id}/delete", [], follow_redirects=False)

    assert response.status_code == 303 and load(invoice_id)[2] == []
    assert "invoice.payment_deleted" in all_log_actions()

    pay(invoice_id, "100")   # uhradí celú
    db = TestingSessionLocal()
    payment_id = db.query(InvoicePayment).first().id
    db.close()

    assert post_form(f"/invoices/{invoice_id}/payments/{payment_id}/delete", []).status_code == 409
    assert post_form(f"/invoices/{invoice_id}/payments/99999/delete", []).status_code == 404


def test_unknown_invoice_returns_404():

    assert pay(99999, "10").status_code == 404


def test_dashboard_collection_and_reminders_use_balance():

    from decimal import Decimal
    from invoice_utils import invoice_balance
    from reminder_utils import build_reminder_message, reminder_rows

    invoice_id = make_invoice(price="100.00")
    pay(invoice_id, "70")

    db = TestingSessionLocal()
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    invoice.due_date = date.today() - timedelta(days=3)
    db.commit()
    db.refresh(invoice)

    rows = reminder_rows([invoice], date.today())

    assert invoice_balance(invoice) == Decimal("30.00")
    assert rows["overdue"][0]["total"] == Decimal("30.00") and rows["overdue_total"] == Decimal("30.00")
    assert "30,00" in build_reminder_message(invoice, db.query(Company).first(), date.today())["body"]
    db.close()

    html = client.get("/").text

    assert "30,00" in html or "30.00" in html


def test_payments_are_isolated_in_audit_and_invoice_cannot_be_deleted_with_payments():

    invoice_id = make_invoice()
    pay(invoice_id, "10")

    response = post_form(f"/invoices/{invoice_id}/delete", [], follow_redirects=False)

    assert response.status_code in (403, 409, 422)
    assert load(invoice_id)[2] != []


def test_invoice_list_shows_remaining_balance_for_partial_payment():

    invoice_id = make_invoice(price="100.00")
    pay(invoice_id, "70")

    assert "zostáva 30.00 €" in client.get("/faktury").text


def test_payment_routes_hold_the_process_lock():

    """Úhrady a zmena stavu sa serializujú (inak by dve súčasné úhrady presiahli zostatok)."""

    from routers import invoices as invoices_router

    wanted = {
        ("/invoices/{invoice_id}/payments", "POST"),
        ("/invoices/{invoice_id}/payments/{payment_id}/delete", "POST"),
        ("/invoices/{invoice_id}/status", "POST"),
    }
    found = set()

    for route in invoices_router.router.routes:

        key = (route.path, next(iter(route.methods)))

        if key in wanted:
            assert any(dep.call is invoices_router.payment_lock for dep in route.dependant.dependencies), key
            found.add(key)

    assert found == wanted


def test_concurrent_payments_cannot_exceed_the_invoice_amount():

    from decimal import Decimal

    import threading

    invoice_id = make_invoice(price="100.00")
    results = []

    def send():
        results.append(pay(invoice_id, "60").status_code)

    threads = [threading.Thread(target=send) for _ in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    db = TestingSessionLocal()
    total = sum(payment.amount for payment in db.query(InvoicePayment).filter(InvoicePayment.invoice_id == invoice_id))
    db.close()

    assert total == Decimal("60.00") and results.count(303) == 1
