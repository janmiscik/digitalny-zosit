"""
Zdieľané pomocné funkcie na parsovanie formulárových dát - používajú ich
routers/invoices.py aj routers/quotes.py, nech sa logika nedubluje medzi
faktúrami a cenovými ponukami (majú rovnaký tvar položiek: popis,
množstvo, MJ, cena/MJ, sadzba DPH).
"""

from datetime import date

from fastapi import HTTPException
from pydantic import ValidationError


def parse_optional_date(raw_value: str) -> date | None:

    if not raw_value:
        return None

    try:

        return date.fromisoformat(raw_value)

    except ValueError:

        raise HTTPException(
            status_code=400,
            detail="Neplatný formát dátumu (očakávaný formát: RRRR-MM-DD)"
        )


def parse_required_date(raw_value: str, field_label: str) -> date:

    parsed = parse_optional_date(raw_value)

    if parsed is None:

        raise HTTPException(
            status_code=400,
            detail=f"Pole '{field_label}' je povinné"
        )

    return parsed


def parse_items_from_form(
    form,
    item_schema,
    empty_message: str = "Doklad musí obsahovať aspoň jednu položku"
) -> list:
    """
    Zdieľaná logika parsovania riadkov položiek (faktúra aj cenová
    ponuka majú rovnaký tvar položky). `item_schema` je pydantic model
    triedy (InvoiceItemCreate alebo QuoteItemCreate) použitý na validáciu
    každého riadku.

    Vyhodí HTTPException 422, ak nie je zadaná ani jedna platná položka
    alebo ak niektorá položka neprejde validáciou.
    """

    descriptions = form.getlist("description")
    quantities = form.getlist("quantity")
    units = form.getlist("unit")
    unit_prices = form.getlist("unit_price")
    vat_rates = form.getlist("vat_rate")


    items_data = []

    for i in range(len(descriptions)):

        description = descriptions[i].strip()

        if not description:
            continue

        try:

            item = item_schema(
                description=description,
                quantity=quantities[i] or "1",
                unit=units[i] or "ks",
                unit_price=unit_prices[i] or "0",
                vat_rate=int(vat_rates[i] or 23)
            )

        except (ValidationError, ValueError, IndexError) as exc:

            raise HTTPException(
                status_code=422,
                detail=f"Neplatná položka: {exc}"
            )

        items_data.append(item)


    if not items_data:

        raise HTTPException(
            status_code=422,
            detail=empty_message
        )

    return items_data
