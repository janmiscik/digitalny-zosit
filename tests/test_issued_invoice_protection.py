"""Vystavenú faktúru nemožno zmazať ani cez cascade."""

import os
from datetime import date

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import Customer, Invoice


@pytest.fixture
def db():

    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()

    yield session

    session.close()


def make(db, status, number="2026001"):

    customer = Customer(name="Z")
    db.add(customer)
    db.flush()
    invoice = Invoice(customer_id=customer.id, invoice_number=number, status=status,
                      issue_date=date(2026, 1, 1), due_date=date(2026, 1, 15))
    db.add(invoice)
    db.commit()

    return customer, invoice


@pytest.mark.parametrize("status", ["Odoslaná", "Uhradená", "Stornovaná"])
def test_issued_invoice_cannot_be_deleted(db, status):

    _, invoice = make(db, status)

    db.delete(invoice)

    with pytest.raises(ValueError, match="nemožno zmazať"):
        db.commit()

    db.rollback()
    assert db.query(Invoice).count() == 1


def test_deleting_customer_with_issued_invoice_is_refused(db):

    customer, _ = make(db, "Odoslaná")

    db.delete(customer)

    with pytest.raises(ValueError):
        db.commit()

    db.rollback()
    assert db.query(Invoice).count() == 1


def test_draft_invoice_can_be_deleted(db):

    _, invoice = make(db, "Návrh")

    db.delete(invoice)
    db.commit()

    assert db.query(Invoice).count() == 0
