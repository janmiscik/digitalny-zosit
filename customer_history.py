"""
História zákazníka na jednej stránke: súhrnné čísla, galéria fotiek zo
všetkých jeho zákaziek a časová os (faktúry, ponuky, poznámky, fotky).
Čistá logika bez HTTP.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from auto_backup import format_local
from profit_utils import INVOICED_STATUSES, job_profit, signed_invoice_base

ZERO = Decimal("0")

TIMELINE_LIMIT = 20

MAX_NOTE_LENGTH = 2000


def format_note_time(value: datetime) -> str:
    """Naivný UTC čas z databázy -> lokálny čas na zobrazenie."""

    return format_local(value.replace(tzinfo=timezone.utc))


def customer_summary(customer) -> dict:
    """Súhrn vzťahu so zákazníkom."""

    jobs = list(customer.jobs)

    open_jobs = [job for job in jobs if job.status != "Hotová"]

    issued = [
        invoice for invoice in customer.invoices
        if invoice.status in INVOICED_STATUSES and not invoice.is_proforma
    ]

    revenue = sum((signed_invoice_base(invoice) for invoice in issued), ZERO)

    job_profits = [job_profit(job) for job in jobs]

    jobs_profit = sum((p["profit"] for p in job_profits), ZERO)
    jobs_revenue = sum((p["revenue"] for p in job_profits), ZERO)

    margin = None

    if jobs_revenue != 0:
        margin = (jobs_profit / jobs_revenue * 100).quantize(Decimal("0.1"))

    # priemerná doba, za ktorú zákazník platí (od vystavenia po úhradu)
    paid_days = [
        (invoice.paid_date - invoice.issue_date).days
        for invoice in issued
        if invoice.status == "Uhradená" and invoice.paid_date and not invoice.is_credit_note
    ]

    average_payment_days = round(sum(paid_days) / len(paid_days)) if paid_days else None

    return {
        "jobs_total": len(jobs),
        "jobs_open": len(open_jobs),
        "revenue": revenue,
        "jobs_profit": jobs_profit,
        "jobs_margin": margin,
        "has_job_profit": any(p["has_data"] for p in job_profits),
        "average_payment_days": average_payment_days,
        "notes_total": len(customer.notes),
    }


def customer_photos(customer) -> list[dict]:
    """Všetky fotky zo zákaziek zákazníka, najnovšie prvé."""

    photos = []

    for job in customer.jobs:

        for photo in job.photos:

            photos.append({
                "filename": photo.filename,
                "type": photo.photo_type,
                "date": photo.uploaded_at,
                "job_id": job.id,
                "job_title": job.title,
            })

    photos.sort(key=lambda item: (item["date"], item["filename"]), reverse=True)

    return photos


def build_timeline(customer, limit: int = TIMELINE_LIMIT) -> list[dict]:
    """
    Chronologický zoznam udalostí (najnovšie prvé): vystavená/uhradená
    faktúra, ponuka, poznámka, nahraté fotky (zlúčené po zákazke a dni).
    """

    events = []

    for invoice in customer.invoices:

        if invoice.status == "Návrh":
            continue

        label = "Zálohová faktúra" if invoice.is_proforma else (
            "Dobropis" if invoice.is_credit_note else "Faktúra"
        )

        events.append({
            "date": invoice.issue_date,
            "icon": "🧾",
            "text": f"{label} č. {invoice.invoice_number} vystavená",
            "url": f"/invoices/{invoice.id}",
        })

        if invoice.status == "Uhradená" and invoice.paid_date and not invoice.is_credit_note:

            events.append({
                "date": invoice.paid_date,
                "icon": "✅",
                "text": f"{label} č. {invoice.invoice_number} uhradená",
                "url": f"/invoices/{invoice.id}",
            })

    for quote in customer.quotes:

        events.append({
            "date": quote.issue_date,
            "icon": "📋",
            "text": f"Ponuka č. {quote.quote_number} ({quote.status})",
            "url": f"/quotes/{quote.id}",
        })

    for note in customer.notes:

        text = note.text if len(note.text) <= 80 else note.text[:77] + "..."

        events.append({
            "date": note.created_at.date(),
            "icon": "📝",
            "text": f"Poznámka: {text}",
            "url": None,
        })

    grouped: dict[tuple, int] = {}

    for photo in customer_photos(customer):

        key = (photo["date"], photo["job_id"], photo["job_title"], photo["type"])
        grouped[key] = grouped.get(key, 0) + 1

    for (photo_date, job_id, job_title, photo_type), count in grouped.items():

        kind = "pred" if photo_type == "pred" else "po"
        noun = "fotka" if count == 1 else ("fotky" if count < 5 else "fotiek")

        events.append({
            "date": photo_date,
            "icon": "📷",
            "text": f"{count} {noun} ({kind}) k zákazke {job_title}",
            "url": f"/jobs/{job_id}/edit",
        })

    events.sort(key=lambda event: event["date"], reverse=True)

    return events[:limit]
