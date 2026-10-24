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
