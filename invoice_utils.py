from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Invoice
from schemas import InvoiceStatus


def next_invoice_number(db: Session, year: int) -> str:
    """
    Vygeneruje ďalšie číslo faktúry pre daný rok vo formáte RRRRPPP
    (napr. 2026001, 2026002, ...). Číslovanie je nezávislé pre každý rok.
    """

    prefix = str(year)

    latest = (

        db
        .query(Invoice.invoice_number)
        .filter(
            Invoice.invoice_number.like(f"{prefix}%")
        )
        .order_by(
            Invoice.invoice_number.desc()
        )
        .first()

    )


    if latest is None:

        next_sequence = 1

    else:

        latest_number = latest[0]

        try:

            latest_sequence = int(latest_number[len(prefix):])

        except ValueError:

            latest_sequence = 0

        next_sequence = latest_sequence + 1


    return f"{prefix}{next_sequence:03d}"


def calculate_item_totals(quantity: Decimal, unit_price: Decimal, vat_rate: int) -> dict:
    """
    Vráti základ dane, DPH a sumu s DPH pre jednu položku faktúry.
    """

    base = (quantity * unit_price).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    vat = (base * Decimal(vat_rate) / Decimal(100)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    gross = base + vat


    return {

        "base": base,

        "vat": vat,

        "gross": gross

    }


def calculate_invoice_totals(items) -> dict:
    """
    Vypočíta celkové súčty faktúry a rozpis DPH podľa sadzieb
    (rekapitulácia DPH, ktorá musí byť na faktúre).

    `items` môžu byť InvoiceItem (SQLAlchemy) alebo InvoiceItemCreate (Pydantic) -
    stačí, že majú atribúty quantity, unit_price, vat_rate.
    """

    total_base = Decimal("0")
    total_vat = Decimal("0")
    total_gross = Decimal("0")

    vat_breakdown: dict[int, dict] = {}


    for item in items:

        quantity = Decimal(str(item.quantity))
        unit_price = Decimal(str(item.unit_price))

        line = calculate_item_totals(
            quantity,
            unit_price,
            item.vat_rate
        )

        total_base += line["base"]
        total_vat += line["vat"]
        total_gross += line["gross"]


        if item.vat_rate not in vat_breakdown:

            vat_breakdown[item.vat_rate] = {
                "rate": item.vat_rate,
                "base": Decimal("0"),
                "vat": Decimal("0"),
                "gross": Decimal("0")
            }

        vat_breakdown[item.vat_rate]["base"] += line["base"]
        vat_breakdown[item.vat_rate]["vat"] += line["vat"]
        vat_breakdown[item.vat_rate]["gross"] += line["gross"]


    return {

        "total_base": total_base,

        "total_vat": total_vat,

        "total_gross": total_gross,

        "vat_breakdown": sorted(
            vat_breakdown.values(),
            key=lambda row: row["rate"]
        )

    }


# =========================================
# "PO SPLATNOSTI" - VŽDY LEN POČÍTANÉ, NIKDY NEUKLADANÉ
#
# Toto je JEDINÉ miesto v appke, ktoré rozhoduje o tom, či je faktúra
# po termíne. Stĺpec `status` v databáze nikdy neobsahuje hodnotu
# "Po splatnosti" - je to čisto odvodená vlastnosť z due_date a aktuálneho
# stavu, počítaná za behu (nemôže sa preto rozísť s realitou tak, ako by
# sa mohla rozísť uložená hodnota, ktorú by niekto zabudol prepočítať).
# =========================================

CLOSED_INVOICE_STATUSES = (
    InvoiceStatus.PAID.value,
    InvoiceStatus.CANCELLED.value,
)


def is_invoice_overdue(invoice, today: date | None = None) -> bool:
    """
    Faktúra je "po splatnosti" vtedy a len vtedy, keď je jej dátum
    splatnosti v minulosti A zároveň ešte nie je uzavretá (uhradená
    alebo stornovaná faktúra sa nepovažuje za "po splatnosti", aj keby
    mala starý dátum splatnosti).
    """

    if today is None:
        today = date.today()

    return (
        invoice.due_date < today
        and invoice.status not in CLOSED_INVOICE_STATUSES
    )


# =========================================
# POVOLENÉ PRECHODY STAVOV FAKTÚRY
#
# "Po splatnosti" sa zámerne NIKDY neobjavuje ako cieľový stav v tejto
# mape - nedá sa nastaviť ručne, len sa počíta (viď is_invoice_overdue
# vyššie). Appka takúto požiadavku vždy odmietne ešte skôr, než by sa
# dostala k tejto mape (viď routers/invoices.py).
#
# Logika prechodov:
# - Návrh   -> Odoslaná, Uhradená, Stornovaná (dokument sa ešte len chystá)
# - Odoslaná -> Uhradená, Stornovaná (poslané, čaká sa na peniaze/stornovanie)
# - Uhradená -> Stornovaná (výnimočná oprava chyby, napr. duplicitná platba)
# - Stornovaná -> (nič, je to konečný stav)
# =========================================

ALLOWED_INVOICE_STATUS_TRANSITIONS: dict[str, set[str]] = {

    InvoiceStatus.DRAFT.value: {
        InvoiceStatus.SENT.value,
        InvoiceStatus.PAID.value,
        InvoiceStatus.CANCELLED.value,
    },

    InvoiceStatus.SENT.value: {
        InvoiceStatus.PAID.value,
        InvoiceStatus.CANCELLED.value,
    },

    InvoiceStatus.PAID.value: {
        InvoiceStatus.CANCELLED.value,
    },

    InvoiceStatus.CANCELLED.value: set(),

}


def allowed_next_invoice_statuses(current_status: str) -> set[str]:
    """
    Vráti množinu stavov, do ktorých sa dá z aktuálneho stavu legálne
    prejsť. Neznámy/legacy stav (nemal by nastať, ale pre istotu) sa berie
    ako stav bez povolených prechodov - bezpečnejší default, než tichý
    predpoklad, že je dovolené všetko.
    """

    return ALLOWED_INVOICE_STATUS_TRANSITIONS.get(current_status, set())


def is_valid_invoice_status_transition(current_status: str, new_status: str) -> bool:
    """
    Nastavenie na ten istý stav, aký už faktúra má, je vždy neškodné
    no-op a je povolené. Inak musí byť nový stav v množine povolených
    prechodov z aktuálneho stavu.
    """

    if new_status == current_status:
        return True

    return new_status in allowed_next_invoice_statuses(current_status)
