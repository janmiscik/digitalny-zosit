import io
import os
import sys
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
from PIL import Image as PILImage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import require_login_api, require_login_page
from csrf import verify_csrf
from database import Base, get_db
from main import app
from models import Company
from uploads_utils import UPLOADS_DIR, delete_image, image_path


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(bind=test_engine)


def make_png_bytes(color=(255, 0, 0), size=(40, 20)) -> bytes:

    buffer = io.BytesIO()

    img = PILImage.new("RGB", size, color)
    img.save(buffer, format="PNG")

    return buffer.getvalue()


@pytest.fixture(autouse=True)
def setup_test_database(tmp_path, monkeypatch):

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Fotky zákaziek sa počas testov ukladajú do dočasného priečinka,
    # nie do skutočného uploads/job_photos (rovnaký princíp ako v
    # tests/test_jobs_extras.py) - nech testy nezanechávajú súbory na
    # disku a nekolidujú medzi sebou cez opakujúce sa job_id.
    import uploads_utils

    monkeypatch.setattr(uploads_utils, "JOB_PHOTOS_DIR", tmp_path / "job_photos")

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

    # Tieto testy neoverujú CSRF ochranu (na to je tests/test_csrf.py) -
    # tu ju obídeme, nech sa sústredia len na vlastnú business logiku.
    app.dependency_overrides[verify_csrf] = lambda: None

    # Vyčistíme test-obrázky pred aj po teste, aby testy neboli ovplyvnené
    # predchádzajúcimi behmi
    delete_image("logo")
    delete_image("signature")

    yield

    delete_image("logo")
    delete_image("signature")

    app.dependency_overrides.clear()


client = TestClient(app)


# =========================================
# NAHRÁVANIE - SETTINGS ENDPOINT
# =========================================

def test_settings_upload_logo():

    png_bytes = make_png_bytes()

    response = client.post(
        "/settings",
        data={"name": "Firma s logom"},
        files={"logo": ("logo.png", png_bytes, "image/png")},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.logo_filename == "logo.png"
    assert image_path(company.logo_filename) is not None


def test_settings_upload_signature():

    png_bytes = make_png_bytes(color=(0, 0, 255))

    response = client.post(
        "/settings",
        data={"name": "Firma s podpisom"},
        files={"signature": ("podpis.png", png_bytes, "image/png")},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.signature_filename == "signature.png"


def test_settings_upload_invalid_extension():

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.gif", b"not a real gif but bytes", "image/gif")}
    )

    assert response.status_code == 422


def test_settings_upload_too_large():

    # Vytvoríme "súbor" väčší ako 2 MB limit
    huge_bytes = b"0" * (3 * 1024 * 1024)

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", huge_bytes, "image/png")}
    )

    assert response.status_code == 422


# =========================================
# SKUTOČNÉ OVERENIE TYPU OBRÁZKA (nielen podľa prípony/názvu súboru)
# =========================================

def test_settings_upload_rejects_non_image_disguised_as_png():
    """Súbor, ktorý sa len VOLÁ .png, ale v skutočnosti nie je obrázok
    (napr. HTML/text/škodlivý súbor), sa musí odmietnuť - kontrola podľa
    prípony samotnej nestačí."""

    fake_png = b"<script>alert('nie som obrazok')</script>"

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", fake_png, "image/png")}
    )

    assert response.status_code == 422

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company is None or company.logo_filename is None


def test_settings_upload_rejects_jpeg_disguised_as_png():
    """Skutočný JPEG obsah nahraný pod príponou .png sa musí odmietnuť -
    obsah musí zodpovedať deklarovanej prípone, nielen sa podobať na
    'nejaký' obrázok."""

    buffer = io.BytesIO()
    PILImage.new("RGB", (10, 10), (0, 255, 0)).save(buffer, format="JPEG")
    jpeg_bytes_with_png_extension = buffer.getvalue()

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        files={
            "logo": (
                "logo.png",
                jpeg_bytes_with_png_extension,
                "image/png"
            )
        }
    )

    assert response.status_code == 422


def test_settings_upload_rejects_truncated_corrupted_image():
    """Poškodený/orezaný obrázok (napr. prerušené sťahovanie) sa musí
    odmietnuť, nie uložiť a spôsobiť pád neskôr pri generovaní PDF."""

    valid_png = make_png_bytes()

    # odrežeme polovicu súboru - hlavička bude vyzerať ako PNG,
    # ale dáta budú neúplné/poškodené
    truncated = valid_png[: len(valid_png) // 2]

    response = client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", truncated, "image/png")}
    )

    assert response.status_code == 422


def test_settings_upload_accepts_real_jpeg():
    """Overíme aj kladný prípad - skutočný JPEG s príponou .jpg musí
    prejsť (nechceme byť príliš prísni, len odhaliť podvrhnuté súbory)."""

    buffer = io.BytesIO()
    PILImage.new("RGB", (10, 10), (10, 20, 30)).save(buffer, format="JPEG")

    response = client.post(
        "/settings",
        data={"name": "Firma s JPEG logom"},
        files={"logo": ("logo.jpg", buffer.getvalue(), "image/jpeg")},
        follow_redirects=False
    )

    assert response.status_code == 303


def test_settings_remove_logo():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    db = TestingSessionLocal()
    company = db.query(Company).first()
    assert company.logo_filename == "logo.png"
    db.close()


    response = client.post(
        "/settings",
        data={"name": "Firma", "remove_logo": "1"},
        follow_redirects=False
    )

    assert response.status_code == 303

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.logo_filename is None
    assert image_path("logo.png") is None


def test_settings_replace_logo_removes_old_extension():
    """
    Ak sa logo nahradí súborom v inom formáte (napr. .png -> .jpg),
    starý súbor sa musí odstrániť, nie len ponechať vedľa nového.
    """

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    assert (UPLOADS_DIR / "logo.png").exists()


    buffer = io.BytesIO()
    PILImage.new("RGB", (30, 30), (0, 255, 0)).save(buffer, format="JPEG")
    jpg_bytes = buffer.getvalue()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.jpg", jpg_bytes, "image/jpeg")}
    )

    assert not (UPLOADS_DIR / "logo.png").exists()
    assert (UPLOADS_DIR / "logo.jpg").exists()


def test_settings_form_without_files_keeps_existing_logo():
    """
    Uloženie nastavení bez výberu nového súboru nesmie zmazať
    už nahraté logo.
    """

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    client.post(
        "/settings",
        data={"name": "Firma s aktualizovaným nazvom"}
    )

    db = TestingSessionLocal()
    company = db.query(Company).first()
    db.close()

    assert company.logo_filename == "logo.png"
    assert company.name == "Firma s aktualizovaným nazvom"


# =========================================
# NÁHĽAD V NASTAVENIACH
# =========================================

def test_settings_page_shows_logo_preview():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    response = client.get("/settings")

    assert response.status_code == 200
    assert "/uploads/logo.png" in response.text


# =========================================
# OBRÁZOK JE DOSTUPNÝ CEZ /uploads
# =========================================

def test_uploaded_logo_is_served():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    response = client.get("/uploads/logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


# =========================================
# BEZPEČNOSŤ - PRÍSTUP BEZ PRIHLÁSENIA
#
# Tieto testy zámerne odstránia override pre require_login_page, aby
# overili SKUTOČNÉ správanie (nie testovací "vždy prihlásený" skrat).
# =========================================

def test_uploaded_logo_blocked_without_login():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    del app.dependency_overrides[require_login_page]

    try:

        response = client.get("/uploads/logo.png", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    finally:

        app.dependency_overrides[require_login_page] = lambda: "testuser"


def test_uploads_path_traversal_blocked():

    png_bytes = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", png_bytes, "image/png")}
    )

    traversal_attempts = [
        "/uploads/..%2Fmain.py",
        "/uploads/..%2f..%2fmain.py",
        "/uploads/%2e%2e%2fmain.py",
    ]

    for path in traversal_attempts:

        response = client.get(path, follow_redirects=False)

        assert response.status_code == 404, f"Zlyhalo pre: {path}"
        assert b"import" not in response.content
        assert b"FastAPI" not in response.content


def test_uploads_rejects_disallowed_extension():

    response = client.get("/uploads/hacker.exe")

    assert response.status_code == 404


def test_uploads_rejects_unknown_filename():

    response = client.get("/uploads/random-file.png")

    assert response.status_code == 404

# =========================================
# ATOMICITA UPLOADU (stage -> DB commit -> finalize/discard)
# =========================================

def test_failed_settings_commit_does_not_delete_old_logo(monkeypatch):
    """Ak DB commit zlyhá PO nahraní nového loga, staré logo sa NESMIE
    stratiť - appka má byť v konzistentnom stave (starý súbor + stará
    DB hodnota), nie s DB hodnotou ukazujúcou na zmazaný súbor."""

    old_png = make_png_bytes()

    client.post(
        "/settings",
        data={"name": "Firma"},
        files={"logo": ("logo.png", old_png, "image/png")}
    )

    db = TestingSessionLocal()
    company = db.query(Company).first()
    assert company.logo_filename == "logo.png"
    old_logo_path = image_path(company.logo_filename)
    assert old_logo_path is not None
    db.close()

    import routers.company as company_router

    def failing_commit(self):
        raise RuntimeError("simulovane zlyhanie DB commitu")

    monkeypatch.setattr(
        "sqlalchemy.orm.Session.commit",
        failing_commit
    )

    new_png = make_png_bytes()

    with pytest.raises(RuntimeError):

        client.post(
            "/settings",
            data={"name": "Firma"},
            files={"logo": ("logo2.png", new_png, "image/png")}
        )

    # Starý súbor musí byť STÁLE na disku - commit zlyhal, takže sa
    # nesmel finalizovať/zmazať.
    assert old_logo_path.exists()


def test_finalize_staged_image_keeps_old_file_if_replace_fails(monkeypatch):
    """
    Priama jednotka na finalize_staged_image(): ak os.replace() zlyhá
    (napr. plný disk), starý súbor (INEJ prípony) musí ostať netknutý -
    predtým sa najprv mazal starý súbor a AŽ POTOM premenovával nový,
    takže presne tento scenár by starý súbor stratil úplne (ani starý,
    ani nový by neexistoval).
    """

    from uploads_utils import (
        UPLOADS_DIR,
        ensure_uploads_dir,
        finalize_staged_image,
    )

    ensure_uploads_dir()

    old_path = UPLOADS_DIR / "logo.png"
    old_path.write_bytes(make_png_bytes())

    temp_path = UPLOADS_DIR / ".tmp-test-logo.jpg"
    temp_path.write_bytes(b"fake-jpeg-bytes-pre-test")

    import uploads_utils

    original_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("simulovane zlyhanie os.replace (napr. plny disk)")

    monkeypatch.setattr(uploads_utils.os, "replace", failing_replace)

    with pytest.raises(OSError):
        finalize_staged_image(temp_path, "logo.jpg", "logo")

    # Starý súbor (iná prípona) je stále na disku - k jeho mazaniu sa
    # kód vôbec nedostal, lebo os.replace zlyhalo skôr.
    assert old_path.exists()
    # Dočasný súbor ostáva tiež netknutý (nebol premenovaný).
    assert temp_path.exists()


def test_failed_job_photo_commit_removes_orphan_file(monkeypatch):
    """Ak DB commit zlyhá PO zapísaní fotky na disk, appka musí súbor
    zase zmazať - inak by zostal ako osirotený súbor bez DB záznamu."""

    db = TestingSessionLocal()
    from models import Job, Customer

    customer = db.query(Customer).first()

    if customer is None:
        customer = Customer(name="Test")
        db.add(customer)
        db.commit()

    job = Job(title="Test zákazka", status="Nová", customer_id=customer.id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    def failing_commit(self):
        raise RuntimeError("simulovane zlyhanie DB commitu")

    monkeypatch.setattr(
        "sqlalchemy.orm.Session.commit",
        failing_commit
    )

    with pytest.raises(RuntimeError):

        client.post(
            f"/jobs/{job_id}/photos",
            data={"photo_type": "pred"},
            files={"photo": ("photo.png", make_png_bytes(), "image/png")}
        )

    from uploads_utils import JOB_PHOTOS_DIR

    remaining_files = list(JOB_PHOTOS_DIR.glob(f"job{job_id}-*")) if JOB_PHOTOS_DIR.exists() else []

    assert remaining_files == []


# =========================================
# AUTOMATICKÉ MAZANIE SÚBORU PRI ZMAZANÍ ZÁZNAMU (event listener)
# =========================================

def test_deleting_job_photo_row_directly_removes_file():
    """Aj keby JobPhoto záznam zanikol iným spôsobom než cez explicitný
    /delete endpoint (napr. budúce cascade zmazanie zákazky), fyzický
    súbor sa má zmazať automaticky (SQLAlchemy event listener)."""

    db = TestingSessionLocal()
    from models import Job, Customer, JobPhoto

    customer = db.query(Customer).first()

    if customer is None:
        customer = Customer(name="Test")
        db.add(customer)
        db.commit()

    job = Job(title="Test", status="Nová", customer_id=customer.id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    client.post(
        f"/jobs/{job_id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("photo.png", make_png_bytes(), "image/png")}
    )

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job_id).first()
    filename = photo.filename

    from uploads_utils import job_photo_path
    assert job_photo_path(filename) is not None

    # zmažeme priamo cez ORM (nie cez router endpoint) - simuluje
    # buduce cascade zmazanie napr. pri mazani zakazky
    db.delete(photo)
    db.commit()
    db.close()

    assert job_photo_path(filename) is None


# =========================================
# AUTO-RESIZE FOTIEK ZÁKAZIEK
# =========================================

def make_jpeg_bytes(size, color=(200, 100, 50)) -> bytes:

    buffer = io.BytesIO()

    img = PILImage.new("RGB", size, color)
    img.save(buffer, format="JPEG")

    return buffer.getvalue()


def test_large_job_photo_is_resized_to_max_dimension():
    """Veľká fotka (dlhšia strana nad limit) sa má pri uploade
    zmenšiť tak, aby dlhšia strana nepresiahla MAX_PHOTO_DIMENSION."""

    db = TestingSessionLocal()
    from models import Job, Customer, JobPhoto

    customer = db.query(Customer).first()
    if customer is None:
        customer = Customer(name="Test")
        db.add(customer)
        db.commit()

    job = Job(title="Test", status="Nová", customer_id=customer.id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    # 3000x2000 - typický pomer strán fotky z mobilu, väčší než limit.
    response = client.post(
        f"/jobs/{job_id}/photos",
        data={"photo_type": "pred"},
        files={
            "photo": ("photo.jpg", make_jpeg_bytes((3000, 2000)), "image/jpeg")
        }
    )
    assert response.status_code in (200, 303, 307, 308)

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job_id).first()
    filename = photo.filename
    db.close()

    from uploads_utils import MAX_PHOTO_DIMENSION, job_photo_path

    saved_path = job_photo_path(filename)
    assert saved_path is not None

    with PILImage.open(saved_path) as saved_image:
        width, height = saved_image.size

    assert max(width, height) == MAX_PHOTO_DIMENSION
    # pomer strán ostáva zachovaný (3:2)
    assert width / height == pytest.approx(3000 / 2000, rel=0.01)


def test_small_job_photo_is_not_upscaled():
    """Fotka menšia než limit sa nesmie zväčšovať - resize je
    jednosmerný (len zmenšovanie)."""

    db = TestingSessionLocal()
    from models import Job, Customer, JobPhoto

    customer = db.query(Customer).first()
    if customer is None:
        customer = Customer(name="Test")
        db.add(customer)
        db.commit()

    job = Job(title="Test", status="Nová", customer_id=customer.id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    client.post(
        f"/jobs/{job_id}/photos",
        data={"photo_type": "po"},
        files={
            "photo": ("photo.jpg", make_jpeg_bytes((200, 100)), "image/jpeg")
        }
    )

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job_id).first()
    filename = photo.filename
    db.close()

    from uploads_utils import job_photo_path

    with PILImage.open(job_photo_path(filename)) as saved_image:
        assert saved_image.size == (200, 100)


def test_job_photo_resize_fixes_exif_rotation():
    """Fotka z mobilu otočená len cez EXIF (pixely na šírku, EXIF
    hovorí "otoč o 90°") sa má po uložení fyzicky otočiť - inak by
    v prehliadačoch/PDF, ktoré EXIF ignorujú, bola nahor nohami."""

    buffer = io.BytesIO()
    # pixely 200x100 (na šírku), ale EXIF Orientation=6 = "otoč o 90°
    # v smere hodinových ručičiek" -> zobrazená by mala byť 100x200.
    img = PILImage.new("RGB", (200, 100), (10, 20, 30))
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation tag
    img.save(buffer, format="JPEG", exif=exif)

    db = TestingSessionLocal()
    from models import Job, Customer, JobPhoto

    customer = db.query(Customer).first()
    if customer is None:
        customer = Customer(name="Test")
        db.add(customer)
        db.commit()

    job = Job(title="Test", status="Nová", customer_id=customer.id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    client.post(
        f"/jobs/{job_id}/photos",
        data={"photo_type": "pred"},
        files={"photo": ("photo.jpg", buffer.getvalue(), "image/jpeg")}
    )

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job_id).first()
    filename = photo.filename
    db.close()

    from uploads_utils import job_photo_path

    with PILImage.open(job_photo_path(filename)) as saved_image:
        # po fyzickom otočení má byť už "na výšku" a bez EXIF
        # orientácie (exif_transpose ju odstráni, keď ju už aplikoval)
        assert saved_image.size == (100, 200)
        saved_exif = saved_image.getexif()
        assert saved_exif.get(0x0112) in (None, 1)


def test_job_photo_png_stays_png_after_resize():
    """PNG fotka (napr. screenshot priložený k zákazke) má po
    zmenšení ostať PNG, nie sa nenápadne premeniť na JPEG."""

    db = TestingSessionLocal()
    from models import Job, Customer, JobPhoto

    customer = db.query(Customer).first()
    if customer is None:
        customer = Customer(name="Test")
        db.add(customer)
        db.commit()

    job = Job(title="Test", status="Nová", customer_id=customer.id)
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()

    client.post(
        f"/jobs/{job_id}/photos",
        data={"photo_type": "pred"},
        files={
            "photo": ("photo.png", make_png_bytes(size=(2000, 1000)), "image/png")
        }
    )

    db = TestingSessionLocal()
    photo = db.query(JobPhoto).filter(JobPhoto.job_id == job_id).first()
    filename = photo.filename
    db.close()

    from uploads_utils import MAX_PHOTO_DIMENSION, job_photo_path

    saved_path = job_photo_path(filename)
    assert saved_path.suffix == ".png"

    with PILImage.open(saved_path) as saved_image:
        assert saved_image.format == "PNG"
        assert max(saved_image.size) == MAX_PHOTO_DIMENSION
