"""
Testy pre cenové ponuky - Fáza 3.

Pokrýva: CRUD ponúk, stavové prechody, jedným klikom generovanie
faktúry (ostrej aj zálohovej), vylúčenie proforma faktúr z tržieb,
PDF ponuky aj dodacieho listu.
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
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
from database import Base, get_db
from invoice_utils import (
    allowed_next_quote_statuses,
    is_quote_expired,
    is_valid_quote_status_transition,
    next_proforma_number,
    next_quote_number,
)
from main import app
from models import Company, Customer, Invoice, Job, Quote, QuoteItem


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
        ico="12345678",
        dic="1234567890",
        ic_dph="SK1234567890"
    )

    db.add(customer)
    db.commit()

    company = Company(
        name="Testovacia firma s.r.o.",
        ico="99998888",
        is_vat_payer=True
    )

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

    yield

    app.dependency_overrides.clear()


client = TestClient(app)


def post_form(url, items, **kwargs):

    body = urlencode(items)

    headers = kwargs.pop("headers", {})
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    return client.post(
        url,
        content=body,
        headers=headers,
        **kwargs
    )


def get_test_customer():

    db = TestingSessionLocal()
    customer = db.query(Customer).first()
    db.close()

    return customer


def create_sample_quote(db, status="Návrh"):

    customer = db.query(Customer).first()

    quote = Quote(
        quote_number="CP2026099",
        customer_id=customer.id,
        status=status,
        issue_date=date.today(),
        valid_until=date.today() + timedelta(days=30)
    )

    quote.items.append(
        QuoteItem(
            description="Stavebné práce",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("500.00"),
            vat_rate=23
        )
    )

    db.add(quote)
    db.commit()
    db.refresh(quote)

    return quote


def set_quote_status(quote_id: int, status: str) -> None:

    db = TestingSessionLocal()
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    quote.status = status
    db.commit()
    db.close()


def get_quote(quote_id: int) -> Quote:

    db = TestingSessionLocal()
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    db.expunge(quote)
    db.close()

    return quote


# =========================================
# JEDNOTKOVÉ TESTY - invoice_utils
# =========================================

def test_next_quote_number_format():

    db = TestingSessionLocal()
    number = next_quote_number(db, 2026)
    db.close()

    assert number == "CP2026001"


def test_next_quote_number_increments():

    db = TestingSessionLocal()
    create_sample_quote(db)
    number = next_quote_number(db, 2026)
    db.close()

    assert number == "CP2026100"


def test_next_proforma_number_format():

    db = TestingSessionLocal()
    number = next_proforma_number(db, 2026)
    db.close()

    assert number == "ZF2026001"


def test_proforma_and_invoice_numbers_are_independent():
    """Zálohová faktúra má vlastný číselný rad - nesmie kolidovať ani
    ovplyvňovať číslovanie ostrých faktúr."""

    db = TestingSessionLocal()

    invoice = Invoice(
        invoice_number="ZF2026005",
        customer_id=get_test_customer().id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        is_proforma=True
    )
    db.add(invoice)
    db.commit()

    from invoice_utils import next_invoice_number

    regular_number = next_invoice_number(db, 2026)

    db.close()

    assert regular_number == "2026001"


def test_is_quote_expired_true_when_past_valid_until():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote.valid_until = date.today() - timedelta(days=1)
    db.commit()
    db.refresh(quote)

    assert is_quote_expired(quote) is True

    db.close()


def test_is_quote_expired_false_without_valid_until():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote.valid_until = None
    db.commit()
    db.refresh(quote)

    assert is_quote_expired(quote) is False

    db.close()


def test_is_quote_expired_false_when_accepted():
    """Akceptovaná ponuka sa nepovažuje za 'po platnosti', aj keby mala
    starý dátum platnosti - je už uzavretá."""

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote.valid_until = date.today() - timedelta(days=1)
    quote.status = "Akceptovaná"
    db.commit()
    db.refresh(quote)

    assert is_quote_expired(quote) is False

    db.close()


@pytest.mark.parametrize(
    "current_status,new_status,expected",
    [
        ("Návrh", "Odoslaná", True),
        ("Návrh", "Akceptovaná", True),
        ("Návrh", "Zamietnutá", True),
        ("Odoslaná", "Akceptovaná", True),
        ("Odoslaná", "Zamietnutá", True),
        ("Akceptovaná", "Prevedená na faktúru", True),
        ("Odoslaná", "Návrh", False),
        ("Zamietnutá", "Odoslaná", False),
        ("Prevedená na faktúru", "Návrh", False),
    ]
)
def test_quote_status_transitions(current_status, new_status, expected):

    assert is_valid_quote_status_transition(current_status, new_status) == expected


# =========================================
# CRUD PONÚK
# =========================================

def test_create_quote_success():

    customer = get_test_customer()

    response = post_form(
        f"/customers/{customer.id}/quotes",
        [
            ("issue_date", date.today().isoformat()),
            ("valid_until", (date.today() + timedelta(days=30)).isoformat()),
            ("description", "Maliarske práce"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "300.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/quotes/1"

    db = TestingSessionLocal()
    quote = db.query(Quote).first()
    db.close()

    assert quote.quote_number == "CP2026001"
    assert quote.status == "Návrh"


def test_create_quote_no_items_fails():

    customer = get_test_customer()

    response = post_form(
        f"/customers/{customer.id}/quotes",
        [
            ("issue_date", date.today().isoformat()),
        ]
    )

    assert response.status_code == 422


def test_create_quote_customer_not_found():

    response = post_form(
        "/customers/999999/quotes",
        [
            ("issue_date", date.today().isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 404


def test_create_quote_valid_until_before_issue_date_rejected():

    customer = get_test_customer()

    issue_date = date.today()
    valid_until = issue_date - timedelta(days=1)

    response = post_form(
        f"/customers/{customer.id}/quotes",
        [
            ("issue_date", issue_date.isoformat()),
            ("valid_until", valid_until.isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_quote_detail_page():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote_id = quote.id
    db.close()

    response = client.get(f"/quotes/{quote_id}")

    assert response.status_code == 200
    assert "CP2026099" in response.text


def test_quote_detail_not_found():

    response = client.get("/quotes/999999")

    assert response.status_code == 404


def test_update_quote_success():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote_id = quote.id
    db.close()

    response = post_form(
        f"/quotes/{quote_id}/edit",
        [
            ("issue_date", date.today().isoformat()),
            ("description", "Upravená položka"),
            ("quantity", "2"),
            ("unit", "ks"),
            ("unit_price", "250.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    item_count = len(quote.items)
    item_description = quote.items[0].description
    db.close()

    assert item_count == 1
    assert item_description == "Upravená položka"


def test_update_non_draft_quote_forbidden():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Odoslaná")
    quote_id = quote.id
    db.close()

    response = post_form(
        f"/quotes/{quote_id}/edit",
        [
            ("issue_date", date.today().isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 409


def test_delete_draft_quote():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote_id = quote.id
    db.close()

    response = client.post(
        f"/quotes/{quote_id}/delete",
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    assert db.query(Quote).filter(Quote.id == quote_id).first() is None
    db.close()


def test_delete_non_draft_quote_forbidden():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Odoslaná")
    quote_id = quote.id
    db.close()

    response = client.post(f"/quotes/{quote_id}/delete")

    assert response.status_code == 409


# =========================================
# STAVOVÉ PRECHODY (endpoint)
# =========================================

def test_update_quote_status_success():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote_id = quote.id
    db.close()

    response = post_form(
        f"/quotes/{quote_id}/status",
        [("status", "Odoslaná")],
        follow_redirects=False
    )

    assert response.status_code == 303
    assert get_quote(quote_id).status == "Odoslaná"


def test_update_quote_status_invalid_transition_rejected():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Zamietnutá")
    quote_id = quote.id
    db.close()

    response = post_form(
        f"/quotes/{quote_id}/status",
        [("status", "Odoslaná")]
    )

    assert response.status_code == 409


def test_quote_status_cannot_be_set_to_converted_manually():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Akceptovaná")
    quote_id = quote.id
    db.close()

    response = post_form(
        f"/quotes/{quote_id}/status",
        [("status", "Prevedená na faktúru")]
    )

    assert response.status_code == 422
    assert get_quote(quote_id).status == "Akceptovaná"


def test_quote_status_cannot_be_set_to_expired_manually():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote_id = quote.id
    db.close()

    response = post_form(
        f"/quotes/{quote_id}/status",
        [("status", "Po platnosti")]
    )

    assert response.status_code == 422


# =========================================
# JEDNÝM KLIKOM: PONUKA -> FAKTÚRA
# =========================================

def test_convert_quote_requires_accepted_status():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Návrh")
    quote_id = quote.id
    db.close()

    response = client.post(f"/quotes/{quote_id}/convert-to-invoice")

    assert response.status_code == 409


def test_convert_accepted_quote_to_regular_invoice():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Akceptovaná")
    quote_id = quote.id
    db.close()

    response = client.post(
        f"/quotes/{quote_id}/convert-to-invoice",
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/invoices/1"

    db = TestingSessionLocal()

    invoice = db.query(Invoice).filter(Invoice.id == 1).first()
    assert invoice.is_proforma is False
    assert invoice.invoice_number == "2026001"
    assert invoice.quote_id == quote_id
    assert len(invoice.items) == 1
    assert invoice.items[0].description == "Stavebné práce"

    updated_quote = db.query(Quote).filter(Quote.id == quote_id).first()
    assert updated_quote.status == "Prevedená na faktúru"

    db.close()


def test_convert_accepted_quote_to_proforma_invoice():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Akceptovaná")
    quote_id = quote.id
    db.close()

    response = post_form(
        f"/quotes/{quote_id}/convert-to-invoice",
        [("is_proforma", "on")],
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    invoice = db.query(Invoice).first()

    assert invoice.is_proforma is True
    assert invoice.invoice_number == "ZF2026001"

    db.close()


def test_convert_quote_rejects_when_vat_regime_conflicts():
    """Ak firma medzičasom prestala byť platiteľom DPH, ale ponuka má
    položky s nenulovou sadzbou, konverzia sa musí odmietnuť namiesto
    tichého vytvorenia nekonzistentnej faktúry."""

    db = TestingSessionLocal()

    company = db.query(Company).first()
    company.is_vat_payer = False
    db.commit()

    quote = create_sample_quote(db, status="Akceptovaná")
    quote_id = quote.id
    db.close()

    response = client.post(f"/quotes/{quote_id}/convert-to-invoice")

    assert response.status_code == 422


def test_converted_quote_cannot_be_converted_again():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Akceptovaná")
    quote_id = quote.id
    db.close()

    client.post(f"/quotes/{quote_id}/convert-to-invoice")

    response = client.post(f"/quotes/{quote_id}/convert-to-invoice")

    assert response.status_code == 409


# =========================================
# PROFORMA FAKTÚRY VYLÚČENÉ Z TRŽIEB
# =========================================

def test_proforma_invoice_excluded_from_dashboard_revenue():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Akceptovaná")
    quote_id = quote.id
    db.close()

    post_form(
        f"/quotes/{quote_id}/convert-to-invoice",
        [("is_proforma", "on")]
    )

    # označíme proforma faktúru ako "uhradenú"
    post_form("/invoices/1/status", [("status", "Uhradená")])

    response = client.get("/")

    assert "615.00" not in response.text
    assert "500.00" not in response.text


def test_proforma_invoice_excluded_from_customer_totals():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Akceptovaná")
    quote_id = quote.id
    customer_id = quote.customer_id
    db.close()

    post_form(
        f"/quotes/{quote_id}/convert-to-invoice",
        [("is_proforma", "on")]
    )

    response = client.get(f"/customers/{customer_id}")

    assert "Celkovo fakturované" not in response.text or "615.00" not in response.text


# =========================================
# ZOZNAM PONÚK (FILTER)
# =========================================

def test_quotes_list_page():

    db = TestingSessionLocal()
    create_sample_quote(db)
    db.close()

    response = client.get("/ponuky")

    assert response.status_code == 200
    assert "CP2026099" in response.text


def test_quotes_list_filtered_by_status():

    db = TestingSessionLocal()
    create_sample_quote(db, status="Odoslaná")
    db.close()

    response = client.get("/ponuky?status=Akceptovan%C3%A1")

    assert response.status_code == 200
    assert "CP2026099" not in response.text


def test_quotes_list_filtered_by_expired():

    db = TestingSessionLocal()
    quote = create_sample_quote(db, status="Odoslaná")
    quote.valid_until = date.today() - timedelta(days=5)
    db.commit()
    db.close()

    response = client.get("/ponuky?status=Po+platnosti")

    assert response.status_code == 200
    assert "CP2026099" in response.text


# =========================================
# PDF - PONUKA A DODACÍ LIST
# =========================================

def test_quote_pdf_download():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote_id = quote.id
    db.close()

    response = client.get(f"/quotes/{quote_id}/pdf")

    assert response.status_code == 200
    assert response.content[:4] == b"%PDF"


def test_quote_pdf_not_found():

    response = client.get("/quotes/999999/pdf")

    assert response.status_code == 404


def test_quote_delivery_note_excludes_prices():

    db = TestingSessionLocal()
    quote = create_sample_quote(db)
    quote_id = quote.id
    db.close()

    response = client.get(f"/quotes/{quote_id}/delivery-note")

    assert response.status_code == 200
    assert response.content[:4] == b"%PDF"

    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(response.content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert "500.00" not in text
    assert "Stavebné práce" in text


def test_invoice_delivery_note():

    db = TestingSessionLocal()
    customer = get_test_customer()

    invoice = Invoice(
        invoice_number="2026050",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14)
    )

    from models import InvoiceItem

    invoice.items.append(
        InvoiceItem(
            description="Elektroinštalácia",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("400.00"),
            vat_rate=23
        )
    )

    db.add(invoice)
    db.commit()
    invoice_id = invoice.id
    db.close()

    response = client.get(f"/invoices/{invoice_id}/delivery-note")

    assert response.status_code == 200
    assert response.content[:4] == b"%PDF"


# =========================================
# AUTENTIFIKÁCIA - QUOTE ROUTES
# =========================================

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/ponuky"),
        ("get", "/customers/1/quotes/new"),
        ("post", "/customers/1/quotes"),
        ("get", "/quotes/1"),
        ("get", "/quotes/1/edit"),
        ("post", "/quotes/1/edit"),
        ("post", "/quotes/1/delete"),
        ("post", "/quotes/1/status"),
        ("post", "/quotes/1/convert-to-invoice"),
        ("get", "/quotes/1/pdf"),
        ("get", "/quotes/1/delivery-note"),
    ]
)
def test_quote_routes_require_login(method, path):

    del app.dependency_overrides[require_login_page]

    try:

        response = getattr(client, method)(path, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = lambda: "testuser"
