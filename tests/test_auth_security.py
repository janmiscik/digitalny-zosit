"""
Systematické bezpečnostné testy prihlasovania.

Na rozdiel od tests/test_auth.py, ktorý overuje základné prihlásenie/
odhlásenie a rate limiting, tento súbor sa sústredí na 4 konkrétne veci:

1. Prístup po odhlásení (logout) - viacero chránených stránok, nie len "/"
2. Priamy prístup na chránenú URL úplne bez session cookie
3. Manipulácia so session cookie (sfalšovaná / upravená / cudzím kľúčom)
4. Systematická kontrola, že VŠETKY chránené routy appky sa správajú
   konzistentne (nie len namátkovo "/" a "/customers")

Každý test používa VLASTNÚ, izolovanú inštanciu TestClient (nie zdieľaný
"client" z test_auth.py), aby výsledok nezávisel od poradia spúšťania
testov a od toho, či je v zdieľanom klientovi už nastavená cookie.
"""

import os
import sys
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import auth
from auth import hash_password
from database import Base, get_db
from main import app


# =========================================
# TESTOVACIE HESLO A DB (rovnaký princíp ako v test_auth.py)
# =========================================

TEST_PASSWORD = "tajne-heslo-123"

os.environ["ADMIN_PASSWORD_HASH"] = hash_password(TEST_PASSWORD)

auth.ADMIN_PASSWORD_HASH = os.environ["ADMIN_PASSWORD_HASH"]

import routers.auth as auth_router_module
auth_router_module.ADMIN_PASSWORD_HASH = os.environ["ADMIN_PASSWORD_HASH"]


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


def override_get_db():

    db = TestingSessionLocal()

    try:
        yield db

    finally:
        db.close()


Base.metadata.create_all(bind=test_engine)

app.dependency_overrides[get_db] = override_get_db


def fresh_client() -> TestClient:
    """Nová, úplne izolovaná inštancia klienta - žiadne cookies zdieľané
    s inými testami ani medzi sebou."""

    return TestClient(app)


def login(client: TestClient) -> None:

    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD
        },
        follow_redirects=False
    )

    assert response.status_code == 303


# =========================================
# VŠETKY CHRÁNENÉ ROUTY APPKY
#
# Zoznam vychádza priamo z routers/*.py a main.py - každá GET/POST routa,
# ktorá má Depends(require_login_page) alebo Depends(require_login_api).
# Ak niekto v appke pridá novú chránenú routu a zabudne na Depends,
# tento test na to sám neupozorní (to nevie odhaliť žiadny black-box
# test) - ale ak routu OMYLOM vynechá z Depends a pridá ju sem, tak áno.
# =========================================

PAGE_ROUTES_GET = [
    "/",
    "/uploads/logo.png",
    "/settings",
    "/zakaznici",
    "/customers/1",
    "/customers/1/edit",
    "/faktury",
    "/customers/1/invoices/new",
    "/invoices/1",
    "/invoices/1/edit",
    "/invoices/1/pdf",
    "/invoices/1/peppol-xml",
    "/zakazky",
    "/jobs/1/edit",
]

PAGE_ROUTES_POST = [
    "/settings",
    "/customers",
    "/customers/1/edit",
    "/customers/1/invoices",
    "/invoices/1/edit",
    "/invoices/1/delete",
    "/invoices/1/status",
    "/jobs",
    "/jobs/1/edit",
    "/jobs/1/status",
]

API_ROUTES_GET = [
    "/customers",
    "/jobs",
    "/invoices",
]


# =========================================
# 2. PRIAMY PRÍSTUP BEZ AKÉHOKOĽVEK SESSION COOKIE
# =========================================

@pytest.mark.parametrize("path", PAGE_ROUTES_GET)
def test_page_route_without_any_cookie_redirects_to_login(path):

    response = fresh_client().get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("path", PAGE_ROUTES_POST)
def test_page_route_post_without_any_cookie_redirects_to_login(path):

    # Zámerne posielame prázdne telo - dependency na prihlásenie sa
    # vyhodnotí PRED validáciou formulárových polí, takže aj bez dát
    # dostaneme redirect na login, nie 422.
    response = fresh_client().post(path, data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize("path", API_ROUTES_GET)
def test_api_route_without_any_cookie_returns_401(path):

    response = fresh_client().get(path)

    assert response.status_code == 401


# =========================================
# 1. PRÍSTUP PO ODHLÁSENÍ (LOGOUT)
# =========================================

def test_logout_blocks_access_to_all_page_routes():

    client = fresh_client()
    login(client)

    # over si, že prihlásenie naozaj funguje, inak by bol test nezmyselný
    assert client.get("/").status_code == 200

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"

    for path in PAGE_ROUTES_GET:

        response = client.get(path, follow_redirects=False)

        assert response.status_code == 303, (
            f"Po odhlásení by {path} malo presmerovať na /login, "
            f"ale vrátilo {response.status_code}"
        )
        assert response.headers["location"] == "/login"


def test_logout_blocks_access_to_api_routes():

    client = fresh_client()
    login(client)

    assert client.get("/customers").status_code == 200

    client.post("/logout")

    for path in API_ROUTES_GET:

        response = client.get(path)

        assert response.status_code == 401, (
            f"Po odhlásení by {path} malo vrátiť 401, "
            f"ale vrátilo {response.status_code}"
        )


# =========================================
# 3. MANIPULÁCIA SO SESSION COOKIE
# =========================================

def test_tampered_session_cookie_is_rejected():

    client = fresh_client()
    login(client)

    valid_cookie = client.cookies.get("session")
    assert valid_cookie is not None

    # over si najprv, že platná cookie naozaj funguje
    assert client.get("/").status_code == 200

    # Zmeníme znak niekde v STREDE cookie (nie posledný znak) - posledný
    # base64 znak niekedy obsahuje len "padding" bity, ktoré sa pri
    # dekódovaní ignorujú, takže zmena posledného znaku by príležitostne
    # (nedeterministicky) dekódovala na rovnaké bajty ako originál a test
    # by bol flaky. Zmena znaku v strede vždy skutočne zmení dáta.
    middle_index = len(valid_cookie) // 2
    middle_char = valid_cookie[middle_index]
    replacement = "X" if middle_char != "X" else "Y"

    tampered_cookie = (
        valid_cookie[:middle_index]
        + replacement
        + valid_cookie[middle_index + 1:]
    )

    tampered_client = fresh_client()
    tampered_client.cookies.set("session", tampered_cookie)

    response = tampered_client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_random_session_cookie_is_rejected():

    client = fresh_client()
    client.cookies.set(
        "session",
        "eyJ1c2VyIjogImFkbWluIn0.FAKEFAKEFAKE.notARealSignature123"
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_session_cookie_signed_with_wrong_secret_is_rejected():

    import itsdangerous

    forged_serializer = itsdangerous.URLSafeTimedSerializer(
        "utocnikov-uplne-iny-tajny-kluc"
    )

    forged_cookie = forged_serializer.dumps({"user": "testadmin"})

    client = fresh_client()
    client.cookies.set("session", forged_cookie)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_empty_session_cookie_is_rejected():

    client = fresh_client()
    client.cookies.set("session", "")

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# =========================================
# 4. SYSTEMATICKÁ KONZISTENCIA MEDZI ROUTAMI
# =========================================

def test_all_page_routes_use_consistent_redirect_target():
    """Všetky chránené 'page' routy musia presmerovať na presne rovnaké
    miesto ("/login"), nie na rôzne varianty (napr. "/login/",
    "login", s trailing slash a pod.)."""

    redirect_targets = {
        path: fresh_client().get(path, follow_redirects=False).headers.get("location")
        for path in PAGE_ROUTES_GET
    }

    unique_targets = set(redirect_targets.values())

    assert unique_targets == {"/login"}, (
        f"Nekonzistentné redirect ciele naprieč routami: {redirect_targets}"
    )


def test_all_api_routes_return_same_status_code():
    """Všetky chránené 'api' routy musia bez prihlásenia vracať presne
    rovnaký status kód (401), nie niektoré 401 a iné 403/404."""

    status_codes = {
        path: fresh_client().get(path).status_code
        for path in API_ROUTES_GET
    }

    assert set(status_codes.values()) == {401}, (
        f"Nekonzistentné status kódy naprieč API routami: {status_codes}"
    )
