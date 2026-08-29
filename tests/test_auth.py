import os
import sys
from pathlib import Path


os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)


from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import hash_password, verify_password
from database import Base, get_db
from main import app


# =========================================
# TESTOVACIE HESLO
# =========================================

TEST_PASSWORD = "tajne-heslo-123"

os.environ["ADMIN_PASSWORD_HASH"] = hash_password(TEST_PASSWORD)


# Modul auth.py si ADMIN_PASSWORD_HASH načíta pri importe, takže ho
# po nastavení env premennej ešte musíme prepísať priamo v module.
import auth
auth.ADMIN_PASSWORD_HASH = os.environ["ADMIN_PASSWORD_HASH"]

import routers.auth as auth_router_module
auth_router_module.ADMIN_PASSWORD_HASH = os.environ["ADMIN_PASSWORD_HASH"]


# =========================================
# TEST DATABASE (aby appka pri štarte mala kam zapisovať)
# =========================================

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


client = TestClient(app)


# =========================================
# HASHOVANIE HESLA
# =========================================

def test_password_hash_roundtrip():

    hashed = hash_password("moje-heslo")

    assert verify_password("moje-heslo", hashed) is True
    assert verify_password("zle-heslo", hashed) is False


# =========================================
# PRÍSTUP BEZ PRIHLÁSENIA
# =========================================

def test_home_requires_login():

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_customers_api_requires_login():

    response = client.get("/customers")

    assert response.status_code == 401


# =========================================
# PRIHLÁSENIE
# =========================================

def test_login_wrong_password():

    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": "zle-heslo"
        }
    )

    assert response.status_code == 401


def test_login_success_and_access():

    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # session cookie by mal byť teraz nastavený v kliente
    response = client.get("/")

    assert response.status_code == 200


def test_logout():

    client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD
        }
    )

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303


# =========================================
# RATE LIMITING PRIHLÁSENIA
# =========================================

def test_login_locked_after_too_many_wrong_attempts():

    # vyčistíme stav limitera, nech test nezávisí od poradia iných testov
    auth._failed_login_attempts.clear()
    auth._lockout_until = None

    for _ in range(auth.MAX_LOGIN_ATTEMPTS):

        response = client.post(
            "/login",
            data={
                "username": "testadmin",
                "password": "zle-heslo"
            }
        )

        assert response.status_code == 401

    # ďalší pokus (aj so správnym heslom) musí byť zablokovaný
    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD
        }
    )

    assert response.status_code == 429
    assert "session" not in response.cookies

    auth._failed_login_attempts.clear()
    auth._lockout_until = None


def test_successful_login_resets_lockout_counter():

    auth._failed_login_attempts.clear()
    auth._lockout_until = None

    for _ in range(auth.MAX_LOGIN_ATTEMPTS - 1):

        client.post(
            "/login",
            data={
                "username": "testadmin",
                "password": "zle-heslo"
            }
        )

    response = client.post(
        "/login",
        data={
            "username": "testadmin",
            "password": TEST_PASSWORD
        },
        follow_redirects=False
    )

    assert response.status_code == 303
    assert len(auth._failed_login_attempts) == 0

    auth._failed_login_attempts.clear()
    auth._lockout_until = None
