"""
Pomocné funkcie pre dashboard (/) - graf tržieb po mesiacoch a zoznam
faktúr po splatnosti. Sú oddelené od main.py, aby sa dali testovať bez
HTTP požiadavky a aby route handler zostal čitateľný.
"""

from datetime import date
from decimal import Decimal

from invoice_utils import calculate_invoice_totals, signed_invoice_total


SK_MONTH_SHORT = [
    "jan", "feb", "mar", "apr", "máj", "jún",
    "júl", "aug", "sep", "okt", "nov", "dec",
]


def first_day_of_month_back(today: date, months_back: int) -> date:
    """Prvý deň mesiaca, ktorý je `months_back` mesiacov pred mesiacom
    dátumu `today` (0 = aktuálny mesiac)."""

    index = today.year * 12 + (today.month - 1) - months_back

    return date(index // 12, index % 12 + 1, 1)


def revenue_paid_date(invoice) -> date:
    """Dátum, podľa ktorého sa faktúra počíta do tržieb - dátum úhrady,
    s poistkou na dátum vystavenia (rovnaké pravidlo ako na dashboarde)."""

    return invoice.paid_date or invoice.issue_date


def monthly_revenue_series(paid_invoices, today: date, months: int = 6) -> list[dict]:
    """
    Tržby (uhradené faktúry, dobropisy so záporným znamienkom) po
    mesiacoch za posledných `months` mesiacov vrátane aktuálneho,
    od najstaršieho po najnovší. Každá položka nesie aj `percent` -
    výšku stĺpca v grafe relatívne k najvyššiemu mesiacu (0-100).
    """

    first_month = first_day_of_month_back(today, months - 1)

    totals: dict[tuple[int, int], Decimal] = {}

    for offset in range(months):

        month_start = first_day_of_month_back(today, months - 1 - offset)
        totals[(month_start.year, month_start.month)] = Decimal("0")

    for invoice in paid_invoices:

        paid_on = revenue_paid_date(invoice)

        if paid_on < first_month or paid_on > today:
            continue

        key = (paid_on.year, paid_on.month)

        if key in totals:
            totals[key] += signed_invoice_total(invoice)

    highest = max((value for value in totals.values()), default=Decimal("0"))

    series = []

    for (year, month), total in totals.items():

        if highest > 0 and total > 0:
            # aspoň 4 %, nech je nenulový mesiac na grafe vždy viditeľný
            percent = max(4, int(total / highest * 100))
        else:
            percent = 0

        series.append({
            "year": year,
            "month": month,
            "label": SK_MONTH_SHORT[month - 1],
            "total": total,
            "percent": percent,
            "is_current": (year, month) == (today.year, today.month),
        })

    return series


def overdue_invoice_rows(overdue_invoices, today: date, limit: int = 5) -> list[dict]:
    """
    Faktúry po splatnosti pripravené na zobrazenie: najdlhšie
    omeškané ako prvé, max `limit` riadkov.
    """

    rows = []

    for invoice in overdue_invoices:

        rows.append({
            "id": invoice.id,
            "number": invoice.invoice_number,
            "customer_name": invoice.customer.name if invoice.customer else "",
            "total": calculate_invoice_totals(invoice.items)["total_gross"],
            "due_date": invoice.due_date,
            "days_overdue": (today - invoice.due_date).days,
        })

    rows.sort(key=lambda row: (-row["days_overdue"], row["number"]))

    return rows[:limit]
