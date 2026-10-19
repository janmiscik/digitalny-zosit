"""Účtovná správnosť: zaokrúhľovanie DPH, rekapitulácia, súlad s Peppol BR-CO-17."""

import os
import sys
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from invoice_utils import calculate_invoice_totals
from peppol_validation import validate_peppol_invoice_xml
from peppol_xml import generate_peppol_xml
from test_peppol_validation import make_company, make_invoice, make_item

D = Decimal


def item(price, vat=23, qty="1"):

    return make_item(unit_price=D(price), vat_rate=vat, quantity=D(qty))


@pytest.mark.parametrize("lines, base, vat", [
    ([item("0.10")] * 3, "0.30", "0.07"),            # po riadkoch by bolo 0,06
    ([item("0.15")] * 10, "1.50", "0.35"),           # po riadkoch by bolo 0,30
    ([item("1.99")] * 5, "9.95", "2.29"),            # po riadkoch by bolo 2,30
    ([item("100.00")], "100.00", "23.00"),
    ([item("0.01")], "0.01", "0.00"),                # 0,0023 -> 0,00
    ([item("0.03")], "0.03", "0.01"),                # 0,0069 -> 0,01
    ([item("0.50", vat=19)], "0.50", "0.10"),        # 0,095 -> 0,10 (HALF_UP)
])
def test_vat_is_rounded_once_per_rate(lines, base, vat):

    totals = calculate_invoice_totals(lines)

    assert totals["total_base"] == D(base)
    assert totals["total_vat"] == D(vat)
    assert totals["total_gross"] == D(base) + D(vat)


def test_line_base_is_rounded_half_up_before_summing():

    # 3 x 0,335 = 1,005 -> 1,01 (HALF_UP, nie bankárske zaokrúhlenie)
    totals = calculate_invoice_totals([item("0.335", qty="3")])

    assert totals["total_base"] == D("1.01")


def test_fractional_quantity():

    totals = calculate_invoice_totals([item("12.50", qty="2.5")])

    assert totals["total_base"] == D("31.25")
    assert totals["total_vat"] == D("7.19")           # 7,1875 -> 7,19


def test_breakdown_sums_equal_totals_and_are_sorted():

    lines = [item("10.00", 23), item("5.55", 19), item("7.77", 0), item("0.10", 23), item("0.10", 23)]

    totals = calculate_invoice_totals(lines)
    rows = totals["vat_breakdown"]

    assert [row["rate"] for row in rows] == [0, 19, 23]
    assert sum(row["base"] for row in rows) == totals["total_base"]
    assert sum(row["vat"] for row in rows) == totals["total_vat"]
    assert sum(row["gross"] for row in rows) == totals["total_gross"]

    for row in rows:
        assert row["vat"] == (row["base"] * row["rate"] / 100).quantize(D("0.01"), rounding="ROUND_HALF_UP")
        assert row["gross"] == row["base"] + row["vat"]


def test_zero_rate_and_reverse_charge_have_no_vat():

    totals = calculate_invoice_totals([item("999.99", 0), item("0.01", 0)])

    assert totals["total_vat"] == D("0")
    assert totals["total_gross"] == D("1000.00")


@pytest.mark.parametrize("prices", [
    ["0.10"] * 3,
    ["0.15"] * 10,
    ["1.99"] * 5,
    ["33.33", "66.67", "0.01", "12.34"],
])
def test_generated_peppol_xml_passes_br_co_17_for_many_small_lines(prices):

    invoice = make_invoice([make_item(unit_price=D(price), vat_rate=23) for price in prices])

    xml_bytes = generate_peppol_xml(invoice, make_company())

    assert validate_peppol_invoice_xml(xml_bytes) == []


def test_mixed_rates_peppol_xml_is_consistent():

    invoice = make_invoice([
        make_item(unit_price=D("0.10"), vat_rate=23),
        make_item(unit_price=D("0.10"), vat_rate=23),
        make_item(unit_price=D("0.10"), vat_rate=23),
        make_item(unit_price=D("1.99"), vat_rate=19),
        make_item(unit_price=D("1.99"), vat_rate=19),
        make_item(unit_price=D("5.00"), vat_rate=0),
    ])

    assert validate_peppol_invoice_xml(generate_peppol_xml(invoice, make_company())) == []
