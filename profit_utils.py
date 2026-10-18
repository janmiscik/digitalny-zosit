"""
Zisk zo zákazky: tržba (bez DPH), náklady podľa druhu (materiál, práca,
iné), zisk a marža. Čistá logika bez HTTP - dá sa testovať samostatne.

Tržba sa počíta BEZ DPH (DPH sa odvádza štátu, nie je zárobok) a len z
faktúr, ktoré boli skutočne vystavené (Odoslaná/Uhradená), nie sú
zálohové; dobropis tržbu znižuje. Náklady sa zadávajú bez DPH.
"""

from decimal import Decimal, ROUND_HALF_UP

from invoice_utils import calculate_invoice_totals

# (kľúč, názov)
COST_CATEGORIES = (
    ("material", "Materiál"),
    ("praca", "Práca"),
    ("ine", "Iné (doprava, subdodávka...)"),
)

CATEGORY_KEYS = tuple(key for key, _label in COST_CATEGORIES)

CATEGORY_LABELS = dict(COST_CATEGORIES)

INVOICED_STATUSES = ("Odoslaná", "Uhradená")

ZERO = Decimal("0")


def signed_invoice_base(invoice) -> Decimal:
    """Suma faktúry BEZ DPH so správnym znamienkom (dobropis záporne)."""

    base = calculate_invoice_totals(invoice.items)["total_base"]

    return -base if invoice.is_credit_note else base


def job_profit(job) -> dict:
    """
    Prehľad zisku zákazky:

      revenue          tržba bez DPH (vystavené faktúry)
      revenue_paid     z toho už uhradené
      costs_by_category  {kľúč: suma}
      total_costs
      profit           revenue - total_costs
      margin           marža v % z tržby (None, ak je tržba 0)
      hours            odpracované hodiny spolu (z nákladov typu práca)
    """

    revenue = ZERO
    revenue_paid = ZERO

    for invoice in job.invoices:

        if invoice.status not in INVOICED_STATUSES or invoice.is_proforma:
            continue

        base = signed_invoice_base(invoice)

        revenue += base

        if invoice.status == "Uhradená":
            revenue_paid += base

    costs_by_category = {key: ZERO for key in CATEGORY_KEYS}

    hours = ZERO

    for cost in job.costs:

        key = cost.category if cost.category in costs_by_category else "ine"

        costs_by_category[key] += cost.amount

        if key == "praca" and cost.hours:
            hours += cost.hours

    total_costs = sum(costs_by_category.values(), ZERO)

    profit = revenue - total_costs

    margin = None

    if revenue != 0:

        margin = (profit / revenue * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    return {
        "revenue": revenue,
        "revenue_paid": revenue_paid,
        "revenue_pending": revenue - revenue_paid,
        "costs_by_category": costs_by_category,
        "total_costs": total_costs,
        "profit": profit,
        "margin": margin,
        "hours": hours,
        "has_data": bool(revenue or total_costs),
    }
