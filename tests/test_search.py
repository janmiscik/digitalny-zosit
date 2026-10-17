"""
Testy vyhľadávania a filtrov (search_utils.py, /faktury, /zakazky,
/customers API, /zakaznici): bez ohľadu na diakritiku a veľkosť písmen,
filter podľa zákazníka, správne zakódované odkazy filtrov.
"""

import os
import sys
from datetime import date
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
from models import Customer, Invoice, InvoiceItem, Job
from search_utils import fold, matches_search


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

    simon = Customer(name="Šimon Ďurčo", ico="12345008", phone="0900111222")
    novak = Customer(name="Stavebniny Novák s.r.o.", ico="12345016")

    db.add_all([simon, novak])
    db.commit()

    def add_invoice(number, customer, description, note=None):

        invoice = Invoice(
            invoice_number=number,
            customer_id=customer.id,
            status="Odoslaná",
            issue_date=date(2026, 5, 1),
            due_date=date(2026, 5, 15),
            note=note,
        )

        db.add(invoice)
        db.flush()

        db.add(InvoiceItem(
            invoice_id=invoice.id,
            description=description,
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("100"),
            vat_rate=0,
        ))

    add_invoice("2026001", simon, "Montáž plynového kotla", note="Záloha uhradená")
    add_invoice("2026002", novak, "Oprava strechy")

    db.add(Job(title="Výmena kotla", description="Žilina, 2. poschodie",
               status="Nová", customer_id=simon.id))
    db.add(Job(title="Oprava odkvapov", status="Prebieha", customer_id=novak.id))

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


def ids(response):
    """Čísla faktúr / názvy zákaziek viditeľné v odpovedi - stačí hľadať text."""
    return response.text


# =========================================
# search_utils
# =========================================

def test_fold_removes_diacritics_and_case():

    assert fold("Šimon Ďurčo") == "simon durco"
    assert fold("ŽILINA") == "zilina"
    assert fold(None) == ""


def test_matches_search_all_words_any_order_any_field():

    assert matches_search("novak kotol", "Stavebniny Novák", "Oprava kotla") is False
    assert matches_search("novak kotl", "Stavebniny Novák", "Oprava kotla") is True
    assert matches_search("kotla novak", "Stavebniny Novák", "Oprava kotla") is True
    assert matches_search("", "cokolvek") is True
    assert matches_search("   ", None) is True
    assert matches_search("x", None, "") is False


# =========================================
# FAKTÚRY
# =========================================

def test_invoice_search_ignores_diacritics_and_case():

    for term in ("simon", "ŠIMON", "durco", "Ďurčo"):

        text = client.get("/faktury", params={"q": term}).text

        assert "2026001" in text, term
        assert "2026002" not in text, term


def test_invoice_search_by_ico_item_description_and_note():

    assert "2026002" in client.get("/faktury", params={"q": "12345016"}).text
    assert "2026001" in client.get("/faktury", params={"q": "plynoveho kotla"}).text \
        or "2026001" in client.get("/faktury", params={"q": "plynoveho"}).text
    assert "2026001" in client.get("/faktury", params={"q": "zaloha"}).text


def test_invoice_search_multiple_words_must_all_match():

    text = client.get("/faktury", params={"q": "simon strecha"}).text

    assert "2026001" not in text
    assert "2026002" not in text


def test_invoice_search_no_result_message():

    text = client.get("/faktury", params={"q": "neexistuje"}).text

    assert "Nič sa nenašlo" in text


def test_invoice_filter_by_customer_id():

    db = TestingSessionLocal()
    novak_id = db.query(Customer).filter(Customer.ico == "12345016").one().id
    db.close()

    text = client.get("/faktury", params={"customer_id": novak_id}).text

    assert "2026002" in text
    assert "2026001" not in text
    assert "Stavebniny Novák s.r.o." in text


def test_invoice_status_chip_links_are_url_encoded_and_keep_filters():

    response = client.get("/faktury", params={"q": "a & b #1", "date_from": "2026-01-01"})

    assert response.status_code == 200
    assert "q=a+%26+b+%231" in response.text
    assert "date_from=2026-01-01" in response.text
    # surový, nezakódovaný text sa v odkaze nesmie objaviť
    assert "q=a & b" not in response.text


def test_invoice_wildcard_characters_are_treated_literally():

    # '%' a '_' nesmú fungovať ako zástupné znaky - nič také v dátach nie je
    assert "2026001" not in client.get("/faktury", params={"q": "%"}).text
    assert "2026001" not in client.get("/faktury", params={"q": "_"}).text


# =========================================
# ZÁKAZKY
# =========================================

def test_job_search_by_title_with_and_without_diacritics():

    for term in ("vymena", "VÝMENA", "kotla"):

        text = client.get("/zakazky", params={"q": term}).text

        assert "Výmena kotla" in text, term
        assert "Oprava odkvapov" not in text, term


def test_job_search_by_description_and_customer_name():

    assert "Výmena kotla" in client.get("/zakazky", params={"q": "zilina"}).text
    assert "Oprava odkvapov" in client.get("/zakazky", params={"q": "novak"}).text
    assert "Oprava odkvapov" in client.get("/zakazky", params={"q": "12345016"}).text


def test_job_search_combines_with_status_filter():

    text = client.get("/zakazky", params={"q": "oprava", "status": "Nová"}).text

    assert "Oprava odkvapov" not in text
    assert "Výmena kotla" not in text


def test_job_filter_by_customer_id():

    db = TestingSessionLocal()
    simon_id = db.query(Customer).filter(Customer.ico == "12345008").one().id
    db.close()

    text = client.get("/zakazky", params={"customer_id": simon_id}).text

    assert "Výmena kotla" in text
    assert "Oprava odkvapov" not in text


def test_job_chip_links_keep_search_and_are_encoded():

    text = client.get("/zakazky", params={"q": "oprava & co", "when": "overdue"}).text

    assert "q=oprava+%26+co" in text
    assert "when=overdue" in text


def test_job_no_result_message():

    assert "Nič sa nenašlo" in client.get("/zakazky", params={"q": "xyz"}).text


# =========================================
# ZÁKAZNÍCI
# =========================================

def test_customers_api_search_ignores_diacritics():

    names = [c["name"] for c in client.get("/customers", params={"search": "simon"}).json()]

    assert names == ["Šimon Ďurčo"]


def test_customers_api_search_by_ico():

    names = [c["name"] for c in client.get("/customers", params={"search": "12345016"}).json()]

    assert names == ["Stavebniny Novák s.r.o."]


def test_customers_page_exposes_searchable_fields_for_client_filter():

    text = client.get("/zakaznici").text

    assert 'data-customer-ico="12345008"' in text
    assert "foldText" in text
