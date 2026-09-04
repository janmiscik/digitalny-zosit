from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Invoice, Quote
from schemas import InvoiceStatus, QuoteStatus


def _next_sequential_number(db: Session, column, prefix: str) -> str:
    """
    Spoločná logika číslovania pre faktúry, ponuky aj zálohové faktúry -
    nájde najväčšie existujúce číslo so zadaným prefixom a vráti ďalšie
    v poradí, vo formáte {prefix}{poradie:03d}.
    """

    latest = (

        db
        .query(column)
        .filter(
            column.like(f"{prefix}%")
        )
        .order_by(
            column.desc()
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


def next_invoice_number(db: Session, year: int) -> str:
    """
    Vygeneruje ďalšie číslo faktúry pre daný rok vo formáte RRRRPPP
    (napr. 2026001, 2026002, ...). Číslovanie je nezávislé pre každý rok.
    """

    return _next_sequential_number(db, Invoice.invoice_number, str(year))


def next_proforma_number(db: Session, year: int) -> str:
    """
    Vygeneruje ďalšie číslo zálohovej (proforma) faktúry vo formáte
    ZFRRRRPPP (napr. ZF2026001). Zámerne VLASTNÝ číselný rad, oddelený
    od ostrých faktúr - proforma nie je daňový doklad a nesmie "spotrebovať"
    číslo z fakturačnej rady, ktorá musí byť súvislá.
    """

    return _next_sequential_number(db, Invoice.invoice_number, f"ZF{year}")


def next_quote_number(db: Session, year: int) -> str:
    """
    Vygeneruje ďalšie číslo cenovej ponuky vo formáte CPRRRRPPP
    (napr. CP2026001).
    """

    return _next_sequential_number(db, Quote.quote_number, f"CP{year}")


def next_credit_note_number(db: Session, year: int) -> str:
    """
    Vygeneruje ďalšie číslo dobropisu vo formáte DPRRRRPPP
    (napr. DP2026001). Vlastný číselný rad, oddelený od ostrých faktúr
    aj zálohových faktúr.
    """

    return _next_sequential_number(db, Invoice.invoice_number, f"DP{year}")


def signed_invoice_total(invoice) -> Decimal:
    """
    Vráti CELKOVÚ sumu faktúry (s DPH) so správnym znamienkom - dobropis
    má vždy zápornú sumu (znižuje pohľadávku/tržbu), bežná aj zálohová
    faktúra kladnú.

    Toto je JEDINÉ miesto, ktoré rozhoduje o znamienku - všade v appke,
    kde sa sčítavajú sumy faktúr naprieč viacerými dokladmi (dashboard,
    súčty na stránke zákazníka, zisk zo zákazky), sa má použiť táto
    funkcia namiesto priameho čítania calculate_invoice_totals().
    """

    gross = calculate_invoice_totals(invoice.items)["total_gross"]

    return -gross if invoice.is_credit_note else gross


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


# =========================================
# DPH REŽIM - NEPLATCA VS. PLATCA DPH
#
# Neplatca DPH NESMIE na faktúre uvádzať sadzbu ani výšku DPH (zo zákona)
# a MUSÍ uviesť text "Nie som platiteľom DPH podľa zákona o DPH."
# Táto kontrola sa robí PRI VYTVÁRANÍ/ÚPRAVE faktúry (nie až vizuálne pri
# zobrazení) - dáta v DB majú byť konzistentné s DPH režimom firmy.
# =========================================

NON_VAT_PAYER_NOTICE = "Nie som platiteľom DPH podľa zákona o DPH."

REVERSE_CHARGE_NOTICE = "Prenesenie daňovej povinnosti"

CREDIT_NOTE_LABEL = "DOBROPIS"


def validate_vat_regime(is_vat_payer: bool, item_vat_rates: list[int]) -> None:
    """
    Ak firma NIE JE platiteľom DPH, žiadna položka faktúry nesmie mať
    nenulovú sadzbu DPH - inak by faktúra tvrdila niečo, čo firma zo
    zákona nesmie (účtovať a vyberať DPH bez toho, aby ňou bola
    registrovaná).

    Vyhodí ValueError s používateľsky zrozumiteľnou správou - volajúci
    (routers/invoices.py) ju premení na HTTPException 422.
    """

    if is_vat_payer:
        return

    if any(rate != 0 for rate in item_vat_rates):

        raise ValueError(
            "Ako neplatca DPH nemôžete na faktúre účtovať DPH "
            "(sadzba musí byť 0 %). Skontrolujte nastavenia firmy, ak "
            "ste medzičasom platiteľom DPH."
        )


def validate_reverse_charge_eligibility(
    company_is_vat_payer: bool,
    customer_ic_dph: str | None
) -> None:
    """
    Prenesenie daňovej povinnosti (§69 ods. 12 zákona o DPH) je možné
    len medzi dvoma platiteľmi DPH - dodávateľ aj odberateľ musia mať
    pridelené IČ DPH.

    Vyhodí ValueError s používateľsky zrozumiteľnou správou, ak podmienky
    nie sú splnené.
    """

    if not company_is_vat_payer:

        raise ValueError(
            "Prenesenie daňovej povinnosti je možné len ak ste "
            "platiteľom DPH."
        )

    if not customer_ic_dph:

        raise ValueError(
            "Prenesenie daňovej povinnosti je možné len vtedy, keď má "
            "odberateľ pridelené IČ DPH."
        )


def validate_invoice_vat(
    company_is_vat_payer: bool,
    customer_ic_dph: str | None,
    reverse_charge: bool,
    item_vat_rates: list[int]
) -> None:
    """
    Jednotný vstupný bod pre všetky pravidlá DPH pri vytváraní/úprave
    faktúry - volajte VŽDY túto funkciu (nie priamo validate_vat_regime/
    validate_reverse_charge_eligibility), nech sa poradie kontrol
    nerozíde medzi vytváraním a úpravou faktúry.
    """

    if reverse_charge:

        validate_reverse_charge_eligibility(company_is_vat_payer, customer_ic_dph)

        # Pri prenesení daňovej povinnosti sa DPH nikdy neúčtuje - platí
        # rovnaké pravidlo ako pre neplatcu DPH (nulová sadzba na všetkých
        # položkách).
        validate_vat_regime(is_vat_payer=False, item_vat_rates=item_vat_rates)

        return

    validate_vat_regime(company_is_vat_payer, item_vat_rates)


# =========================================
# CENOVÁ PONUKA - STAVY A PLATNOSŤ
#
# Rovnaký princíp ako pri faktúrach (Fáza 1): "Po platnosti" sa nikdy
# neukladá ako skutočný stav, len sa počíta z valid_until. Prechody
# medzi stavmi sú explicitne vymenované, nie "čokoľvek na čokoľvek".
# =========================================

def is_quote_expired(quote, today: date | None = None) -> bool:
    """
    Ponuka je "po platnosti" vtedy, keď má nastavený dátum platnosti,
    ten je v minulosti, a ponuka ešte nebola nijako uzavretá (prijatá/
    zamietnutá/prevedená na faktúru).
    """

    if today is None:
        today = date.today()

    if quote.valid_until is None:
        return False

    closed_statuses = (
        QuoteStatus.ACCEPTED.value,
        QuoteStatus.REJECTED.value,
        QuoteStatus.CONVERTED.value,
    )

    return (
        quote.valid_until < today
        and quote.status not in closed_statuses
    )


ALLOWED_QUOTE_STATUS_TRANSITIONS: dict[str, set[str]] = {

    QuoteStatus.DRAFT.value: {
        QuoteStatus.SENT.value,
        QuoteStatus.ACCEPTED.value,
        QuoteStatus.REJECTED.value,
    },

    QuoteStatus.SENT.value: {
        QuoteStatus.ACCEPTED.value,
        QuoteStatus.REJECTED.value,
    },

    QuoteStatus.ACCEPTED.value: {
        QuoteStatus.CONVERTED.value,
        QuoteStatus.REJECTED.value,
    },

    QuoteStatus.REJECTED.value: set(),

    QuoteStatus.CONVERTED.value: set(),

}


def allowed_next_quote_statuses(current_status: str) -> set[str]:

    return ALLOWED_QUOTE_STATUS_TRANSITIONS.get(current_status, set())


def is_valid_quote_status_transition(current_status: str, new_status: str) -> bool:

    if new_status == current_status:
        return True

    return new_status in allowed_next_quote_statuses(current_status)
