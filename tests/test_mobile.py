"""
Testy mobilného zobrazenia. Skutočné vykreslenie v prehliadači sa tu
neskúša (to vyžaduje prehliadač) - testy strážia štruktúru, od ktorej
mobilné rozloženie závisí: viewport meta v každej stránke, popisky
(`data-label`) buniek tabuliek položiek (CSS z nich na mobile skladá
kartičky) a prítomnosť kľúčových mobilných CSS pravidiel.
"""

import os
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import require_login_api, require_login_page
from csrf import verify_csrf
from database import Base, get_db
from main import app
from models import Company, Customer, Invoice, InvoiceItem, Quote, QuoteItem


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

    customer = Customer(name="Mobilný zákazník")
    db.add(customer)
    db.add(Company(name="Firma", is_vat_payer=True))
    db.commit()

    invoice = Invoice(
        invoice_number="2026001",
        customer_id=customer.id,
        status="Odoslaná",
        issue_date=date(2026, 5, 1),
        due_date=date(2026, 5, 15),
    )
    db.add(invoice)
    db.flush()

    db.add(InvoiceItem(
        invoice_id=invoice.id, description="Práca", quantity=Decimal("1"),
        unit="ks", unit_price=Decimal("100"), vat_rate=23,
    ))

    quote = Quote(
        quote_number="CP2026001", customer_id=customer.id,
        status="Odoslaná", issue_date=date(2026, 5, 1),
    )
    db.add(quote)
    db.flush()

    db.add(QuoteItem(
        quote_id=quote.id, description="Ponuka", quantity=Decimal("1"),
        unit="ks", unit_price=Decimal("100"), vat_rate=23,
    ))

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

EDIT_LABELS = ["Popis", "Množ.", "MJ", "Cena/MJ (€)", "DPH"]
VIEW_LABELS = ["Popis", "Množ.", "MJ", "Cena/MJ", "DPH", "Spolu"]


def test_every_page_template_has_viewport_meta():

    # čiastkové šablóny (bez <html>) sa preskakujú
    for template in sorted((PROJECT_ROOT / "templates").glob("*.html")):

        text = template.read_text(encoding="utf-8")

        if "<html" not in text:
            continue

        assert 'name="viewport"' in text, f"{template.name} nemá viewport meta"


@pytest.mark.parametrize("path", [
    "/customers/1/invoices/new",
    "/customers/1/quotes/new",
])
def test_item_form_cells_have_labels_including_js_template(path):

    text = client.get(path).text

    # riadok vo formulári aj riadok v <template> (ten klonuje "Pridať položku")
    template_part = text[text.index('<template id="itemRowTemplate">'):]

    for label in EDIT_LABELS:

        assert f'data-label="{label}"' in text, label
        assert f'data-label="{label}"' in template_part, f"template: {label}"


def test_credit_note_form_cells_have_labels():

    text = client.get("/invoices/1/credit-note/new").text

    for label in EDIT_LABELS:
        assert f'data-label="{label}"' in text, label


@pytest.mark.parametrize("path", ["/invoices/1", "/quotes/1"])
def test_detail_tables_have_labels(path):

    text = client.get(path).text

    for label in VIEW_LABELS:
        assert f'data-label="{label}"' in text, label


def test_item_cells_have_no_label_on_remove_button_column():

    text = client.get("/customers/1/invoices/new").text

    # posledná bunka (tlačidlo ×) nesmie mať popisok - na mobile by
    # ho CSS vykreslilo nad tlačidlom
    removes = re.findall(r'<td[^>]*>\s*<button[^>]*invoice-item-remove', text)

    assert removes
    assert all("data-label" not in item for item in removes)


def test_stylesheet_contains_key_mobile_rules():

    css = (PROJECT_ROOT / "static" / "style.css").read_text(encoding="utf-8")

    mobile = css[css.index("MOBIL - OVLÁDANIE PRSTAMI"):]

    assert "table.invoice-items-table tr.invoice-item-row" in mobile
    assert "attr(data-label)" in mobile
    assert "form .invoice-form-grid" in mobile
    assert "font-size: 16px" in mobile      # iOS nezväčšuje stránku pri focuse
    assert ".topbar-actions" in mobile and "min-width: 0" in mobile
    assert "min-height: 44px" in mobile     # plocha na ťuknutie
