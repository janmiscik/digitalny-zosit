from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Invoice


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
