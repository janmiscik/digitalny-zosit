"""Číslovanie dokladov: číselné poradie a hľadanie medzier v rade."""

import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from integrity_check import check_integrity, find_number_gaps
from invoice_utils import (
    next_credit_note_number,
    next_invoice_number,
    next_proforma_number,
    next_quote_number,
)
from models import Customer, Invoice, Quote


@pytest.fixture
def db():

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    session.add(Customer(name="Z"))
    session.commit()

    yield session

    session.close()


def add_invoice(db, number):

    db.add(Invoice(invoice_number=number, customer_id=1, status="Návrh",
                   issue_date=date(2026, 1, 1), due_date=date(2026, 1, 15)))
    db.commit()


def add_quote(db, number):

    db.add(Quote(quote_number=number, customer_id=1, status="Návrh",
                 issue_date=date(2026, 1, 1), valid_until=date(2026, 2, 1)))
    db.commit()


# ---------- ďalšie číslo ----------

def test_first_numbers_of_each_series(db):

    assert next_invoice_number(db, 2026) == "2026001"
    assert next_proforma_number(db, 2026) == "ZF2026001"
    assert next_credit_note_number(db, 2026) == "DP2026001"
    assert next_quote_number(db, 2026) == "CP2026001"


def test_series_are_independent_per_type_and_year(db):

    add_invoice(db, "2026001")
    add_invoice(db, "ZF2026007")
    add_invoice(db, "2025120")

    assert next_invoice_number(db, 2026) == "2026002"
    assert next_proforma_number(db, 2026) == "ZF2026008"
    assert next_credit_note_number(db, 2026) == "DP2026001"
    assert next_invoice_number(db, 2025) == "2025121"


def test_numbering_continues_correctly_past_999(db):

    add_invoice(db, "2026999")

    assert next_invoice_number(db, 2026) == "20261000"

    add_invoice(db, "20261000")

    assert next_invoice_number(db, 2026) == "20261001"       # textové zoradenie by vrátilo 20261000


def test_next_number_uses_highest_even_with_gaps(db):

    add_invoice(db, "2026001")
    add_invoice(db, "2026005")

    assert next_invoice_number(db, 2026) == "2026006"


def test_non_numeric_suffix_is_ignored(db):

    add_invoice(db, "2026ABC")
    add_invoice(db, "2026003")

    assert next_invoice_number(db, 2026) == "2026004"


# ---------- medzery ----------

def test_no_gaps_in_continuous_series():

    assert find_number_gaps(["2026001", "2026002", "2026003", "ZF2026001", "CP2026001"]) == []


def test_gaps_are_listed_per_series_and_year():

    issues = find_number_gaps(["2026001", "2026004", "2025001", "2025003", "DP2026002", "ZF2026001"])

    result = {issue.series: issue.missing for issue in issues}

    assert result == {
        "Faktúry 2025": ["2025002"],
        "Faktúry 2026": ["2026002", "2026003"],
        "Dobropisy 2026": ["DP2026001"],
    }


def test_hand_typed_numbers_are_ignored():

    assert find_number_gaps(["FA-1", "2026-0005", "", None, "abc"]) == []


def test_gap_beyond_999():

    issues = find_number_gaps(["2026998", "2026999", "20261001"] + [f"2026{n:03d}" for n in range(1, 998)])

    assert [issue.missing for issue in issues] == [["20261000"]]


def test_check_integrity_reports_gaps_for_invoices_and_quotes(db, patch_tenant_paths, tmp_path):

    patch_tenant_paths(uploads=tmp_path / 'uploads')

    for number in ("2026001", "2026003"):
        add_invoice(db, number)

    add_quote(db, "CP2026002")

    report = check_integrity(db)

    assert report.is_clean is False
    assert {issue.series: issue.missing for issue in report.number_gaps} == {
        "Faktúry 2026": ["2026002"],
        "Cenové ponuky 2026": ["CP2026001"],
    }


def test_check_integrity_clean_when_continuous(db, patch_tenant_paths, tmp_path):

    patch_tenant_paths(uploads=tmp_path / 'uploads')

    for number in ("2026001", "2026002"):
        add_invoice(db, number)

    assert check_integrity(db).number_gaps == []
