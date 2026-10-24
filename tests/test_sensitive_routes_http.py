"""Skutočné HTTP požiadavky na citlivé trasy (nie len kontrola závislostí v definícii)."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest
from fastapi.testclient import TestClient

from main import app
from tests.test_multi_tenant import _csrf_token, isolated_tenancy, register  # noqa: F401

SENSITIVE_GET = ["/settings", "/settings/backups/x.zip", "/export", "/audit-log", "/pripomienky", "/uploads/x.png"]
SENSITIVE_POST = ["/settings/password", "/settings/backups/restore", "/invoices/1/delete", "/invoices/1/status", "/invoices/1/credit-note"]


@pytest.mark.parametrize("path", SENSITIVE_GET)
def test_anonymous_get_is_redirected_to_login(path):

    response = TestClient(app).get(path, follow_redirects=False)

    assert response.status_code in (303, 401, 404)
    assert response.status_code != 303 or response.headers["location"] == "/login"


@pytest.mark.parametrize("path", SENSITIVE_POST)
def test_anonymous_post_never_succeeds(path):

    response = TestClient(app).post(path, data={}, follow_redirects=False)

    assert response.status_code in (303, 401, 403, 404, 405, 422)
    assert response.status_code != 200


@pytest.mark.parametrize("path", [p for p in SENSITIVE_POST if p != "/settings/backups/restore"])
def test_logged_in_post_without_csrf_token_is_403(isolated_tenancy, path):

    client = TestClient(app)
    assert register(client, "firma").status_code == 303

    assert client.post(path, data={}, follow_redirects=False).status_code == 403


def test_wrong_current_password_is_refused_over_http(isolated_tenancy):

    client = TestClient(app)
    assert register(client, "firma").status_code == 303
    token = _csrf_token(client, "/settings")

    response = client.post("/settings/password", data={
        "csrf_token": token, "current_password": "zle-heslo-zle",
        "new_password": "Uplne-Nove-Heslo-7", "new_password_confirm": "Uplne-Nove-Heslo-7",
    }, follow_redirects=False)

    assert response.status_code == 403


def test_there_is_no_route_that_deletes_customers_or_jobs():

    from tests.test_route_policy import all_routes

    for route in all_routes():

        if route.methods - {"GET", "HEAD", "OPTIONS"}:

            path = route.path

            assert not (path.startswith("/customers/{customer_id}") and path.endswith("/delete") and "notes" not in path), path
            assert not (path.startswith("/jobs/{job_id}/delete")), path
