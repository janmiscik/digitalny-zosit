"""Zneplatnenie prihlásení po zmene hesla a zmazaní konta."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

from fastapi.testclient import TestClient

from accounts_models import Account
from main import app
from tests.test_change_password import (  # noqa: F401  (fixture isolated je autouse)
    NEW, OLD, change, csrf, isolated, login, register,
)


def logged_in_second_device(username="firma"):

    client = TestClient(app)
    response = client.post(
        "/login",
        data={"csrf_token": csrf(client, "/login"), "username": username, "password": OLD},
        follow_redirects=False,
    )
    assert response.status_code == 303

    return client


def test_password_change_logs_out_other_devices():

    first = register()
    second = logged_in_second_device()

    assert second.get("/settings", follow_redirects=False).status_code == 200

    assert change(first).status_code in (200, 303)

    # Druhé zariadenie je odhlásené...
    response = second.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    # ...a aktuálne zariadenie, ktoré heslo menilo, ostáva prihlásené.
    assert first.get("/settings", follow_redirects=False).status_code == 200


def test_api_returns_401_for_invalidated_session():

    first = register()
    second = logged_in_second_device()

    change(first)

    assert second.get("/customers/lookup-ico/12345678", follow_redirects=False).status_code == 401


def test_deleted_account_session_is_rejected(isolated):

    client = register()

    assert client.get("/settings", follow_redirects=False).status_code == 200

    db = isolated()
    db.query(Account).delete()
    db.commit()
    db.close()

    response = client.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_session_without_fingerprint_is_rejected(isolated):

    client = register()
    # Cookie vydaná pred zavedením kontroly: zasiahneme priamo do session.
    from itsdangerous import TimestampSigner  # noqa: F401
    import base64, json

    cookie = client.cookies.get("session")
    signer = TimestampSigner(os.environ["SECRET_KEY"])
    data = json.loads(base64.b64decode(signer.unsign(cookie.encode())))
    data.pop("pwv")
    forged = signer.sign(base64.b64encode(json.dumps(data).encode())).decode()
    client.cookies.set("session", forged)

    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_new_password_works_after_change():

    first = register()
    change(first)

    assert login("firma", NEW).status_code == 303
