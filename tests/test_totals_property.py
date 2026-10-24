"""Náhodné (deterministické) kontroly invariantov výpočtu súm faktúry."""

import os
import random
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest

from invoice_utils import calculate_invoice_totals, signed_invoice_total

CENT = Decimal("0.01")
RATES = [0, 5, 10, 19, 20, 23]


def random_items(rng):

    return [
        SimpleNamespace(
            quantity=Decimal(str(round(rng.uniform(0.01, 500), rng.choice([0, 1, 2, 3])))),
            unit_price=Decimal(str(round(rng.uniform(0.01, 5000), rng.choice([2, 3, 4])))),
            vat_rate=rng.choice(RATES),
        )
        for _ in range(rng.randint(1, 25))
    ]


@pytest.mark.parametrize("seed", range(200))
def test_totals_invariants(seed):

    rng = random.Random(seed)
    items = random_items(rng)

    totals = calculate_invoice_totals(items)

    expected_base = {}

    for item in items:
        line = (item.quantity * item.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        expected_base[item.vat_rate] = expected_base.get(item.vat_rate, Decimal(0)) + line

    assert [entry["rate"] for entry in totals["vat_breakdown"]] == sorted(expected_base)

    for entry in totals["vat_breakdown"]:

        assert entry["base"] == expected_base[entry["rate"]]
        assert entry["vat"] == (entry["base"] * entry["rate"] / 100).quantize(CENT, rounding=ROUND_HALF_UP)
        assert entry["gross"] == entry["base"] + entry["vat"]

    assert totals["total_base"] == sum(e["base"] for e in totals["vat_breakdown"])
    assert totals["total_vat"] == sum(e["vat"] for e in totals["vat_breakdown"])
    assert totals["total_gross"] == totals["total_base"] + totals["total_vat"]

    # všetko v celých centoch
    for value in (totals["total_base"], totals["total_vat"], totals["total_gross"]):
        assert value == value.quantize(CENT)


@pytest.mark.parametrize("seed", range(50))
def test_credit_note_is_exact_negative_of_same_items(seed):

    items = random_items(random.Random(seed))

    invoice = SimpleNamespace(items=items, is_credit_note=False)
    credit = SimpleNamespace(items=items, is_credit_note=True)

    assert signed_invoice_total(credit) == -signed_invoice_total(invoice)
    assert signed_invoice_total(invoice) + signed_invoice_total(credit) == 0


def test_item_order_does_not_change_totals():

    rng = random.Random(7)
    items = random_items(rng)
    shuffled = items[:]
    rng.shuffle(shuffled)

    assert calculate_invoice_totals(items) == calculate_invoice_totals(shuffled)


# ---------- hraničné prípady ----------

def totals_of(*rows):

    return calculate_invoice_totals([
        SimpleNamespace(quantity=Decimal(q), unit_price=Decimal(p), vat_rate=v) for q, p, v in rows
    ])


def test_zero_and_tiny_quantities():

    assert totals_of(("0", "100", 23))["total_gross"] == Decimal("0.00")
    assert totals_of(("0.001", "0.01", 23))["total_gross"] == Decimal("0.00")
    assert totals_of(("0.001", "10", 23))["total_base"] == Decimal("0.01")


def test_discount_as_negative_line_reduces_the_base_per_rate():

    totals = totals_of(("1", "100", 23), ("1", "-10", 23), ("2", "50", 10))

    by_rate = {e["rate"]: e for e in totals["vat_breakdown"]}

    assert by_rate[23]["base"] == Decimal("90.00") and by_rate[23]["vat"] == Decimal("20.70")
    assert by_rate[10]["base"] == Decimal("100.00") and by_rate[10]["vat"] == Decimal("10.00")
    assert totals["total_gross"] == Decimal("220.70")


def test_mixed_rates_on_one_invoice_are_rounded_per_rate_once():

    totals = totals_of(*[("1", "0.33", 23)] * 3, *[("1", "0.33", 10)] * 3)

    by_rate = {e["rate"]: e for e in totals["vat_breakdown"]}

    # 0.99 * 23 % = 0.2277 -> 0.23 (nie 3 x 0.08 = 0.24 po riadkoch)
    assert by_rate[23]["vat"] == Decimal("0.23")
    assert by_rate[10]["vat"] == Decimal("0.10")


def test_half_cent_rounds_up():

    assert totals_of(("1", "0.50", 10))["total_vat"] == Decimal("0.05")
    assert totals_of(("1", "0.05", 10))["total_vat"] == Decimal("0.01")   # 0.005 -> 0.01


def test_recalculating_gives_identical_result_every_time():

    items = random_items(random.Random(99))
    first = calculate_invoice_totals(items)

    for _ in range(5):
        assert calculate_invoice_totals(items) == first


def test_partial_credit_note_is_negative_of_its_own_items_only():

    full = random_items(random.Random(5))
    part = full[:2]

    credit = SimpleNamespace(items=part, is_credit_note=True)

    assert signed_invoice_total(credit) == -calculate_invoice_totals(part)["total_gross"]
    assert -signed_invoice_total(credit) <= calculate_invoice_totals(full)["total_gross"]
