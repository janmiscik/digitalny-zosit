"""Súbežné vytváranie dokladov: čísla musia byť jedinečné a nikto nesmie dostať chybu."""

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("SESSION_HTTPS_ONLY", "false")

import re
import sqlite3
import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import tenancy
from main import app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import accounts_db
from tests.test_multi_tenant import _csrf_token, register

SLUG = "firma"
WORKERS = 20


def form(token, valid_until=False):

    data = {
        "csrf_token": token,
        "issue_date": date.today().isoformat(),
        "due_date": (date.today() + timedelta(days=14)).isoformat(),
        "description": "Práca",
        "quantity": "1",
        "unit": "ks",
        "unit_price": "10.00",
        "vat_rate": "0",
    }

    if valid_until:
        data["valid_until"] = (date.today() + timedelta(days=30)).isoformat()

    return data


@pytest.fixture(autouse=True)
def isolated_tenancy(tmp_path, monkeypatch):
    """
    Ako v ostatných testoch kont, ale accounts.db je SÚBOR (nie in-memory
    StaticPool s jedným spojením), aby ho vlákna používali ako v produkcii.
    """

    monkeypatch.setattr(tenancy, "ACCOUNTS_DATA_DIR", tmp_path / "accounts")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'accounts.db'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    accounts_db.AccountsBase.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    def override():

        db = Session()

        try:
            yield db

        finally:
            db.close()

    app.dependency_overrides[accounts_db.get_accounts_db] = override
    tenancy._engine_cache.clear()

    yield

    app.dependency_overrides.clear()
    tenancy._engine_cache.clear()
    engine.dispose()


@pytest.fixture
def setup():

    owner = TestClient(app)
    assert register(owner, SLUG).status_code == 303

    token = _csrf_token(owner, "/zakaznici")
    owner.post("/customers", data={"csrf_token": token, "name": "Zakaznik"}, follow_redirects=False)

    conn = sqlite3.connect(str(tenancy.account_database_path(SLUG)))
    customer_id = conn.execute("SELECT id FROM customers").fetchone()[0]
    conn.close()

    return owner, token, customer_id


def run_parallel(owner, token, url, valid_until=False):

    barrier = threading.Barrier(WORKERS)
    results = [None] * WORKERS

    def worker(index):

        client = TestClient(app)
        client.cookies.update(owner.cookies)

        barrier.wait()

        try:
            response = client.post(url, data=form(token, valid_until), follow_redirects=False)
            results[index] = (response.status_code, response.headers.get("location"))

        except BaseException as exc:   # noqa: BLE001
            results[index] = ("exception", repr(exc))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(WORKERS)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=120)

    return results


def numbers(table, column):

    conn = sqlite3.connect(str(tenancy.account_database_path(SLUG)))
    rows = [row[0] for row in conn.execute(f"SELECT {column} FROM {table}")]
    conn.close()

    return rows


def test_parallel_invoice_creation_gives_unique_consecutive_numbers(setup):

    owner, token, customer_id = setup

    results = run_parallel(owner, token, f"/customers/{customer_id}/invoices")

    assert all(status == 303 for status, _ in results), results

    created = numbers("invoices", "invoice_number")

    assert len(created) == WORKERS
    assert len(set(created)) == WORKERS

    suffixes = sorted(int(number[4:]) for number in created)
    assert suffixes == list(range(1, WORKERS + 1))

    # každý redirect ukazuje na inú faktúru
    assert len({location for _, location in results}) == WORKERS


def test_parallel_quote_creation_gives_unique_numbers(setup):

    owner, token, customer_id = setup

    results = run_parallel(owner, token, f"/customers/{customer_id}/quotes", valid_until=True)

    assert all(status == 303 for status, _ in results), results

    created = numbers("quotes", "quote_number")

    assert len(set(created)) == WORKERS == len(created)


def test_parallel_creation_then_next_number_continues_without_gap(setup):

    owner, token, customer_id = setup

    run_parallel(owner, token, f"/customers/{customer_id}/invoices")

    response = owner.post(f"/customers/{customer_id}/invoices", data=form(token), follow_redirects=False)

    assert response.status_code == 303
    assert max(int(n[4:]) for n in numbers("invoices", "invoice_number")) == WORKERS + 1
