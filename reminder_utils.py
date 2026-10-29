"""
Pripomienky platieb: ktoré faktúry treba pripomenúť, v akej tónine a
hotový text správy. Nič sa neodosiela automaticky - appka pripraví
text (a mailto odkaz) a eviduje, že pripomienka prebehla.
"""

from datetime import date
from decimal import Decimal
from urllib.parse import quote

from invoice_utils import invoice_balance

# Koľko dní pred splatnosťou už faktúru ukázať v "Čoskoro splatné".
UPCOMING_DAYS = 3

# (od koľko dní po splatnosti, kľúč, názov stupňa)
REMINDER_LEVELS = (
    (30, "final", "Posledná výzva"),
    (14, "second", "Druhá upomienka"),
    (1, "first", "Pripomienka"),
)


def days_overdue(invoice, today: date) -> int:
    return (today - invoice.due_date).days


def reminder_level(days: int) -> dict:
    """Stupeň pripomienky podľa počtu dní po splatnosti."""

    for threshold, key, label in REMINDER_LEVELS:

        if days >= threshold:
            return {"key": key, "label": label}

    return {"key": "upcoming", "label": "Čoskoro splatná"}


def money(value) -> str:
    """1234.5 -> '1 234,50 €' (slovenský zápis)."""

    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " €"


def is_remindable(invoice) -> bool:
    """Pripomínať sa dajú len odoslané (vystavené) faktúry - nie návrhy,
    zálohové faktúry ani dobropisy."""

    return (
        invoice.status == "Odoslaná"
        and not invoice.is_proforma
        and not invoice.is_credit_note
    )


def reminder_rows(invoices, today: date) -> dict:
    """
    Rozdelí odoslané faktúry na `overdue` (po splatnosti, najdlhšie
    omeškané prvé) a `upcoming` (splatné do UPCOMING_DAYS dní).
    """

    overdue, upcoming = [], []

    for invoice in invoices:

        if not is_remindable(invoice):
            continue

        total = invoice_balance(invoice)

        row = {
            "invoice": invoice,
            "id": invoice.id,
            "number": invoice.invoice_number,
            "customer_name": invoice.customer.name if invoice.customer else "",
            "customer_email": invoice.customer.email if invoice.customer else None,
            "total": total,
            "due_date": invoice.due_date,
            "reminder_count": invoice.reminder_count or 0,
            "last_reminded_at": invoice.last_reminded_at,
        }

        if invoice.due_date < today:

            row["days"] = days_overdue(invoice, today)
            row["level"] = reminder_level(row["days"])
            overdue.append(row)

        elif (invoice.due_date - today).days <= UPCOMING_DAYS:

            row["days"] = (invoice.due_date - today).days
            row["level"] = reminder_level(0)
            upcoming.append(row)

    overdue.sort(key=lambda row: (-row["days"], row["number"]))
    upcoming.sort(key=lambda row: (row["days"], row["number"]))

    return {
        "overdue": overdue,
        "upcoming": upcoming,
        "overdue_total": sum((row["total"] for row in overdue), Decimal("0")),
    }


def build_reminder_message(invoice, company, today: date) -> dict:
    """Predmet a telo správy so zákazníkom, podľa stupňa omeškania."""

    total = money(invoice_balance(invoice))
    due = invoice.due_date.strftime("%d.%m.%Y")
    days = days_overdue(invoice, today)
    level = reminder_level(days)

    sender = (company.name if company and company.name else "").strip()
    symbol = invoice.variable_symbol or invoice.invoice_number

    payment_lines = []

    if company and company.iban:
        payment_lines.append(f"IBAN: {company.iban}")

    payment_lines.append(f"Variabilný symbol: {symbol}")
    payment_lines.append(f"Suma: {total}")

    payment = "\n".join(payment_lines)

    greeting = "Dobrý deň,\n\n"

    if days < 1:

        subject = f"Pripomenutie splatnosti faktúry {invoice.invoice_number}"

        intro = (
            f"dovoľujeme si Vás upozorniť, že faktúra č. {invoice.invoice_number} "
            f"na sumu {total} má splatnosť {due}."
        )

        closing = "Ak ste už platbu poslali, považujte túto správu za bezpredmetnú."

    elif level["key"] == "first":

        subject = f"Pripomienka platby faktúry {invoice.invoice_number}"

        intro = (
            f"v našej evidencii sme zatiaľ nezaznamenali úhradu faktúry č. "
            f"{invoice.invoice_number} na sumu {total}, ktorá bola splatná {due} "
            f"(po splatnosti {days} dní)."
        )

        closing = (
            "Prosíme o jej úhradu. Ak ste už platbu poslali, považujte túto "
            "správu za bezpredmetnú."
        )

    elif level["key"] == "second":

        subject = f"Upomienka - faktúra {invoice.invoice_number} po splatnosti"

        intro = (
            f"napriek našej predchádzajúcej pripomienke sme neevidovali úhradu "
            f"faktúry č. {invoice.invoice_number} na sumu {total}, splatnej {due} "
            f"(po splatnosti {days} dní)."
        )

        closing = "Žiadame Vás o jej úhradu do 7 dní od doručenia tejto správy."

    else:

        subject = f"Posledná výzva na úhradu faktúry {invoice.invoice_number}"

        intro = (
            f"faktúra č. {invoice.invoice_number} na sumu {total} je už {days} dní "
            f"po splatnosti ({due}) a napriek opakovaným pripomienkam nebola uhradená."
        )

        closing = (
            "Vyzývame Vás na jej úhradu do 7 dní. V opačnom prípade budeme nútení "
            "riešiť pohľadávku ďalším postupom."
        )

    body = f"{greeting}{intro}\n\nÚdaje na platbu:\n{payment}\n\n{closing}\n\nĎakujeme.\n"

    if sender:
        body += f"\nS pozdravom\n{sender}"

    return {"subject": subject, "body": body, "level": level, "days": days}


def mailto_url(email: str | None, subject: str, body: str) -> str | None:

    if not email:
        return None

    return f"mailto:{quote(email)}?subject={quote(subject)}&body={quote(body)}"
