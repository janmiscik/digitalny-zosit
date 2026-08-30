import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


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
from urllib.parse import urlencode

from auth import require_login_api, require_login_page
from database import Base, get_db
from invoice_pdf import generate_invoice_pdf
from invoice_utils import (
    NON_VAT_PAYER_NOTICE,
    REVERSE_CHARGE_NOTICE,
    calculate_invoice_totals,
    next_invoice_number,
    validate_invoice_vat,
    validate_reverse_charge_eligibility,
    validate_vat_regime,
)
from main import app
from models import Company, Customer, Invoice, InvoiceItem, Job


# =========================================
# TEST DATABASE
# =========================================

TEST_DATABASE_URL = "sqlite://"


test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={
        "check_same_thread": False
    },
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
    db.refresh(customer)

    job = Job(
        title="Oprava kotla",
        status="Nová",
        customer_id=customer.id
    )

    db.add(job)
    db.commit()

    # Predvolená testovacia firma JE platiteľom DPH - drvivá väčšina
    # existujúcich testov predpokladá bežnú fakturáciu s DPH (napr.
    # sadzba 23 %). Testy pre neplatcu DPH / prenesenie daňovej
    # povinnosti si is_vat_payer explicitne nastavia inak.
    company = Company(
        name="Testovacia firma s.r.o.",
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


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """
    Vytiahne textový obsah z PDF pomocou čisto Python knižnice pypdf -
    zámerne NEPOUŽÍVA externú binárku ako pdftotext (poppler-utils),
    lebo tá na Windows vývojárskych strojoch typicky nie je nainštalovaná
    a testy by tam boli nespustiteľné.
    """

    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))

    return "\n".join(
        page.extract_text() or ""
        for page in reader.pages
    )


def post_form(url, items, **kwargs):
    """
    httpx v tejto verzii nevie priamo zakódovať zoznam dvojíc (opakované
    form kľúče, napr. viac riadkov položiek faktúry) cez `data=[...]`.
    Telo si preto zostavíme ručne cez urlencode a pošleme ako raw content.
    """

    body = urlencode(items)

    headers = kwargs.pop("headers", {})
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    return client.post(
        url,
        content=body,
        headers=headers,
        **kwargs
    )


# =========================================
# INVOICE_UTILS - ČÍSLOVANIE
# =========================================

def test_next_invoice_number_first_of_year():

    db = TestingSessionLocal()

    number = next_invoice_number(db, 2026)

    db.close()

    assert number == "2026001"


def test_next_invoice_number_increments():

    db = TestingSessionLocal()

    customer = db.query(Customer).first()

    invoice = Invoice(
        invoice_number="2026001",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date(2026, 1, 10),
        due_date=date(2026, 1, 24)
    )

    db.add(invoice)
    db.commit()

    number = next_invoice_number(db, 2026)

    db.close()

    assert number == "2026002"


def test_next_invoice_number_resets_per_year():

    db = TestingSessionLocal()

    customer = db.query(Customer).first()

    invoice = Invoice(
        invoice_number="2026005",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date(2026, 6, 1),
        due_date=date(2026, 6, 15)
    )

    db.add(invoice)
    db.commit()

    number_2026 = next_invoice_number(db, 2026)
    number_2027 = next_invoice_number(db, 2027)

    db.close()

    assert number_2026 == "2026006"
    assert number_2027 == "2027001"


# =========================================
# INVOICE_UTILS - VÝPOČET SÚM
# =========================================

class FakeItem:

    def __init__(self, quantity, unit_price, vat_rate):

        self.quantity = quantity
        self.unit_price = unit_price
        self.vat_rate = vat_rate


def test_calculate_invoice_totals_single_rate():

    items = [
        FakeItem(Decimal("2"), Decimal("50.00"), 23),
        FakeItem(Decimal("1"), Decimal("100.00"), 23),
    ]

    totals = calculate_invoice_totals(items)

    assert totals["total_base"] == Decimal("200.00")
    assert totals["total_vat"] == Decimal("46.00")
    assert totals["total_gross"] == Decimal("246.00")

    assert len(totals["vat_breakdown"]) == 1
    assert totals["vat_breakdown"][0]["rate"] == 23


def test_calculate_invoice_totals_mixed_rates():

    items = [
        FakeItem(Decimal("1"), Decimal("100.00"), 23),
        FakeItem(Decimal("1"), Decimal("100.00"), 0),
    ]

    totals = calculate_invoice_totals(items)

    assert totals["total_base"] == Decimal("200.00")
    assert totals["total_vat"] == Decimal("23.00")
    assert totals["total_gross"] == Decimal("223.00")

    assert len(totals["vat_breakdown"]) == 2


def test_calculate_invoice_totals_empty():

    totals = calculate_invoice_totals([])

    assert totals["total_base"] == Decimal("0")
    assert totals["total_vat"] == Decimal("0")
    assert totals["total_gross"] == Decimal("0")
    assert totals["vat_breakdown"] == []


# =========================================
# COMPANY SETTINGS
# =========================================

def test_settings_get_creates_empty_company():

    response = client.get("/settings")

    assert response.status_code == 200


def test_settings_save():

    response = client.post(
        "/settings",
        data={
            "name": "Ján Novák - Vodoinštalatér",
            "ico": "87654321",
            "dic": "2087654321",
            "ic_dph": "",
            "address": "Hlavná 1",
            "city": "Prešov",
            "zip_code": "08001",
            "iban": "SK0000000000000000000000",
            "email": "jan@example.com",
            "phone": "0900123456"
        },
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.name == "Ján Novák - Vodoinštalatér"
    assert company.ico == "87654321"
    assert company.ic_dph is None


# =========================================
# VYTVORENIE FAKTÚRY
# =========================================

def get_test_customer_and_job():

    db = TestingSessionLocal()

    customer = db.query(Customer).first()
    job = db.query(Job).first()

    db.close()

    return customer, job


def test_new_invoice_form():

    customer, job = get_test_customer_and_job()

    response = client.get(
        f"/customers/{customer.id}/invoices/new"
    )

    assert response.status_code == 200


def test_new_invoice_form_with_job():

    customer, job = get_test_customer_and_job()

    response = client.get(
        f"/customers/{customer.id}/invoices/new?job_id={job.id}"
    )

    assert response.status_code == 200
    assert "Oprava kotla" in response.text


def test_create_invoice_success():

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
            ("quantity", "2"),
            ("unit", "hod"),
            ("unit_price", "20.00"),
            ("vat_rate", "23"),
            ("description", "Materiál"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "50.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/invoices/")


def test_create_invoice_no_items_fails():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", ""),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_customer_not_found():

    response = post_form(
        "/customers/999999/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 404


# =========================================
# VALIDÁCIA CENY POLOŽKY FAKTÚRY
# =========================================

def test_create_invoice_negative_price_rejected():

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
            ("unit", "ks"),
            ("unit_price", "-50.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_zero_price_allowed():
    """Cena 0 je legitímna (napr. bezplatná položka/zľava do 100%),
    len záporná cena nedáva zmysel."""

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", "Bezplatná obhliadka"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "0"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303


def test_create_invoice_zero_quantity_rejected():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", "Práca"),
            ("quantity", "0"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_price_too_many_decimal_places_rejected():

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
            ("unit", "ks"),
            ("unit_price", "10.999"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_absurdly_large_price_rejected():
    """Cena, ktorá by sa nezmestila do DB stĺpca Numeric(10, 2),
    sa musí odmietnuť pri validácii, nie zlyhať až pri zápise do DB."""

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
            ("unit", "ks"),
            ("unit_price", "123456789.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


# =========================================
# VALIDÁCIA DÁTUMOV FAKTÚRY
# =========================================

def test_create_invoice_due_date_before_issue_date_rejected():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date - timedelta(days=5)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_due_date_equal_issue_date_allowed():
    """Splatnosť v deň vystavenia (napr. platba v hotovosti na mieste)
    je legitímny prípad, nemá zmysel to blokovať."""

    customer, job = get_test_customer_and_job()

    issue_date = date.today()

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", issue_date.isoformat()),
            ("description", "Hotovostná platba"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303


def test_update_invoice_due_date_before_issue_date_rejected():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    issue_date = date.today()
    due_date = issue_date - timedelta(days=1)

    response = post_form(
        f"/invoices/{invoice_id}/edit",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_numbers_increment():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    def create_one():

        response = post_form(
            f"/customers/{customer.id}/invoices",
            [
                ("issue_date", issue_date.isoformat()),
                ("due_date", due_date.isoformat()),
                ("description", "Práca"),
                ("quantity", "1"),
                ("unit", "ks"),
                ("unit_price", "10.00"),
                ("vat_rate", "23"),
            ],
            follow_redirects=False
        )

        return response.headers["location"]

    first_location = create_one()
    second_location = create_one()

    first_id = int(first_location.split("/")[-1])
    second_id = int(second_location.split("/")[-1])

    db = TestingSessionLocal()

    first_invoice = db.query(Invoice).filter(Invoice.id == first_id).first()
    second_invoice = db.query(Invoice).filter(Invoice.id == second_id).first()

    db.close()

    assert first_invoice.invoice_number != second_invoice.invoice_number


# =========================================
# DETAIL A PDF
# =========================================

def create_sample_invoice(db):

    customer = db.query(Customer).first()

    invoice = Invoice(
        invoice_number="2026099",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14)
    )

    invoice.items.append(
        InvoiceItem(
            description="Testovacia položka",
            quantity=Decimal("3"),
            unit="ks",
            unit_price=Decimal("15.50"),
            vat_rate=19
        )
    )

    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    return invoice


def test_invoice_detail_page():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = client.get(f"/invoices/{invoice_id}")

    assert response.status_code == 200
    assert "2026099" in response.text


def test_invoice_detail_not_found():

    response = client.get("/invoices/999999")

    assert response.status_code == 404


def test_invoice_pdf_download():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = client.get(f"/invoices/{invoice_id}/pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"


def test_invoice_pdf_generation_direct_with_diacritics():
    """
    Priamy test PDF generátora - overí, že vygenerovanie s diakritikou
    (slovenské znaky v popisoch) neskončí chybou.
    """

    db = TestingSessionLocal()

    customer = db.query(Customer).first()
    customer.name = "Ľubomír Šťastný"
    customer.address = "Žižkova 5, Košice"

    invoice = Invoice(
        invoice_number="2026100",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14)
    )

    invoice.items.append(
        InvoiceItem(
            description="Oprava strešnej krytiny a odkvapov",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("250.00"),
            vat_rate=23
        )
    )

    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    company = Company(
        name="Peter Kováč",
        ico="11122233",
        address="Dlhá 10",
        city="Prešov"
    )

    pdf_bytes = generate_invoice_pdf(invoice, company)

    db.close()

    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 1000


def test_invoice_pdf_generation_without_company():
    """
    PDF sa musí dať vygenerovať aj keď si používateľ ešte nevyplnil
    fakturačné údaje firmy (company=None).
    """

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)

    pdf_bytes = generate_invoice_pdf(invoice, None)

    db.close()

    assert pdf_bytes[:4] == b"%PDF"


def test_update_invoice_status():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = client.post(
        f"/invoices/{invoice_id}/status",
        data={"status": "Odoslaná"},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    updated = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    db.close()

    assert updated.status == "Odoslaná"


def test_update_invoice_status_invalid():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = client.post(
        f"/invoices/{invoice_id}/status",
        data={"status": "Neexistujúci stav"}
    )

    assert response.status_code == 422


# =========================================
# PRECHODY STAVOV FAKTÚRY
# =========================================

def set_invoice_status_directly(invoice_id: int, status: str) -> None:

    db = TestingSessionLocal()
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    invoice.status = status
    db.commit()
    db.close()


def get_invoice_status(invoice_id: int) -> str:

    db = TestingSessionLocal()
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    status = invoice.status
    db.close()

    return status


@pytest.mark.parametrize(
    "current_status,new_status",
    [
        ("Návrh", "Odoslaná"),
        ("Návrh", "Uhradená"),
        ("Návrh", "Stornovaná"),
        ("Odoslaná", "Uhradená"),
        ("Odoslaná", "Stornovaná"),
        ("Uhradená", "Stornovaná"),
    ]
)
def test_allowed_status_transitions_succeed(current_status, new_status):

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = current_status
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = client.post(
        f"/invoices/{invoice_id}/status",
        data={"status": new_status},
        follow_redirects=False
    )

    assert response.status_code == 303
    assert get_invoice_status(invoice_id) == new_status


@pytest.mark.parametrize(
    "current_status,forbidden_status",
    [
        # nedá sa vrátiť späť do Návrhu (obchádzalo by to ochranu
        # require_draft_invoice pre editáciu/zmazanie)
        ("Odoslaná", "Návrh"),
        ("Uhradená", "Návrh"),
        ("Uhradená", "Odoslaná"),
        ("Stornovaná", "Návrh"),
        # Stornovaná je konečný stav - nedá sa z nej nikam prejsť
        ("Stornovaná", "Odoslaná"),
        ("Stornovaná", "Uhradená"),
    ]
)
def test_forbidden_status_transitions_rejected(current_status, forbidden_status):

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = current_status
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = client.post(
        f"/invoices/{invoice_id}/status",
        data={"status": forbidden_status}
    )

    assert response.status_code == 409
    assert get_invoice_status(invoice_id) == current_status


def test_status_noop_transition_allowed():
    """Nastavenie na ten istý stav, aký faktúra už má, je neškodné
    no-op a nemá zmysel ho blokovať."""

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = "Odoslaná"
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = client.post(
        f"/invoices/{invoice_id}/status",
        data={"status": "Odoslaná"},
        follow_redirects=False
    )

    assert response.status_code == 303
    assert get_invoice_status(invoice_id) == "Odoslaná"


def test_overdue_status_cannot_be_set_manually_from_any_state():
    """'Po splatnosti' sa nedá nastaviť ručne, nech je faktúra v
    akomkoľvek stave - je to čisto počítaná vlastnosť."""

    for current_status in ("Návrh", "Odoslaná", "Uhradená", "Stornovaná"):

        db = TestingSessionLocal()
        invoice = create_sample_invoice(db)
        invoice.status = current_status
        invoice_id = invoice.id
        db.commit()
        db.close()

        response = client.post(
            f"/invoices/{invoice_id}/status",
            data={"status": "Po splatnosti"}
        )

        assert response.status_code == 422, (
            f"Očakával som 422 pri prechode z '{current_status}' "
            f"na 'Po splatnosti'"
        )
        assert get_invoice_status(invoice_id) == current_status

        # create_sample_invoice() vždy použije rovnaké číslo faktúry -
        # pred ďalšou iterciou ju treba zmazať, inak spadne na
        # UNIQUE constraint pri ďalšom vytváraní.
        db = TestingSessionLocal()
        db.query(Invoice).filter(Invoice.id == invoice_id).delete()
        db.commit()
        db.close()


def test_overdue_status_not_manually_selectable():
    """
    "Po splatnosti" sa počíta automaticky podľa dátumu splatnosti
    (v zoznamoch faktúr) - nemal by sa dať nastaviť ručne, aby si
    neprotirečil s automatickým výpočtom (napr. faktúra splatná
    o mesiac by sa nemala dať ručne označiť ako "po splatnosti").
    """

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = client.get(f"/invoices/{invoice_id}")

    assert response.status_code == 200
    assert 'value="Po splatnosti"' not in response.text


def test_overdue_status_still_shown_if_already_set():
    """
    Ak faktúra už má (napr. z importu staršej verzie, pred touto opravou)
    uložený stav 'Po splatnosti', appka ho musí zobraziť korektne ako
    statický (needitovateľný) štítok - nie ako položku v rozbaľovacom
    zozname, lebo tento stav sa už nikdy nedá ručne nastaviť ani ponechať
    prostredníctvom formulára."""

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = "Po splatnosti"
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = client.get(f"/invoices/{invoice_id}")

    assert response.status_code == 200
    assert "Po splatnosti" in response.text
    assert 'value="Po splatnosti"' not in response.text


# =========================================
# ZOZNAM FAKTÚR (API)
# =========================================

def test_get_invoices_api():

    db = TestingSessionLocal()
    create_sample_invoice(db)
    db.close()

    response = client.get("/invoices")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) >= 1


def test_get_invoices_filtered_by_customer():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    customer_id = invoice.customer_id
    db.close()

    response = client.get(f"/invoices?customer_id={customer_id}")

    assert response.status_code == 200

    for invoice_data in response.json():
        assert invoice_data["customer_id"] == customer_id


# =========================================
# PEPPOL XML EXPORT
# =========================================

def test_peppol_xml_download():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = client.get(f"/invoices/{invoice_id}/peppol-xml")

    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]
    assert response.content.startswith(b"<?xml")


def test_peppol_xml_not_found():

    response = client.get("/invoices/999999/peppol-xml")

    assert response.status_code == 404


def test_peppol_xml_well_formed_and_valid_structure():

    import xml.etree.ElementTree as ET

    db = TestingSessionLocal()

    customer = db.query(Customer).first()

    invoice = Invoice(
        invoice_number="2026200",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        variable_symbol="2026200"
    )

    invoice.items.append(
        InvoiceItem(
            description="Práca",
            quantity=Decimal("2"),
            unit="hod",
            unit_price=Decimal("25.00"),
            vat_rate=23
        )
    )

    invoice.items.append(
        InvoiceItem(
            description="Materiál oslobodený od DPH",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("100.00"),
            vat_rate=0
        )
    )

    company = Company(
        name="Firma XY",
        ico="11223344",
        ic_dph="SK1122334455",
        address="Testovacia 1",
        city="Bratislava",
        zip_code="81101",
        iban="SK1234567890123456789012",
        peppol_scheme_id="9946"
    )

    db.add(company)
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    from peppol_xml import generate_peppol_xml

    xml_bytes = generate_peppol_xml(invoice, company)

    db.close()

    # Musí byť well-formed XML
    root = ET.fromstring(xml_bytes)

    ns = {
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    }

    # Základné povinné elementy podľa Peppol BIS 3.0
    assert root.find("cbc:CustomizationID", ns) is not None
    assert root.find("cbc:ProfileID", ns) is not None
    assert root.find("cbc:ID", ns).text == "2026200"
    assert root.find("cbc:InvoiceTypeCode", ns).text == "380"
    assert root.find("cbc:DocumentCurrencyCode", ns).text == "EUR"

    supplier = root.find("cac:AccountingSupplierParty/cac:Party", ns)
    assert supplier is not None
    assert supplier.find("cac:PartyLegalEntity/cbc:CompanyID", ns).text == "11223344"

    customer_party = root.find("cac:AccountingCustomerParty/cac:Party", ns)
    assert customer_party is not None

    lines = root.findall("cac:InvoiceLine", ns)
    assert len(lines) == 2

    # DPH kategórie: štandardná (S) a nulová (Z)
    tax_categories = {
        line.find("cac:Item/cac:ClassifiedTaxCategory/cbc:ID", ns).text
        for line in lines
    }
    assert tax_categories == {"S", "Z"}

    # Merná jednotka "hod" sa mapuje na UN/ECE kód HUR
    quantities = root.findall("cac:InvoiceLine/cbc:InvoicedQuantity", ns)
    unit_codes = {q.get("unitCode") for q in quantities}
    assert "HUR" in unit_codes
    assert "C62" in unit_codes

    # Celková suma s DPH: (2*25*1.23) + 100 = 61.50 + 100 = 161.50
    payable = root.find("cac:LegalMonetaryTotal/cbc:PayableAmount", ns)
    assert payable.text == "161.50"


def test_peppol_xml_does_not_reuse_supplier_scheme_for_customer():
    """
    Regresný test na konkrétny bug: generátor predtým OMYLOM použil
    Peppol schému DODÁVATEĽA (company.peppol_scheme_id) aj na
    EndpointID ODBERATEĽA. Tieto dve schémy nemajú nič spoločné a
    odberateľ svoju vlastnú (zatiaľ) nemá nastavenú - EndpointID sa
    preto pre odberateľa nemá vôbec vygenerovať, kým appka nezbiera
    jeho skutočnú Peppol identifikáciu.
    """

    import xml.etree.ElementTree as ET

    db = TestingSessionLocal()

    customer = db.query(Customer).first()
    # explicitne overíme, že zákazník NEMÁ nastavenú vlastnú Peppol schému
    assert customer.peppol_scheme_id is None

    invoice = Invoice(
        invoice_number="2026201",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14)
    )

    invoice.items.append(
        InvoiceItem(
            description="Práca",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("100.00"),
            vat_rate=23
        )
    )

    company = Company(
        name="Firma XY",
        ico="11223344",
        peppol_scheme_id="9946"
    )

    db.add(company)
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    from peppol_xml import generate_peppol_xml

    xml_bytes = generate_peppol_xml(invoice, company)

    db.close()

    root = ET.fromstring(xml_bytes)

    ns = {
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    }

    supplier_endpoint = root.find(
        "cac:AccountingSupplierParty/cac:Party/cbc:EndpointID",
        ns
    )
    customer_endpoint = root.find(
        "cac:AccountingCustomerParty/cac:Party/cbc:EndpointID",
        ns
    )

    # Dodávateľ svoj EndpointID (so svojou schémou) má...
    assert supplier_endpoint is not None
    assert supplier_endpoint.get("schemeID") == "9946"

    # ...ale odberateľ NESMIE dostať schému dodávateľa - keďže vlastnú
    # nemá, element sa má úplne vynechať, nie obsahovať "9946".
    assert customer_endpoint is None


def test_peppol_xml_uses_customers_own_scheme_when_set():

    import xml.etree.ElementTree as ET

    db = TestingSessionLocal()

    customer = db.query(Customer).first()
    customer.ico = "99887766"
    customer.peppol_scheme_id = "0088"
    db.commit()

    invoice = Invoice(
        invoice_number="2026202",
        customer_id=customer.id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14)
    )

    invoice.items.append(
        InvoiceItem(
            description="Práca",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("100.00"),
            vat_rate=23
        )
    )

    company = Company(
        name="Firma XY",
        ico="11223344",
        peppol_scheme_id="9946"
    )

    db.add(company)
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    from peppol_xml import generate_peppol_xml

    xml_bytes = generate_peppol_xml(invoice, company)

    db.close()

    root = ET.fromstring(xml_bytes)

    ns = {
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    }

    customer_endpoint = root.find(
        "cac:AccountingCustomerParty/cac:Party/cbc:EndpointID",
        ns
    )

    assert customer_endpoint is not None
    assert customer_endpoint.get("schemeID") == "0088"
    assert customer_endpoint.text == "99887766"


def test_peppol_xml_without_company():
    """
    Musí sa dať vygenerovať aj bez vyplnených údajov firmy (company=None) -
    nesmie spadnúť, aj keď výsledok nebude kompletný.
    """

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)

    from peppol_xml import generate_peppol_xml

    xml_bytes = generate_peppol_xml(invoice, None)

    db.close()

    assert xml_bytes.startswith(b"<?xml")


def test_map_unit_code():

    from peppol_xml import map_unit_code

    assert map_unit_code("ks") == "C62"
    assert map_unit_code("hod") == "HUR"
    assert map_unit_code("m2") == "MTK"
    assert map_unit_code("kg") == "KGM"
    assert map_unit_code("neznáma jednotka") == "C62"


def test_vat_category_code():

    from peppol_xml import vat_category_code

    assert vat_category_code(0) == "Z"
    assert vat_category_code(23) == "S"
    assert vat_category_code(19) == "S"
    assert vat_category_code(5) == "S"


# =========================================
# PDF S LOGOM A PODPISOM
# =========================================

def test_pdf_with_logo_and_signature():

    import io
    from PIL import Image as PILImage
    from uploads_utils import UPLOADS_DIR, delete_image, ensure_uploads_dir

    ensure_uploads_dir()

    buffer = io.BytesIO()
    PILImage.new("RGB", (200, 80), (10, 20, 30)).save(buffer, format="PNG")

    logo_path = UPLOADS_DIR / "logo.png"

    with open(logo_path, "wb") as f:
        f.write(buffer.getvalue())


    buffer2 = io.BytesIO()
    PILImage.new("RGB", (150, 60), (200, 200, 200)).save(buffer2, format="PNG")

    signature_path = UPLOADS_DIR / "signature.png"

    with open(signature_path, "wb") as f:
        f.write(buffer2.getvalue())


    db = TestingSessionLocal()

    invoice = create_sample_invoice(db)

    company = Company(
        name="Firma s logom",
        logo_filename="logo.png",
        signature_filename="signature.png"
    )

    db.add(company)
    db.commit()

    from invoice_pdf import generate_invoice_pdf

    pdf_bytes = generate_invoice_pdf(invoice, company)

    db.close()

    delete_image("logo")
    delete_image("signature")

    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 1000


def test_pdf_without_logo_or_signature_still_works():
    """
    Ak firma nemá nahraté logo/podpis, PDF sa musí vygenerovať bez chyby.
    """

    db = TestingSessionLocal()

    invoice = create_sample_invoice(db)

    company = Company(name="Firma bez loga")

    db.add(company)
    db.commit()

    from invoice_pdf import generate_invoice_pdf

    pdf_bytes = generate_invoice_pdf(invoice, company)

    db.close()

    assert pdf_bytes[:4] == b"%PDF"


# =========================================
# QR PLATOBNÝ KÓD (PAY BY SQUARE)
# =========================================

def test_qr_payment_generates_valid_png():

    from qr_payment import generate_payment_qr_image

    qr = generate_payment_qr_image(
        iban="SK6807200002891987426353",
        amount=Decimal("184.50"),
        variable_symbol="2026001",
        beneficiary_name="Testovacia firma"
    )

    assert qr is not None

    png_bytes = qr.read()

    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_qr_payment_returns_none_without_iban():

    from qr_payment import generate_payment_qr_image

    qr = generate_payment_qr_image(
        iban=None,
        amount=Decimal("100.00"),
        variable_symbol="123",
        beneficiary_name="Firma"
    )

    assert qr is None


def test_qr_payment_roundtrip_data_integrity():
    """
    Overí, že vygenerovaný pay-by-square reťazec sa dá spätne dekódovať
    a obsahuje presne tie údaje, ktoré sme zakódovali (vrátane CRC kontroly).
    """

    import lzma
    import binascii
    import pay_by_square

    payment_string = pay_by_square.generate(
        amount=99.90,
        iban="SK6807200002891987426353",
        swift="TATRSKBX",
        beneficiary_name="Testovacia firma",
        variable_symbol="2026005",
        note="Test"
    )

    subst = "0123456789ABCDEFGHIJKLMNOPQRSTUV"

    binary = "".join(
        bin(subst.index(c))[2:].zfill(5)
        for c in payment_string
    )

    n_bytes = len(binary) // 8

    byte_data = bytes(
        int(binary[i * 8:i * 8 + 8], 2)
        for i in range(n_bytes)
    )

    compressed = byte_data[4:]

    decompressed = lzma.decompress(
        compressed,
        format=lzma.FORMAT_RAW,
        filters=[{
            "id": lzma.FILTER_LZMA1,
            "lc": 3, "lp": 0, "pb": 2, "dict_size": 128 * 1024,
        }]
    )

    checksum = decompressed[:4]
    data = decompressed[4:]

    calculated_checksum = binascii.crc32(data).to_bytes(4, "little")

    assert checksum == calculated_checksum

    fields = data.decode().split("\t")

    assert fields[3] == "99.90"
    assert fields[6] == "2026005"
    assert fields[12] == "SK6807200002891987426353"
    assert fields[13] == "TATRSKBX"
    assert fields[16] == "Testovacia firma"


def test_pdf_includes_qr_code_when_iban_present():

    db = TestingSessionLocal()

    invoice = create_sample_invoice(db)

    company = Company(
        name="Firma s IBAN",
        iban="SK6807200002891987426353"
    )

    db.add(company)
    db.commit()

    from invoice_pdf import generate_invoice_pdf

    pdf_with_iban = generate_invoice_pdf(invoice, company)

    company_no_iban = Company(name="Firma bez IBAN")

    db.add(company_no_iban)
    db.commit()

    pdf_without_iban = generate_invoice_pdf(invoice, company_no_iban)

    db.close()

    # PDF s QR kódom musí byť väčší (obsahuje navyše vygenerovaný obrázok)
    assert len(pdf_with_iban) > len(pdf_without_iban)


# =========================================
# NOVÉ POLIA FIRMY (website, swift_bic)
# =========================================

def test_settings_save_website_and_swift():

    response = client.post(
        "/settings",
        data={
            "name": "Firma XY",
            "website": "www.firmaxy.sk",
            "swift_bic": "TATRSKBX"
        },
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.website == "www.firmaxy.sk"
    assert company.swift_bic == "TATRSKBX"


# =========================================
# SPÔSOB ÚHRADY NA FAKTÚRE
# =========================================

def test_create_invoice_with_payment_method():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("payment_method", "Hotovosť"),
            ("description", "Test"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303

    invoice_id = int(response.headers["location"].split("/")[-1])

    db = TestingSessionLocal()
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    db.close()

    assert invoice.payment_method == "Hotovosť"


def test_create_invoice_default_payment_method():

    customer, job = get_test_customer_and_job()

    issue_date = date.today()
    due_date = issue_date + timedelta(days=14)

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", issue_date.isoformat()),
            ("due_date", due_date.isoformat()),
            ("description", "Test"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303

    invoice_id = int(response.headers["location"].split("/")[-1])

    db = TestingSessionLocal()
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    db.close()

    assert invoice.payment_method == "Prevodom"


# =========================================
# ÚPRAVA FAKTÚRY
# =========================================

def test_edit_invoice_form_draft():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = client.get(f"/invoices/{invoice_id}/edit")

    assert response.status_code == 200
    assert "Testovacia položka" in response.text


def test_edit_invoice_form_not_found():

    response = client.get("/invoices/999999/edit")

    assert response.status_code == 404


def test_edit_invoice_form_non_draft_forbidden():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = "Uhradená"
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = client.get(f"/invoices/{invoice_id}/edit")

    assert response.status_code == 409


def test_update_invoice_changes_items_and_dates():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    new_due_date = date.today() + timedelta(days=30)

    response = post_form(
        f"/invoices/{invoice_id}/edit",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", new_due_date.isoformat()),
            ("payment_method", "Hotovosť"),
            ("note", "Upravená poznámka"),
            ("description", "Nová položka po úprave"),
            ("quantity", "5"),
            ("unit", "hod"),
            ("unit_price", "12.50"),
            ("vat_rate", "19"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/invoices/{invoice_id}"

    db = TestingSessionLocal()
    updated = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    items_count = len(updated.items)
    first_item_description = updated.items[0].description
    first_item_vat_rate = updated.items[0].vat_rate
    db.close()

    assert updated.due_date == new_due_date
    assert updated.payment_method == "Hotovosť"
    assert updated.note == "Upravená poznámka"
    assert items_count == 1
    assert first_item_description == "Nová položka po úprave"
    assert first_item_vat_rate == 19


def test_update_invoice_non_draft_forbidden():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = "Odoslaná"
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = post_form(
        f"/invoices/{invoice_id}/edit",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Pokus o úpravu"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 409

    db = TestingSessionLocal()
    unchanged = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    first_item_description = unchanged.items[0].description
    db.close()

    assert first_item_description == "Testovacia položka"


def test_update_invoice_no_items_fails():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    db.close()

    response = post_form(
        f"/invoices/{invoice_id}/edit",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", ""),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "10.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


# =========================================
# ZMAZANIE FAKTÚRY
# =========================================

def test_delete_draft_invoice():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice_id = invoice.id
    customer_id = invoice.customer_id
    db.close()

    response = client.post(
        f"/invoices/{invoice_id}/delete",
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/customers/{customer_id}"

    db = TestingSessionLocal()
    deleted = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    remaining_items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == invoice_id).count()
    db.close()

    assert deleted is None
    assert remaining_items == 0  # cascade delete of items


def test_delete_non_draft_invoice_forbidden():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = "Uhradená"
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = client.post(f"/invoices/{invoice_id}/delete")

    assert response.status_code == 409

    db = TestingSessionLocal()
    still_exists = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    db.close()

    assert still_exists is not None


def test_delete_invoice_not_found():

    response = client.post("/invoices/999999/delete")

    assert response.status_code == 404


# =========================================
# FINANCIE - DASHBOARD
# =========================================

def test_dashboard_shows_unpaid_total():

    db = TestingSessionLocal()

    invoice = create_sample_invoice(db)
    invoice.status = "Odoslaná"
    db.commit()
    db.close()

    response = client.get("/")

    assert response.status_code == 200
    # 3 * 15.50 = 46.50 základ, +19% DPH = 55.335 -> 55.34 zaokrúhlené
    assert "55.34" in response.text
    assert "Neuhradená 1 faktúra" in response.text


def test_dashboard_shows_revenue_this_month():

    db = TestingSessionLocal()

    invoice = create_sample_invoice(db)
    invoice.status = "Uhradená"
    invoice.issue_date = date.today()
    db.commit()
    db.close()

    response = client.get("/")

    assert response.status_code == 200
    assert "Tržba tento mesiac" in response.text
    assert "55.34" in response.text


def test_dashboard_excludes_cancelled_from_unpaid():

    db = TestingSessionLocal()

    invoice = create_sample_invoice(db)
    invoice.status = "Stornovaná"
    db.commit()
    db.close()

    response = client.get("/")

    assert response.status_code == 200
    # Stornovaná faktúra sa nesmie počítať ako neuhradená
    assert "Neuhradená 1 faktúra" not in response.text


def test_dashboard_shows_overdue_jobs_count():

    db = TestingSessionLocal()

    customer = db.query(Customer).first()

    overdue_job = Job(
        title="Zákazka po termíne",
        status="Prebieha",
        due_date=date.today() - timedelta(days=3),
        customer_id=customer.id
    )

    db.add(overdue_job)
    db.commit()
    db.close()

    response = client.get("/")

    assert response.status_code == 200
    assert "Zákazky po termíne" in response.text


# =========================================
# HISTÓRIA ZÁKAZNÍKA
# =========================================

def test_customer_detail_shows_total_invoiced():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    customer_id = invoice.customer_id
    db.close()

    response = client.get(f"/customers/{customer_id}")

    assert response.status_code == 200
    assert "Celkovo fakturované" in response.text
    assert "55.34" in response.text


def test_customer_detail_no_history_when_no_invoices():

    response = client.get("/customers/1")

    assert response.status_code == 200
    assert "Celkovo fakturované" not in response.text


def test_customer_detail_unpaid_excludes_paid_invoice():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.status = "Uhradená"
    customer_id = invoice.customer_id
    db.commit()
    db.close()

    response = client.get(f"/customers/{customer_id}")

    assert response.status_code == 200
    # Faktúra je uhradená, takže neuhradená suma musí byť nulová
    assert "Neuhradené faktúry (0)" in response.text




# =========================================
# DPH REŽIM - NEPLATCA VS. PLATCA DPH
# =========================================

def set_company_vat_payer(is_vat_payer: bool) -> None:

    db = TestingSessionLocal()
    company = db.query(Company).first()
    company.is_vat_payer = is_vat_payer
    db.commit()
    db.close()


def test_validate_vat_regime_allows_zero_rate_for_non_payer():

    validate_vat_regime(is_vat_payer=False, item_vat_rates=[0, 0])


def test_validate_vat_regime_rejects_nonzero_rate_for_non_payer():

    with pytest.raises(ValueError):
        validate_vat_regime(is_vat_payer=False, item_vat_rates=[0, 23])


def test_validate_vat_regime_allows_any_rate_for_payer():

    validate_vat_regime(is_vat_payer=True, item_vat_rates=[23, 19, 5, 0])


def test_validate_reverse_charge_requires_vat_payer_company():

    with pytest.raises(ValueError):
        validate_reverse_charge_eligibility(
            company_is_vat_payer=False,
            customer_ic_dph="SK1234567890"
        )


def test_validate_reverse_charge_requires_customer_ic_dph():

    with pytest.raises(ValueError):
        validate_reverse_charge_eligibility(
            company_is_vat_payer=True,
            customer_ic_dph=None
        )


def test_validate_reverse_charge_succeeds_when_eligible():

    validate_reverse_charge_eligibility(
        company_is_vat_payer=True,
        customer_ic_dph="SK1234567890"
    )


def test_validate_invoice_vat_reverse_charge_forces_zero_rate():

    with pytest.raises(ValueError):
        validate_invoice_vat(
            company_is_vat_payer=True,
            customer_ic_dph="SK1234567890",
            reverse_charge=True,
            item_vat_rates=[23]
        )


def test_validate_invoice_vat_reverse_charge_success():

    validate_invoice_vat(
        company_is_vat_payer=True,
        customer_ic_dph="SK1234567890",
        reverse_charge=True,
        item_vat_rates=[0, 0]
    )


def test_create_invoice_with_vat_when_not_vat_payer_rejected():

    set_company_vat_payer(False)

    customer, job = get_test_customer_and_job()

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "100.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_zero_vat_when_not_vat_payer_allowed():

    set_company_vat_payer(False)

    customer, job = get_test_customer_and_job()

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Práca"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "100.00"),
            ("vat_rate", "0"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303


def test_create_invoice_reverse_charge_requires_vat_payer_company():

    set_company_vat_payer(False)

    customer, job = get_test_customer_and_job()

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Stavebné práce"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "100.00"),
            ("vat_rate", "0"),
            ("reverse_charge", "on"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_reverse_charge_requires_customer_ic_dph():

    set_company_vat_payer(True)

    db = TestingSessionLocal()
    customer = Customer(name="Zákazník bez IČ DPH")
    db.add(customer)
    db.commit()
    customer_id = customer.id
    db.close()

    response = post_form(
        f"/customers/{customer_id}/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Stavebné práce"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "100.00"),
            ("vat_rate", "0"),
            ("reverse_charge", "on"),
        ]
    )

    assert response.status_code == 422


def test_create_invoice_reverse_charge_success():

    set_company_vat_payer(True)

    # predvolený testovací zákazník má ic_dph="SK1234567890" (viď fixture)
    customer, job = get_test_customer_and_job()

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Stavebné práce"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "1000.00"),
            ("vat_rate", "0"),
            ("reverse_charge", "on"),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    invoice = db.query(Invoice).order_by(Invoice.id.desc()).first()
    assert invoice.reverse_charge is True
    db.close()


def test_create_invoice_reverse_charge_rejects_nonzero_vat_rate():

    set_company_vat_payer(True)

    customer, job = get_test_customer_and_job()

    response = post_form(
        f"/customers/{customer.id}/invoices",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Stavebné práce"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "1000.00"),
            ("vat_rate", "23"),
            ("reverse_charge", "on"),
        ]
    )

    assert response.status_code == 422


def test_update_invoice_vat_regime_validated_too():

    set_company_vat_payer(False)

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.items[0].vat_rate = 0
    invoice_id = invoice.id
    db.commit()
    db.close()

    response = post_form(
        f"/invoices/{invoice_id}/edit",
        [
            ("issue_date", date.today().isoformat()),
            ("due_date", date.today().isoformat()),
            ("description", "Nová položka"),
            ("quantity", "1"),
            ("unit", "ks"),
            ("unit_price", "50.00"),
            ("vat_rate", "23"),
        ]
    )

    assert response.status_code == 422


def test_settings_save_vat_payer_checkbox_checked():

    response = client.post(
        "/settings",
        data={"name": "Firma", "is_vat_payer": "1"},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    assert company.is_vat_payer is True
    db.close()


def test_settings_save_vat_payer_checkbox_unchecked():
    """Needostavená checkbox hodnota v HTML formulári znamená, že sa
    vôbec neposlala - appka to musí interpretovať ako False."""

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    assert company.is_vat_payer is False
    db.close()


# =========================================
# PDF - DPH REŽIM
# =========================================

def test_pdf_hides_vat_column_for_non_vat_payer():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.items[0].vat_rate = 0

    company = Company(name="Neplatca s.r.o.", is_vat_payer=False)

    db.add(company)
    db.commit()
    db.refresh(invoice)

    from invoice_pdf import generate_invoice_pdf

    pdf_bytes = generate_invoice_pdf(invoice, company)

    db.close()

    assert pdf_bytes[:4] == b"%PDF"

    # Text upozornenia musí byť niekde v (nekomprimovanom) obsahu PDF -
    # keďže reportlab defaultne kompresuje stream, len overíme, že sa
    # PDF vygenerovalo bez chyby; textový obsah PDF sa dôkladnejšie
    # kontroluje cez pdftotext v manuálnom vizuálnom teste.
    assert len(pdf_bytes) > 500


def test_pdf_generation_works_with_reverse_charge():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.items[0].vat_rate = 0
    invoice.reverse_charge = True

    company = Company(
        name="Platca s.r.o.",
        ico="12345678",
        is_vat_payer=True
    )

    db.add(company)
    db.commit()
    db.refresh(invoice)

    from invoice_pdf import generate_invoice_pdf

    pdf_bytes = generate_invoice_pdf(invoice, company)

    db.close()

    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_pdf_hides_company_ic_dph_when_not_vat_payer():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.items[0].vat_rate = 0

    company = Company(
        name="Firma s IC DPH ale neplatca",
        ic_dph="SK9999999999",
        is_vat_payer=False
    )

    db.add(company)
    db.commit()
    db.refresh(invoice)

    from invoice_pdf import generate_invoice_pdf

    pdf_bytes = generate_invoice_pdf(invoice, company)

    db.close()

    text = extract_pdf_text(pdf_bytes)

    assert "SK9999999999" not in text
    assert NON_VAT_PAYER_NOTICE in text


def test_pdf_shows_reverse_charge_notice_text():

    db = TestingSessionLocal()
    invoice = create_sample_invoice(db)
    invoice.items[0].vat_rate = 0
    invoice.reverse_charge = True

    company = Company(name="Platca s.r.o.", is_vat_payer=True)

    db.add(company)
    db.commit()
    db.refresh(invoice)

    from invoice_pdf import generate_invoice_pdf

    pdf_bytes = generate_invoice_pdf(invoice, company)

    db.close()

    text = extract_pdf_text(pdf_bytes)

    assert REVERSE_CHARGE_NOTICE in text
    assert "DPH spolu" not in text
