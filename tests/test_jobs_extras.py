"""
Testy pre Fázu 4 - vylepšenia zákaziek:
fotodokumentácia (pred/po), náklady a zisk, kalendár/plánovač.
"""

import io
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
from PIL import Image as PILImage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import require_login_api, require_login_page
from database import Base, get_db
from main import app
from models import Customer, Invoice, InvoiceItem, Job, JobCost, JobPhoto


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_database(tmp_path, monkeypatch):

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Fotky sa počas testov ukladajú do dočasného priečinka, nie do
    # skutočného uploads/job_photos - nech testy nezanechávajú súbory
    # na disku a nekolidujú medzi sebou.
    import uploads_utils

    monkeypatch.setattr(uploads_utils, "JOB_PHOTOS_DIR", tmp_path / "job_photos")

    db = TestingSessionLocal()

    customer = Customer(name="Peter Zákazník")
    db.add(customer)
    db.commit()

    job = Job(
        title="Rekonštrukcia kúpeľne",
        status="Prebieha",
        customer_id=customer.id,
        due_date=date.today()
    )
    db.add(job)
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

    return client.post(url, content=body, headers=headers, **kwargs)


def make_png_bytes() -> bytes:

    buffer = io.BytesIO()
    PILImage.new("RGB", (20, 20), (10, 20, 30)).save(buffer, format="PNG")

    return buffer.getvalue()


def get_test_job() -> Job:

    db = TestingSessionLocal()
    job = db.query(Job).first()
    db.close()

    return job


# =========================================
# NÁKLADY A ZISK
# =========================================

def test_edit_job_page_shows_zero_profit_without_data():

    job = get_test_job()

    response = client.get(f"/jobs/{job.id}/edit")

    assert response.status_code == 200
    assert "Náklady a zisk" in response.text


def test_add_job_cost_success():

    job = get_test_job()

    response = post_form(
        f"/jobs/{job.id}/costs",
        [
            ("description", "Obklady a dlažba"),
            ("amount", "350.00"),
            ("cost_date", date.today().isoformat()),
        ],
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    costs = db.query(JobCost).filter(JobCost.job_id == job.id).all()
    db.close()

    assert len(costs) == 1
    assert costs[0].amount == Decimal("350.00")


def test_add_job_cost_negative_amount_rejected():

    job = get_test_job()

    response = post_form(
        f"/jobs/{job.id}/costs",
        [
            ("description", "Neplatný náklad"),
            ("amount", "-50.00"),
        ]
    )

    assert response.status_code == 422


def test_add_job_cost_invalid_amount_rejected():

    job = get_test_job()

    response = post_form(
        f"/jobs/{job.id}/costs",
        [
            ("description", "Neplatný náklad"),
            ("amount", "abc"),
        ]
    )

    assert response.status_code == 422


def test_add_job_cost_job_not_found():

    response = post_form(
        "/jobs/999999/costs",
        [
            ("description", "Práca"),
            ("amount", "10.00"),
        ]
    )

    assert response.status_code == 404


def test_delete_job_cost():

    job = get_test_job()

    db = TestingSessionLocal()
    cost = JobCost(
        job_id=job.id,
        description="Materiál",
        amount=Decimal("100.00"),
        cost_date=date.today()
    )
    db.add(cost)
    db.commit()
    cost_id = cost.id
    db.close()

    response = client.post(
        f"/jobs/{job.id}/costs/{cost_id}/delete",
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    assert db.query(JobCost).filter(JobCost.id == cost_id).first() is None
    db.close()


def test_delete_job_cost_not_found():

    job = get_test_job()

    response = client.post(f"/jobs/{job.id}/costs/999999/delete")

    assert response.status_code == 404


def test_profit_calculation_with_invoice_and_costs():
    """Fakturovaná suma - náklady = zisk, presne podľa zadania."""

    job = get_test_job()

    db = TestingSessionLocal()

    invoice = Invoice(
        invoice_number="2026777",
        customer_id=job.customer_id,
        job_id=job.id,
        status="Odoslaná",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14)
    )
    invoice.items.append(
        InvoiceItem(
            description="Práca",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("1200.00"),
            vat_rate=0
        )
    )
    db.add(invoice)

    db.add(JobCost(
        job_id=job.id,
        description="Materiál",
        amount=Decimal("350.00"),
        cost_date=date.today()
    ))
    db.add(JobCost(
        job_id=job.id,
        description="Subdodávka",
        amount=Decimal("200.00"),
        cost_date=date.today()
    ))

    db.commit()
    db.close()

    response = client.get(f"/jobs/{job.id}/edit")

    assert response.status_code == 200
    assert "1200.00" in response.text
    assert "550.00" in response.text
    assert "650.00" in response.text


def test_profit_excludes_draft_invoices():
    """Faktúra v stave Návrh sa nemá počítať do 'fakturovanej sumy'."""

    job = get_test_job()

    db = TestingSessionLocal()

    invoice = Invoice(
        invoice_number="2026778",
        customer_id=job.customer_id,
        job_id=job.id,
        status="Návrh",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14)
    )
    invoice.items.append(
        InvoiceItem(
            description="Práca",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("999.00"),
            vat_rate=0
        )
    )
    db.add(invoice)
    db.commit()
    db.close()

    response = client.get(f"/jobs/{job.id}/edit")

    assert "999.00" not in response.text


def test_profit_excludes_proforma_invoices():

    job = get_test_job()

    db = TestingSessionLocal()

    invoice = Invoice(
        invoice_number="ZF2026001",
        customer_id=job.customer_id,
        job_id=job.id,
        status="Odoslaná",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        is_proforma=True
    )
    invoice.items.append(
        InvoiceItem(
            description="Práca",
            quantity=Decimal("1"),
            unit="ks",
            unit_price=Decimal("777.00"),
            vat_rate=0
        )
    )
    db.add(invoice)
    db.commit()
    db.close()

    response = client.get(f"/jobs/{job.id}/edit")

    assert "777.00" not in response.text


# =========================================
# FOTODOKUMENTÁCIA
# =========================================

def test_upload_job_photo_success():

    job = get_test_job()

    response = client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("pred.png", make_png_bytes(), "image/png")},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    photos = db.query(JobPhoto).filter(JobPhoto.job_id == job.id).all()
    db.close()

    assert len(photos) == 1
    assert photos[0].photo_type == "pred"


def test_upload_job_photo_rejects_non_image():

    job = get_test_job()

    response = client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("fake.png", b"<script>evil</script>", "image/png")}
    )

    assert response.status_code == 422


def test_upload_job_photo_invalid_type_defaults_to_pred():

    job = get_test_job()

    response = client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "neplatny"},
        files={"photo": ("photo.png", make_png_bytes(), "image/png")},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job.id).first()
    db.close()

    assert photo.photo_type == "pred"


def test_upload_job_photo_job_not_found():

    response = client.post(
        "/jobs/999999/photos",
        data={"photo_type": "pred"},
        files={"photo": ("photo.png", make_png_bytes(), "image/png")}
    )

    assert response.status_code == 404


def test_upload_job_photo_no_file_rejected():

    job = get_test_job()

    response = client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "pred"}
    )

    assert response.status_code == 422


def test_multiple_photos_do_not_overwrite_each_other():
    """Na rozdiel od loga/podpisu (jeden pevný súbor) sa fotky zákazky
    nesmú navzájom prepisovať - každý upload musí zostať zachovaný."""

    job = get_test_job()

    client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("a.png", make_png_bytes(), "image/png")}
    )
    client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("b.png", make_png_bytes(), "image/png")}
    )

    db = TestingSessionLocal()
    photos = db.query(JobPhoto).filter(JobPhoto.job_id == job.id).all()
    db.close()

    assert len(photos) == 2
    assert photos[0].filename != photos[1].filename


def test_serve_job_photo():

    job = get_test_job()

    client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "po"},
        files={"photo": ("photo.png", make_png_bytes(), "image/png")}
    )

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job.id).first()
    filename = photo.filename
    db.close()

    response = client.get(f"/jobs/photos/{filename}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_serve_job_photo_not_found():

    response = client.get("/jobs/photos/does-not-exist.png")

    assert response.status_code == 404


def test_serve_job_photo_blocks_path_traversal():

    response = client.get("/jobs/photos/..%2f..%2fmain.py")

    assert response.status_code == 404


def test_delete_job_photo():

    job = get_test_job()

    client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("photo.png", make_png_bytes(), "image/png")}
    )

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job.id).first()
    photo_id = photo.id
    filename = photo.filename
    db.close()

    response = client.post(
        f"/jobs/{job.id}/photos/{photo_id}/delete",
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    assert db.query(JobPhoto).filter(JobPhoto.id == photo_id).first() is None
    db.close()

    # súbor sa už nemá dať servovať
    response = client.get(f"/jobs/photos/{filename}")
    assert response.status_code == 404


def test_delete_job_photo_not_found():

    job = get_test_job()

    response = client.post(f"/jobs/{job.id}/photos/999999/delete")

    assert response.status_code == 404


def test_edit_job_page_shows_uploaded_photos():

    job = get_test_job()

    client.post(
        f"/jobs/{job.id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("photo.png", make_png_bytes(), "image/png")}
    )

    response = client.get(f"/jobs/{job.id}/edit")

    assert response.status_code == 200
    assert "/jobs/photos/" in response.text


# =========================================
# KALENDÁR
# =========================================

def test_calendar_default_shows_current_month():

    response = client.get("/kalendar")

    assert response.status_code == 200


def test_calendar_shows_job_on_due_date():

    job = get_test_job()

    response = client.get(
        f"/kalendar?year={job.due_date.year}&month={job.due_date.month}"
    )

    assert response.status_code == 200
    assert job.title in response.text


def test_calendar_specific_month():

    response = client.get("/kalendar?year=2026&month=9")

    assert response.status_code == 200
    assert "September 2026" in response.text


def test_calendar_invalid_month_rejected():

    response = client.get("/kalendar?year=2026&month=13")

    assert response.status_code == 400


def test_calendar_navigation_links_present():

    response = client.get("/kalendar?year=2026&month=6")

    assert response.status_code == 200
    assert "year=2026&amp;month=5" in response.text or "year=2026&month=5" in response.text
    assert "year=2026&amp;month=7" in response.text or "year=2026&month=7" in response.text


def test_calendar_year_boundary_navigation():
    """December -> január budúceho roka, január -> december predošlého."""

    response = client.get("/kalendar?year=2026&month=12")

    assert response.status_code == 200
    assert "year=2027&amp;month=1" in response.text or "year=2027&month=1" in response.text


def test_calendar_does_not_show_job_from_other_month():

    job = get_test_job()

    other_month = job.due_date.month % 12 + 1
    other_year = job.due_date.year if other_month != 1 else job.due_date.year + 1

    response = client.get(f"/kalendar?year={other_year}&month={other_month}")

    assert response.status_code == 200
    assert job.title not in response.text


# =========================================
# AUTENTIFIKÁCIA
# =========================================

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/kalendar"),
        ("post", "/jobs/1/photos"),
        ("post", "/jobs/1/photos/1/delete"),
        ("get", "/jobs/photos/x.png"),
        ("post", "/jobs/1/costs"),
        ("post", "/jobs/1/costs/1/delete"),
    ]
)
def test_routes_require_login(method, path):

    del app.dependency_overrides[require_login_page]

    try:

        response = getattr(client, method)(path, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = lambda: "testuser"
