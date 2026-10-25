"""
Izolácia kont: konto B nesmie vidieť, meniť ani sťahovať NIČ z konta A.

Dve skutočné kontá (reálne prihlásenie, reálne databázové súbory). Konto A
má zákazníka, zákazku s nákladom, faktúru, ponuku, logo, fotku zákazky a
zálohu. Potom sa konto B pokúsi o KAŽDÚ trasu appky, ktorá berie ID alebo
názov súboru, s presne tými hodnotami, ktoré patria kontu A, a skontroluje
sa, že (1) nič z A sa nezobrazilo, (2) A ostalo nezmenené, (3) B nič nevzniklo.
"""

import os
import re
from datetime import date, datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import tenancy
from main import app
from models import Customer, Invoice, Job, JobCost, JobPhoto, Quote
from tests.test_multi_tenant import _csrf_token, isolated_tenancy, register  # noqa: F401

SLUG_A = "firmaa"
SLUG_B = "firmab"
SECRET_NAME = "TAJNY-ZAKAZNIK-A-s-r-o"
SECRET_JOB = "TAJNA-ZAKAZKA-A"
SECRET_ITEM = "TAJNA-POLOZKA-A"
MARKERS = (SECRET_NAME, SECRET_JOB, SECRET_ITEM)

PNG_A = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cf5000000301010018dd8db00000000049454e44ae426082"
)


def location_id(response, prefix):

    assert "location" in response.headers, (response.status_code, response.text[:300])

    match = re.match(rf"{prefix}/(\d+)", response.headers["location"])
    assert match, response.headers.get("location")

    return int(match.group(1))


def account_session(slug):

    tenancy.set_current_tenant(slug)

    return sessionmaker(bind=tenancy.get_account_engine(slug))()


@pytest.fixture
def world():
    """Konto A s dátami a súbormi, konto B prázdne. Vráti klientov a ID."""

    a = TestClient(app)
    b = TestClient(app)

    assert register(a, SLUG_A).status_code == 303
    assert register(b, SLUG_B).status_code == 303

    token = _csrf_token(a, "/zakaznici")

    response = a.post("/customers", data={"csrf_token": token, "name": SECRET_NAME}, follow_redirects=False)
    assert response.status_code == 303
    db = account_session(SLUG_A)
    customer_id = db.query(Customer).filter(Customer.name == SECRET_NAME).one().id
    db.close()

    response = a.post(
        "/jobs",
        data={"csrf_token": token, "title": SECRET_JOB, "customer_id": customer_id},
        follow_redirects=False,
    )
    assert response.status_code == 303

    items = {
        "csrf_token": token,
        "issue_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=14)).isoformat(),
        "description": SECRET_ITEM,
        "quantity": "1",
        "unit": "ks",
        "unit_price": "100.00",
        "vat_rate": "0",
    }

    response = a.post(f"/customers/{customer_id}/invoices", data=items, follow_redirects=False)
    invoice_id = location_id(response, "/invoices")

    quote_items = {**items, "valid_until": (date.today() + timedelta(days=30)).isoformat()}
    response = a.post(f"/customers/{customer_id}/quotes", data=quote_items, follow_redirects=False)
    quote_id = location_id(response, "/quotes")

    db = account_session(SLUG_A)
    job = db.query(Job).first()
    job_id = job.id

    cost = JobCost(job_id=job_id, description="naklad A", amount=10, cost_date=date.today())
    db.add(cost)
    photo = JobPhoto(job_id=job_id, filename="fotkaA.png", uploaded_at=datetime.now(timezone.utc))
    db.add(photo)
    db.commit()
    cost_id, photo_id = cost.id, photo.id
    db.close()

    tenancy.set_current_tenant(SLUG_A)
    uploads = tenancy.account_uploads_dir(SLUG_A)
    (uploads / "job_photos").mkdir(parents=True, exist_ok=True)
    (uploads / "job_photos" / "fotkaA.png").write_bytes(PNG_A)
    (uploads / "logo.png").write_bytes(PNG_A)

    response = a.post("/settings/backups/run", data={"csrf_token": _csrf_token(a, "/settings")},
                      follow_redirects=False)
    assert response.status_code == 303

    backups = [p.name for p in tenancy.account_backups_dir(SLUG_A).glob("*.zip")]
    assert backups

    return {
        "a": a, "b": b,
        "ids": {"customer_id": customer_id, "job_id": job_id, "invoice_id": invoice_id,
                "quote_id": quote_id, "cost_id": cost_id, "photo_id": photo_id, "note_id": 1},
        "filename": "fotkaA.png",
        "backup": backups[0],
    }


def a_counts():

    db = account_session(SLUG_A)

    try:
        return {m.__name__: db.query(m).count() for m in (Customer, Job, Invoice, Quote, JobCost, JobPhoto)}

    finally:
        db.close()


def fill_path(path, world_):

    def value(match):

        name = match.group(1)

        if name == "filename":
            return world_["filename"]

        if name == "name":
            return world_["backup"]

        if name == "ico":
            return "12345678"

        if name in world_["ids"]:
            return str(world_["ids"][name])

        return "1"

    return re.sub(r"\{(\w+)(?::[^}]*)?\}", value, path)


def all_api_routes():
    """Trasy appky vrátane tých z include_router (nové FastAPI ich obaľuje)."""

    for route in app.routes:

        if isinstance(route, APIRoute):
            yield route

        elif hasattr(route, "original_router"):

            prefix = route.include_context.prefix

            for inner in route.original_router.routes:

                if isinstance(inner, APIRoute):
                    yield type("R", (), {"path": prefix + inner.path, "methods": inner.methods})()


def sensitive_routes():
    """Všetky trasy s parametrom v ceste (ID, názov súboru), okrem anonymných."""

    skip = ("/reset-password/", "/static", "/docs", "/openapi", "/redoc", "/lookup-ico")

    for route in all_api_routes():

        if "{" not in route.path:
            continue

        if any(part in route.path for part in skip):
            continue

        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path


def test_route_sweep_finds_the_expected_surface():
    """Poistka, aby sa sweep nezúžil bez povšimnutia (napr. po zmene prefixov)."""

    routes = list(sensitive_routes())

    assert len(routes) >= 40
    assert ("GET", "/jobs/photos/{filename}") in routes
    assert ("GET", "/settings/backups/{name}") in routes
    assert ("GET", "/uploads/{filename}") in routes


def test_account_b_cannot_read_or_change_anything_of_account_a(world):

    b = world["b"]
    before = a_counts()
    token = _csrf_token(b, "/settings")

    problems = []

    for method, path in sensitive_routes():

        url = fill_path(path, world)

        if method == "GET":
            response = b.get(url, follow_redirects=False)

        else:
            response = b.request(method, url, data={"csrf_token": token}, follow_redirects=False)

        leaked = [m for m in MARKERS if m in response.text]

        if leaked:
            problems.append(f"{method} {url}: unikli dáta {leaked}")

        if method == "GET" and response.status_code == 200:
            problems.append(f"{method} {url}: vrátilo 200 cudziemu kontu")

        if response.headers.get("content-type", "").startswith(("image/", "application/zip")) \
                and response.status_code == 200:
            problems.append(f"{method} {url}: odovzdalo cudzí súbor")

    assert not problems, "\n".join(problems)

    # A je nezmenené (nič sa nezmazalo ani nepridalo)
    assert a_counts() == before

    # B nič nevzniklo
    db = account_session(SLUG_B)

    try:
        for model in (Customer, Job, Invoice, Quote, JobCost, JobPhoto):
            assert db.query(model).count() == 0, model.__name__

    finally:
        db.close()


def test_account_b_lists_and_exports_contain_nothing_from_a(world):

    b = world["b"]

    for path in ("/zakaznici", "/faktury", "/zakazky", "/ponuky", "/kalendar", "/pripomienky",
                 "/audit-log", "/settings", "/settings/integrity-check", "/export",
                 "/export/faktury.csv", "/export/naklady.csv", "/export/uctovnik.xlsx",
                 "/export/faktury-pdf.zip", "/"):

        response = b.get(path, follow_redirects=True)

        text = response.text if response.headers.get("content-type", "").startswith(("text", "application/json")) \
            else response.content.decode("latin-1")

        assert not [m for m in MARKERS if m in text], path


def test_owner_still_reaches_own_files_and_data(world):
    """Kontrolný pár k testom vyššie: A svoje veci vidí (inak by sweep nič nedokazoval)."""

    a = world["a"]
    ids = world["ids"]

    assert a.get(f"/invoices/{ids['invoice_id']}").status_code == 200
    assert a.get(f"/quotes/{ids['quote_id']}").status_code == 200
    assert a.get(f"/customers/{ids['customer_id']}").status_code == 200
    assert a.get(f"/jobs/photos/{world['filename']}").content == PNG_A
    assert a.get("/uploads/logo.png").content == PNG_A
    assert a.get(f"/settings/backups/{world['backup']}").status_code == 200


def test_same_filename_in_both_accounts_serves_each_its_own_content(world):

    b = world["b"]
    other = b"\x89PNG-iny-obsah-B"

    tenancy.set_current_tenant(SLUG_B)
    uploads = tenancy.account_uploads_dir(SLUG_B)
    (uploads / "job_photos").mkdir(parents=True, exist_ok=True)
    (uploads / "job_photos" / world["filename"]).write_bytes(other)
    (uploads / "logo.png").write_bytes(other)

    assert b.get(f"/jobs/photos/{world['filename']}").content == other
    assert b.get("/uploads/logo.png").content == other
    assert world["a"].get(f"/jobs/photos/{world['filename']}").content == PNG_A
    assert world["a"].get("/uploads/logo.png").content == PNG_A


@pytest.mark.parametrize("evil", [
    "../../firmaa/uploads/logo.png",
    "..%2F..%2Ffirmaa%2Fuploads%2Flogo.png",
    "%2e%2e/%2e%2e/firmaa/uploads/logo.png",
    "....//....//firmaa/uploads/logo.png",
])
def test_path_traversal_into_other_account_is_refused(world, evil):

    b = world["b"]

    for prefix in ("/jobs/photos/", "/uploads/", "/settings/backups/"):

        response = b.get(prefix + evil, follow_redirects=False)

        assert response.status_code in (404, 422, 307, 308, 400), (prefix + evil, response.status_code)
        assert response.content != PNG_A


def test_backup_restore_from_account_a_zip_does_not_touch_other_account(world):
    """Obnova v B zo zálohy A neovplyvní A (a dáta by do B prišli len zámerným nahraním)."""

    a, b = world["a"], world["b"]
    before = a_counts()

    path = tenancy.account_backups_dir(SLUG_A) / world["backup"]
    token = _csrf_token(b, "/settings")

    response = b.post(
        "/settings/restore",
        data={"csrf_token": token},
        files={"backup_file": ("zaloha.zip", path.read_bytes(), "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert a_counts() == before
    assert a.get(f"/invoices/{world['ids']['invoice_id']}").status_code == 200


# =========================================
# POSILNENÝ SWEEP: platné formuláre, odtlačok celého konta A
# =========================================
# Prvý sweep posiela len csrf_token, takže veľa trás skončí na 422 ešte pred
# kontrolou vlastníka. Tu B posiela platné hodnoty všetkých polí, ktoré appka
# pozná (handler sa teda naozaj vykoná), a kontroluje sa, že CELÁ databáza,
# súbory a zálohy konta A ostali bajt po bajte rovnaké.

import hashlib


def account_fingerprint(slug):

    import sqlite3

    root = tenancy.ACCOUNTS_DATA_DIR / slug
    digest = hashlib.sha256()

    connection = sqlite3.connect(str(root / "database.db")) if (root / "database.db").exists() else None

    if connection is not None:

        try:
            for line in connection.iterdump():
                digest.update(line.encode("utf-8"))

        finally:
            connection.close()

    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(("-wal", "-shm", ".db"))):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())

    return digest.hexdigest()


def valid_form_payload(world_, token):

    ids = world_["ids"]
    today = date.today().isoformat()

    return {
        "csrf_token": token,
        "name": "B-utok", "title": "B-utok", "description": "B-utok", "note": "B-utok", "text": "B-utok",
        "reason": "B-utok dôvod", "category": "ine", "status": "Uhradená",
        "address": "Ulica 1", "city": "Mesto", "zip_code": "81101", "country_code": "SK",
        "email": "b@example.com", "phone": "+421900000000", "website": "https://example.com",
        "ico": "12345008", "dic": "", "ic_dph": "", "iban": "SK3112000000198742637541",
        "swift_bic": "TATRSKBX", "number": "FA-B-1", "quantity": "1", "unit": "ks", "unit_price": "1.00",
        "vat_rate": "0", "amount": "1.00", "hours": "1", "hourly_rate": "1",
        "issue_date": today, "due_date": today, "delivery_date": today, "paid_date": today,
        "cost_date": today, "valid_until": today,
        "customer_id": str(ids["customer_id"]), "job_id": str(ids["job_id"]),
        "is_proforma": "false", "is_vat_payer": "true", "remove_logo": "true", "remove_signature": "true",
    }


def test_valid_payload_really_works_for_the_owner(world):
    """Kontrola kontrolóra: ten istý payload majiteľ A prijme (inak by sweep B len naráža na 422)."""

    a = world["a"]
    payload = valid_form_payload(world, _csrf_token(a, "/settings"))
    payload["name"] = "A po zmene"

    response = a.post(f"/customers/{world['ids']['customer_id']}/edit", data=payload, follow_redirects=False)

    assert response.status_code == 303, response.text[:300]


def test_account_b_with_valid_forms_leaves_account_a_byte_identical(world):

    b = world["b"]
    before = account_fingerprint(SLUG_A)
    token = _csrf_token(b, "/settings")
    payload = valid_form_payload(world, token)

    statuses = {}
    problems = []

    for method, path in sensitive_routes():

        if path.startswith("/settings/restore"):
            continue

        url = fill_path(path, world)

        response = b.get(url, follow_redirects=False) if method == "GET" \
            else b.request(method, url, data=payload, follow_redirects=False)

        statuses[(method, path)] = response.status_code

        if response.status_code >= 500:
            problems.append(f"{method} {url}: server error {response.status_code}")

        if [m for m in MARKERS if m in response.text]:
            problems.append(f"{method} {url}: unikli dáta konta A")

    assert not problems, "\n".join(problems)

    # sweep sa musí dostať aj za validáciu, inak nič nedokazuje
    mutating = [code for (method, _), code in statuses.items() if method != "GET"]
    validated = [code for code in mutating if code != 422]

    assert len(validated) >= len(mutating) * 0.6, f"príliš veľa 422: {sorted(set(mutating))}"

    # niektoré zmeny B mohli legálne vzniknúť v B, ale A sa nesmie zmeniť ani o bajt
    assert account_fingerprint(SLUG_A) == before


def test_account_b_actions_are_logged_only_in_b(world):

    from models import AuditLog

    b = world["b"]
    token = _csrf_token(b, "/settings")

    db = account_session(SLUG_A)
    audit_before = db.query(AuditLog).count()
    db.close()

    for method, path in sensitive_routes():

        if path.startswith("/settings/restore"):
            continue

        url = fill_path(path, world)

        if method == "GET":
            b.get(url, follow_redirects=False)

        else:
            b.request(method, url, data=valid_form_payload(world, token), follow_redirects=False)

    db = account_session(SLUG_A)

    try:
        assert db.query(AuditLog).count() == audit_before

    finally:
        db.close()


def test_account_b_routes_without_id_in_path_do_not_touch_account_a(world):
    """Trasy bez ID v ceste (POST /jobs s customer_id konta A, údržba, nastavenia) menia len konto B."""

    b = world["b"]
    before = account_fingerprint(SLUG_A)
    token = _csrf_token(b, "/settings")
    payload = valid_form_payload(world, token)

    response = b.post("/jobs", data=payload, follow_redirects=False)

    assert response.status_code in (303, 400, 404, 422), response.status_code

    db = account_session(SLUG_B)

    try:
        # zákazka v B nesmie ukazovať na zákazníka, ktorý v B neexistuje (cudzie ID z A)
        for job in db.query(Job).all():
            assert db.query(Customer).filter(Customer.id == job.customer_id).count() == 1

    finally:
        db.close()

    for path in ("/settings/backups/run", "/settings/backups/verify", "/settings/integrity-check/gap-ack",
                 "/settings/integrity-check/cleanup", "/settings", "/settings/email"):

        response = b.post(path, data=payload, follow_redirects=False)

        assert response.status_code < 500, (path, response.status_code)

    assert account_fingerprint(SLUG_A) == before
