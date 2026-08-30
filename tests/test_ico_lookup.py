"""
Testy pre vyhľadávanie firmy podľa IČO (ico_lookup.py + endpoint).

DÔLEŽITÉ: Nikdy netestujeme proti skutočnému ORSF API - httpx.get sa
vždy mockuje. Testy musia byť rýchle, deterministické a nesmú závisieť
od dostupnosti externej služby tretej strany.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx


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
from ico_lookup import lookup_company_by_ico
from main import app


TEST_DATABASE_URL = "sqlite://"

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(bind=test_engine)


def override_login():

    return "testuser"


@pytest.fixture(autouse=True)
def setup_test_database():

    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():

        db = TestingSessionLocal()

        try:
            yield db

        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_login_page] = override_login
    app.dependency_overrides[require_login_api] = override_login

    yield

    app.dependency_overrides.clear()


client = TestClient(app)


def make_orsf_response(json_data: dict, status_code: int = 200) -> MagicMock:

    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data

    return response


ORSF_SAMPLE_VAT_PAYER = {
    "name": "8888 Servis s. r. o.",
    "ico": "55609830",
    "dic": "2122034970",
    "street": "Jenisejská 45A",
    "city": "Košice - mestská časť Nad jazerom",
    "psc": "040 12",
    "statusCode": "active",
    "vatRegistration": {
        "icDph": "SK2122034970"
    }
}

ORSF_SAMPLE_NON_VAT_PAYER = {
    "name": "Malá Firma s. r. o.",
    "ico": "12345678",
    "dic": "1234567890",
    "street": "Hlavná 1",
    "city": "Žilina",
    "psc": "01001",
    "statusCode": "active",
    "vatRegistration": None
}


# =========================================
# UNIT TESTY - lookup_company_by_ico() priamo
# =========================================

def test_lookup_returns_none_for_invalid_ico_format():

    assert lookup_company_by_ico("abc") is None
    assert lookup_company_by_ico("123") is None
    assert lookup_company_by_ico("") is None
    assert lookup_company_by_ico(None) is None


def test_lookup_success_with_vat_payer():

    with patch("ico_lookup.httpx.get") as mock_get:

        mock_get.return_value = make_orsf_response(ORSF_SAMPLE_VAT_PAYER)

        result = lookup_company_by_ico("55609830")

    assert result is not None
    assert result["name"] == "8888 Servis s. r. o."
    assert result["ico"] == "55609830"
    assert result["dic"] == "2122034970"
    assert result["ic_dph"] == "SK2122034970"
    assert "Jenisejská 45A" in result["address"]
    assert "04012" in result["address"]


def test_lookup_success_without_vat_registration():
    """Subjekt, ktorý NIE JE platiteľom DPH, musí mať ic_dph None -
    nesmie sa vymyslieť/odvodiť odniekiaľ inde."""

    with patch("ico_lookup.httpx.get") as mock_get:

        mock_get.return_value = make_orsf_response(ORSF_SAMPLE_NON_VAT_PAYER)

        result = lookup_company_by_ico("12345678")

    assert result is not None
    assert result["ic_dph"] is None
    assert result["dic"] == "1234567890"


def test_lookup_returns_none_on_404():

    with patch("ico_lookup.httpx.get") as mock_get:

        mock_get.return_value = make_orsf_response({}, status_code=404)

        result = lookup_company_by_ico("99999999")

    assert result is None


def test_lookup_returns_none_on_network_error():
    """Výpadok/timeout externej služby sa NIKDY nesmie prejaviť ako
    chyba appky - len ako 'nenašlo sa', nech si používateľ vyplní
    údaje ručne."""

    with patch("ico_lookup.httpx.get") as mock_get:

        mock_get.side_effect = httpx.ConnectTimeout("timeout")

        result = lookup_company_by_ico("55609830")

    assert result is None


def test_lookup_returns_none_on_malformed_json():

    with patch("ico_lookup.httpx.get") as mock_get:

        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")
        mock_get.return_value = response

        result = lookup_company_by_ico("55609830")

    assert result is None


def test_lookup_returns_none_when_name_missing():
    """Ak odpoveď z nejakého dôvodu nemá ani meno firmy, považujeme ju
    za nepoužiteľnú - radšej nič, než napoloprázdny formulár."""

    with patch("ico_lookup.httpx.get") as mock_get:

        mock_get.return_value = make_orsf_response({"ico": "12345678"})

        result = lookup_company_by_ico("12345678")

    assert result is None


def test_lookup_handles_missing_address_fields_gracefully():

    with patch("ico_lookup.httpx.get") as mock_get:

        mock_get.return_value = make_orsf_response({
            "name": "Firma Bez Adresy s. r. o.",
            "ico": "11112222"
        })

        result = lookup_company_by_ico("11112222")

    assert result is not None
    assert result["name"] == "Firma Bez Adresy s. r. o."
    assert result["address"] is None


# =========================================
# ENDPOINT TESTY - GET /customers/lookup-ico/{ico}
# =========================================

def test_lookup_endpoint_requires_login():

    del app.dependency_overrides[require_login_api]

    try:

        response = client.get("/customers/lookup-ico/55609830")

        assert response.status_code == 401

    finally:

        app.dependency_overrides[require_login_api] = override_login


def test_lookup_endpoint_success():

    with patch("routers.customers.lookup_company_by_ico") as mock_lookup:

        mock_lookup.return_value = {
            "name": "8888 Servis s. r. o.",
            "address": "Jenisejská 45A, Košice, 04012",
            "ico": "55609830",
            "dic": "2122034970",
            "ic_dph": "SK2122034970",
        }

        response = client.get("/customers/lookup-ico/55609830")

    assert response.status_code == 200

    data = response.json()
    assert data["name"] == "8888 Servis s. r. o."
    assert data["ic_dph"] == "SK2122034970"


def test_lookup_endpoint_not_found():

    with patch("routers.customers.lookup_company_by_ico") as mock_lookup:

        mock_lookup.return_value = None

        response = client.get("/customers/lookup-ico/00000000")

    assert response.status_code == 404


def test_lookup_endpoint_does_not_shadow_customer_detail_route():
    """Regresný test na routovanie: /customers/lookup-ico/... sa nesmie
    nechtiac vykonávať ako /customers/{customer_id} (a naopak)."""

    # neexistujúci zákazník s číselným ID -> 404 od DETAILU zákazníka,
    # nie od ICO lookupu
    response = client.get("/customers/999999")
    assert response.status_code == 404

    with patch("routers.customers.lookup_company_by_ico") as mock_lookup:

        mock_lookup.return_value = {
            "name": "Test",
            "address": None,
            "ico": "12345678",
            "dic": None,
            "ic_dph": None,
        }

        response = client.get("/customers/lookup-ico/12345678")

    assert response.status_code == 200
