"""
Audit stopa účtovných zmien: dôvod storna, dátum úhrady, filter a ochrana pred mazaním.

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



def make_invoice(status="Odoslaná"):

    from models import Invoice

    customer, job = get_test_customer_and_job()
    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", (date.today() + timedelta(days=14)).isoformat()),
            ("job_id", str(job.id)),
            ("description", "Práca"), ("quantity", "1"), ("unit", "hod"),
            ("unit_price", "20.00"), ("vat_rate", "23"),
        ],
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = TestingSessionLocal()
    invoice = db.query(Invoice).order_by(Invoice.id.desc()).first()
    invoice.status = status
    db.commit()
    invoice_id, number = invoice.id, invoice.invoice_number
    db.close()

    return invoice_id, number


def test_cancel_requires_reason():

    invoice_id, _ = make_invoice()

    for reason in ("", "  ", "ab"):
        response = post_form(
            f"/invoices/{invoice_id}/status",
            [("status", "Stornovaná"), ("reason", reason)],
        )
        assert response.status_code == 422

    assert "invoice.status_change" not in all_log_actions()


def test_cancel_reason_is_written_to_audit_log():

    invoice_id, number = make_invoice()

    response = post_form(
        f"/invoices/{invoice_id}/status",
        [("status", "Stornovaná"), ("reason", "  Zlá   suma,\n vystavená omylom ")],
        follow_redirects=False,
    )

    assert response.status_code == 303

    entry = latest_log_entry()
    assert entry.action == "invoice.status_change"
    assert number in entry.detail
    assert "dôvod: Zlá suma, vystavená omylom" in entry.detail


def test_non_cancel_status_change_does_not_need_reason():

    invoice_id, _ = make_invoice()

    response = post_form(
        f"/invoices/{invoice_id}/status",
        [("status", "Uhradená")],
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "dôvod" not in latest_log_entry().detail


def test_paid_date_change_is_logged_but_noop_is_not():

    invoice_id, number = make_invoice()

    post_form(f"/invoices/{invoice_id}/paid-date", [("paid_date", "2026-01-15")], follow_redirects=False)

    entry = latest_log_entry()
    assert entry.action == "invoice.paid_date_change"
    assert number in entry.detail and "15.01.2026" in entry.detail

    count = all_log_actions().count("invoice.paid_date_change")
    post_form(f"/invoices/{invoice_id}/paid-date", [("paid_date", "2026-01-15")], follow_redirects=False)
    assert all_log_actions().count("invoice.paid_date_change") == count


def test_audit_page_filters_by_type_and_text():

    invoice_id, number = make_invoice()
    db = TestingSessionLocal()
    db.add(AuditLog(action="company.update", detail="Zákazník Xyz"))
    db.commit()
    db.close()

    page = client.get("/audit-log?typ=invoice").text
    assert number in page
    assert "Zákazník Xyz" not in page

    assert "Zákazník Xyz" in client.get("/audit-log?q=Xyz").text
    assert number not in client.get("/audit-log?q=Xyz").text

    # neplatný typ a strana nesmú zhodiť stránku
    assert client.get("/audit-log?typ=nezmysel&strana=-5").status_code == 200
    assert client.get("/audit-log?strana=999").status_code == 200


def test_audit_page_paginates(monkeypatch):

    import routers.company as company_router

    monkeypatch.setattr(company_router, "AUDIT_LOG_PAGE_SIZE", 2)

    db = TestingSessionLocal()
    for i in range(5):
        db.add(AuditLog(action="invoice.create", detail=f"zaznam-{i}"))
    db.commit()
    db.close()

    first = client.get("/audit-log").text
    assert "zaznam-4" in first and "zaznam-3" in first and "zaznam-2" not in first
    assert "Strana 1 z 3" in first

    last = client.get("/audit-log?strana=3").text
    assert "zaznam-0" in last and "Strana 3 z 3" in last


def test_no_http_route_can_delete_or_edit_audit_log():

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()

        if "audit" in path:
            assert methods <= {"GET", "HEAD"}, (path, methods)


def test_migration_makes_audit_log_append_only(tmp_path):

    import sqlite3

    from alembic import command
    from alembic.config import Config

    db_file = tmp_path / "database.db"

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")

    connection = sqlite3.connect(db_file)
    connection.execute("INSERT INTO audit_log (created_at, action) VALUES ('2026-01-01', 'invoice.create')")
    connection.commit()

    with pytest.raises(sqlite3.DatabaseError, match="len na pridávanie"):
        connection.execute("UPDATE audit_log SET action = 'x'")

    with pytest.raises(sqlite3.DatabaseError, match="len na pridávanie"):
        connection.execute("DELETE FROM audit_log")

    # pridávanie (aj obnova zo zálohy, ktorá len vkladá) funguje
    connection.execute("INSERT INTO audit_log (created_at, action) VALUES ('2026-01-02', 'backup.restore')")
    connection.commit()
    assert connection.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 2
    connection.close()
