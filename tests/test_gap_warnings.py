"""Medzery v číslovaní: upozornenie s preverením a dôvodom (nie chyba integrity)."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import tenancy
from main import app
from tests.test_multi_tenant import _csrf_token, isolated_tenancy, register  # noqa: F401


def invoice_form(token):

    return {
        "csrf_token": token,
        "issue_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=14)).isoformat(),
        "description": "Práca",
        "quantity": "1",
        "unit": "ks",
        "unit_price": "10.00",
        "vat_rate": "0",
    }


@pytest.fixture
def client():
    """Konto s faktúrami 2 a 3 (číslo 1 zmazané) - medzera v rade."""

    http = TestClient(app)
    assert register(http, "firma").status_code == 303

    token = _csrf_token(http, "/zakaznici")
    http.post("/customers", data={"csrf_token": token, "name": "Zakaznik"}, follow_redirects=False)

    ids = []

    for _ in range(3):

        response = http.post("/customers/1/invoices", data=invoice_form(token), follow_redirects=False)
        assert response.status_code == 303, response.text
        ids.append(int(re.search(r"/invoices/(\d+)", response.headers["location"]).group(1)))

    deleted = http.post(f"/invoices/{ids[0]}/delete", data={"csrf_token": token}, follow_redirects=False)
    assert deleted.status_code in (200, 303)

    return http


def missing_number(client):

    page = client.get("/settings/integrity-check").text
    match = re.search(r'name="number" value="(\d+)"', page)

    return page, (match.group(1) if match else None)


def ack(client, number, note):

    return client.post(
        "/settings/integrity-check/gap-ack",
        data={"csrf_token": _csrf_token(client, "/settings"), "number": number, "note": note},
        follow_redirects=False,
    )


def test_gap_is_shown_as_warning_not_as_error(client):

    page, number = missing_number(client)

    assert number is not None
    assert "Upozornenie: medzery v číslovaní" in page
    assert "Databáza a súbory na disku si zodpovedajú" in page
    assert "Preverené" in page


def test_acknowledging_gap_removes_warning_and_keeps_reason(client):

    _, number = missing_number(client)

    response = ack(client, number, "zmazaný návrh, nikdy nevystavený")

    assert response.status_code == 303

    page, still_open = missing_number(client)

    assert still_open is None
    assert "Upozornenie: medzery" not in page
    assert "Preverené medzery (1)" in page
    assert "zmazaný návrh, nikdy nevystavený" in page


def test_acknowledgement_is_written_to_audit_log(client):

    _, number = missing_number(client)
    ack(client, number, "dôvod medzery")

    audit = client.get("/audit-log").text

    assert "integrity.gap_acknowledged" in audit or number in audit


@pytest.mark.parametrize("note", ["", "ab", "x" * 201, "a|b", "dva\nriadky"])
def test_invalid_reason_is_rejected(client, note):

    _, number = missing_number(client)

    assert ack(client, number, note).status_code == 422
    assert missing_number(client)[1] == number


def test_cannot_acknowledge_number_that_is_not_missing(client):

    assert ack(client, "20269999", "vymyslené číslo").status_code == 404


def test_new_gap_after_acknowledgement_is_still_reported(client):

    _, number = missing_number(client)
    ack(client, number, "prvá medzera")

    token = _csrf_token(client, "/zakaznici")
    response = client.post("/customers/1/invoices", data=invoice_form(token), follow_redirects=False)
    new_id = int(re.search(r"/invoices/(\d+)", response.headers["location"]).group(1))
    client.post("/customers/1/invoices", data=invoice_form(token), follow_redirects=False)
    client.post(f"/invoices/{new_id}/delete", data={"csrf_token": token}, follow_redirects=False)

    page, new_number = missing_number(client)

    assert new_number is not None and new_number != number


def test_other_account_cannot_see_or_use_acknowledgements(client):

    _, number = missing_number(client)
    ack(client, number, "poznámka konta firma")

    other = TestClient(app)
    assert register(other, "druha").status_code == 303

    page = other.get("/settings/integrity-check").text

    assert "poznámka konta firma" not in page
    assert ack(other, number, "cudzie číslo").status_code == 404
