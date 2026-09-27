"""
Základná validačná vrstva pre vygenerované Peppol BIS Billing 3.0 /
UBL 2.1 XML (peppol_xml.py).

DÔLEŽITÉ - ČO TOTO JE A ČO NIE JE:

Toto NIE JE náhrada plnej oficiálnej validácie (Schematron pravidlá
OpenPEPPOL + EN16931, prípadne XSD schéma UBL 2.1) - tá appka
nevykonáva a ani nemá k dispozícii oficiálne pravidlové súbory. Pred
ostrým odoslaním faktúry treba XML aj tak overiť u certifikovaného
poskytovateľa (tzv. Digitálny poštár) alebo cez verejný validátor
(napr. https://ecosio.com/en/peppol-and-xml-document-validator/).

Toto JE menšia, ručne napísaná sada kontrol pokrývajúca tie
najdôležitejšie a najľahšie pokaziteľné pravidlá - najmä aritmetickú
konzistenciu súčtov (tie sa pri úprave výpočtov v appke najľahšie
nechtiac rozbijú) a prítomnosť pár kľúčových povinných polí. Slúži
najmä ako regresná poistka v testoch (tests/test_peppol_validation.py)
a ako informatívne upozornenie pri exporte (routers/invoices.py) - NIE
je to blokujúca kontrola, export sa nikdy nezastaví len kvôli nájdeným
problémom.

Implementované pravidlá (číslovanie a znenie podľa oficiálnej
dokumentácie https://docs.peppol.eu/poacc/billing/3.0/rules/ a
https://peppolvalidator.com/ - overené webovým vyhľadávaním, nie z
pamäte):

    BR-01   Invoice/CreditNote musí mať CustomizationID (a musí byť
            presne stanovená Peppol BIS 3.0 URI, nie len ľubovoľný
            neprázdny reťazec).
    BR-02   Invoice musí mať číslo (cbc:ID).
    BR-03   Invoice musí mať dátum vystavenia (cbc:IssueDate).
    BR-05   Invoice musí mať kód meny (cbc:DocumentCurrencyCode).
    BR-06   Predávajúci musí mať meno.
    BR-07   Odberateľ musí mať meno.
    BR-16   Invoice musí mať aspoň jednu položku (cac:InvoiceLine).
    BR-CO-10  Súčet LineExtensionAmount položiek = LineExtensionAmount
              v LegalMonetaryTotal.
    BR-CO-13  TaxExclusiveAmount = súčet netto súm položiek (appka
              negeneruje zľavy/prirážky na úrovni dokumentu, takže sa
              musí rovnať priamo LineExtensionAmount).
    BR-CO-14  TaxAmount (celkový) = súčet TaxAmount vo všetkých
              TaxSubtotal (súčet DPH podľa sadzieb).
    BR-CO-15  TaxInclusiveAmount = TaxExclusiveAmount + TaxAmount.
    BR-CO-17  TaxSubtotal/TaxAmount = TaxableAmount × (Percent / 100),
              zaokrúhlené na 2 desatinné miesta.
    (bez čísla, odvodené z BT-115 definície) PayableAmount =
              TaxInclusiveAmount (appka negeneruje zálohové platby ani
              zaokrúhlenie platby, takže sa musia rovnať priamo).
"""

from decimal import Decimal, ROUND_HALF_UP
from xml.etree.ElementTree import fromstring


NS = {
    "": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

EXPECTED_CUSTOMIZATION_ID = (
    "urn:cen.eu:en16931:2017#compliant#"
    "urn:fdc:peppol.eu:2017:poacc:billing:3.0"
)


def _text(element, path: str) -> str | None:

    found = element.find(path, NS)

    if found is None or found.text is None:
        return None

    return found.text.strip()


def _decimal(element, path: str) -> Decimal | None:

    raw = _text(element, path)

    if raw is None:
        return None

    try:
        return Decimal(raw)

    except Exception:
        return None


def _round2(value: Decimal) -> Decimal:

    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_peppol_invoice_xml(xml_bytes: bytes) -> list[str]:
    """
    Overí vygenerované Peppol/UBL XML podľa pravidiel vypísaných v
    docstringu modulu vyššie. Vráti zoznam nájdených problémov (každý
    ako čitateľný text s prefixom pravidla, napr. "BR-CO-10: ...") -
    prázdny zoznam znamená, že prešlo všetkými implementovanými
    kontrolami (POZOR: to NEZNAMENÁ, že prejde aj plnou oficiálnou
    validáciou - viď docstring modulu).
    """

    issues: list[str] = []

    try:
        root = fromstring(xml_bytes)

    except Exception as exc:

        return [f"XML sa nepodarilo naparsovať: {exc}"]

    # --- BR-01, BR-02, BR-03, BR-05 - povinné hlavičkové polia ---

    customization_id = _text(root, "cbc:CustomizationID")

    if not customization_id:

        issues.append(
            "BR-01: Faktúra nemá CustomizationID (BT-24)."
        )

    elif customization_id != EXPECTED_CUSTOMIZATION_ID:

        issues.append(
            "BR-01: CustomizationID nezodpovedá presnej Peppol BIS "
            f"3.0 URI (má '{customization_id}')."
        )

    if not _text(root, "cbc:ID"):

        issues.append(
            "BR-02: Faktúra nemá číslo (cbc:ID / BT-1)."
        )

    if not _text(root, "cbc:IssueDate"):

        issues.append(
            "BR-03: Faktúra nemá dátum vystavenia (cbc:IssueDate / BT-2)."
        )

    if not _text(root, "cbc:DocumentCurrencyCode"):

        issues.append(
            "BR-05: Faktúra nemá kód meny (cbc:DocumentCurrencyCode / BT-5)."
        )

    # --- BR-06, BR-07 - meno predávajúceho a odberateľa ---

    supplier_name = _text(
        root,
        "cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name"
    )

    if not supplier_name:

        issues.append(
            "BR-06: Chýba meno predávajúceho (BT-27)."
        )

    customer_name = _text(
        root,
        "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name"
    )

    if not customer_name:

        issues.append(
            "BR-07: Chýba meno odberateľa (BT-44)."
        )

    # --- BR-16 - aspoň jedna položka ---

    invoice_lines = root.findall("cac:InvoiceLine", NS)

    if not invoice_lines:

        issues.append(
            "BR-16: Faktúra nemá žiadnu položku (cac:InvoiceLine / BG-25)."
        )

    # --- BR-CO-10: súčet položiek = LineExtensionAmount v hlavičke ---

    legal_monetary_total = root.find("cac:LegalMonetaryTotal", NS)

    lines_sum = sum(
        (
            _decimal(line, "cbc:LineExtensionAmount") or Decimal("0")
            for line in invoice_lines
        ),
        Decimal("0")
    )

    header_line_extension = (
        _decimal(legal_monetary_total, "cbc:LineExtensionAmount")
        if legal_monetary_total is not None
        else None
    )

    if header_line_extension is None:

        issues.append(
            "BR-12: LegalMonetaryTotal nemá LineExtensionAmount (BT-106)."
        )

    elif header_line_extension != _round2(lines_sum):

        issues.append(
            "BR-CO-10: Súčet LineExtensionAmount položiek "
            f"({_round2(lines_sum)}) nesedí s LineExtensionAmount v "
            f"hlavičke ({header_line_extension})."
        )

    # --- BR-CO-13: TaxExclusiveAmount (appka negeneruje zľavy/prirážky
    #     na úrovni dokumentu, takže sa musí rovnať LineExtensionAmount) ---

    tax_exclusive = (
        _decimal(legal_monetary_total, "cbc:TaxExclusiveAmount")
        if legal_monetary_total is not None
        else None
    )

    if tax_exclusive is None:

        issues.append(
            "BR-13: LegalMonetaryTotal nemá TaxExclusiveAmount (BT-109)."
        )

    elif header_line_extension is not None and tax_exclusive != header_line_extension:

        issues.append(
            f"BR-CO-13: TaxExclusiveAmount ({tax_exclusive}) sa nerovná "
            f"LineExtensionAmount ({header_line_extension}) - appka "
            "negeneruje zľavy/prirážky na úrovni dokumentu, takže by "
            "sa mali rovnať."
        )

    # --- TaxTotal / TaxSubtotal - BR-CO-14, BR-CO-17 ---

    tax_total = root.find("cac:TaxTotal", NS)
    tax_subtotals = (
        tax_total.findall("cac:TaxSubtotal", NS)
        if tax_total is not None
        else []
    )

    header_tax_amount = (
        _decimal(tax_total, "cbc:TaxAmount")
        if tax_total is not None
        else None
    )

    if header_tax_amount is None:

        issues.append(
            "BR-CO-14: TaxTotal nemá celkový TaxAmount (BT-110)."
        )

    subtotal_sum = Decimal("0")

    for subtotal in tax_subtotals:

        taxable_amount = _decimal(subtotal, "cbc:TaxableAmount")
        tax_amount = _decimal(subtotal, "cbc:TaxAmount")
        percent = _decimal(subtotal, "cac:TaxCategory/cbc:Percent")

        if taxable_amount is None or tax_amount is None:

            issues.append(
                "BR-45: TaxSubtotal nemá TaxableAmount aj TaxAmount "
                "(BT-116, BT-117)."
            )
            continue

        subtotal_sum += tax_amount

        if percent is not None:

            expected_tax = _round2(taxable_amount * percent / Decimal("100"))

            if tax_amount != expected_tax:

                issues.append(
                    f"BR-CO-17: TaxAmount v jednej z DPH kategórií "
                    f"({tax_amount}) nesedí s TaxableAmount × sadzba / 100 "
                    f"(malo by byť {expected_tax})."
                )

    if header_tax_amount is not None and header_tax_amount != _round2(subtotal_sum):

        issues.append(
            f"BR-CO-14: Celkový TaxAmount ({header_tax_amount}) nesedí "
            f"so súčtom TaxAmount v DPH kategóriách ({_round2(subtotal_sum)})."
        )

    # --- BR-CO-15: TaxInclusiveAmount = TaxExclusiveAmount + TaxAmount ---

    tax_inclusive = (
        _decimal(legal_monetary_total, "cbc:TaxInclusiveAmount")
        if legal_monetary_total is not None
        else None
    )

    if tax_inclusive is None:

        issues.append(
            "BR-14: LegalMonetaryTotal nemá TaxInclusiveAmount (BT-112)."
        )

    elif tax_exclusive is not None and header_tax_amount is not None:

        expected_inclusive = tax_exclusive + header_tax_amount

        if tax_inclusive != expected_inclusive:

            issues.append(
                f"BR-CO-15: TaxInclusiveAmount ({tax_inclusive}) sa "
                f"nerovná TaxExclusiveAmount + TaxAmount "
                f"({expected_inclusive})."
            )

    # --- PayableAmount = TaxInclusiveAmount (bez zálohy/zaokrúhlenia) ---

    payable_amount = (
        _decimal(legal_monetary_total, "cbc:PayableAmount")
        if legal_monetary_total is not None
        else None
    )

    if payable_amount is None:

        issues.append(
            "BR-15: LegalMonetaryTotal nemá PayableAmount (BT-115)."
        )

    elif tax_inclusive is not None and payable_amount != tax_inclusive:

        issues.append(
            f"PayableAmount ({payable_amount}) sa nerovná "
            f"TaxInclusiveAmount ({tax_inclusive}) - appka negeneruje "
            "zálohové platby ani zaokrúhlenie platby, takže by sa mali "
            "rovnať."
        )

    return issues
