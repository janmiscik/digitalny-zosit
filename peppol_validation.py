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
    BR-04   Invoice musí mať kód typu dokladu (cbc:InvoiceTypeCode).
    BR-05   Invoice musí mať kód meny (cbc:DocumentCurrencyCode).
    BR-06   Predávajúci musí mať meno.
    BR-07   Odberateľ musí mať meno.
    BR-08   Predávajúci musí mať poštovú adresu (aspoň krajinu).
    BR-09   Predávajúci musí mať kód krajiny.
    BR-10   Odberateľ musí mať poštovú adresu (aspoň krajinu).
    BR-11   Odberateľ musí mať kód krajiny.
    BR-16   Invoice musí mať aspoň jednu položku (cac:InvoiceLine).
    BR-21   Každá položka musí mať ID (BT-126).
    BR-22   Každá položka musí mať fakturované množstvo (BT-129).
    BR-23   Merná jednotka každej položky musí byť kódovaná (BT-130).
    BR-24   Každá položka musí mať sumu položky (BT-131).
    BR-25   Každá položka musí mať názov (BT-153).
    BR-26   Každá položka musí mať jednotkovú cenu (BT-146).
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

    PEPPOL-EN16931-R020  Predávajúci musí mať elektronickú adresu
              (cbc:EndpointID pod AccountingSupplierParty/Party - BT-34).
    PEPPOL-EN16931-R010  Odberateľ musí mať elektronickú adresu
              (cbc:EndpointID pod AccountingCustomerParty/Party - BT-49).
              Peppol vyžaduje kardinalitu 1..1 pre obe - appka ich
              zámerne negeneruje bez schémy (viď peppol_xml.py), takže
              táto kontrola na to explicitne upozorní namiesto ticha.
    BR-62, BR-63  Ak EndpointID existuje, MUSÍ mať atribút schemeID
              (predávajúci, odberateľ).
    BR-CL-25  schemeID pri EndpointID musí patriť do EAS/ISO 6523
              číselníka - overuje sa len tvar a pár známych odstránených
              kódov (viď _is_plausible_eas_scheme), nie plná zhoda
              s oficiálnym číselníkom.

    BR-AE-01  Ak faktúra obsahuje položku s kategóriou "AE", DPH rozpis
              musí mať práve jeden zodpovedajúci TaxSubtotal.
    BR-AE-02  Ak je ktorákoľvek položka v režime prenesenia daňovej
              povinnosti (VAT kategória "AE"), faktúra musí obsahovať
              IČ DPH predávajúceho AJ odberateľa.
    BR-AE-05  Položka s kategóriou "AE" musí mať sadzbu DPH 0.
    BR-AE-08  TaxableAmount v kategórii "AE" = súčet súm položiek
              s touto kategóriou.
    BR-AE-09  TaxAmount v DPH kategórii "AE" musí byť 0.
    BR-AE-10  DPH rozpis v kategórii "AE" musí mať dôvod oslobodenia
              od DPH (TaxExemptionReasonCode/TaxExemptionReason).
    BR-Z-01   Ak faktúra obsahuje položku s kategóriou "Z", DPH rozpis
              musí mať práve jeden zodpovedajúci TaxSubtotal.
    BR-Z-02   Ak je ktorákoľvek položka s nulovou sadzbou DPH (VAT
              kategória "Z"), faktúra musí obsahovať IČ DPH
              predávajúceho.
    BR-Z-05   Položka s kategóriou "Z" musí mať sadzbu DPH 0.
    BR-Z-08   TaxableAmount v kategórii "Z" = súčet súm položiek
              s touto kategóriou.
    BR-Z-09   TaxAmount v DPH kategórii "Z" musí byť 0.
    BR-Z-10   DPH rozpis v kategórii "Z" NESMIE mať dôvod oslobodenia
              od DPH.

Verzia: kontroly vychádzajú z Peppol BIS Billing 3.0.21 (máj 2026,
povinná od 17.8.2026). Rozsah je zámerne výberový (najčastejšie/
najzávažnejšie pravidlá), nie kompletná implementácia všetkých ~200+
pravidiel EN16931 + Peppol schematronu.
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from xml.etree.ElementTree import ParseError, fromstring


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
        value = Decimal(raw)

    except InvalidOperation:
        return None

    # "NaN"/"Infinity" sú pre Decimal platné hodnoty, ale porovnania a
    # zaokrúhľovanie s nimi končia výnimkou - v XML sú to neplatné sumy.
    return value if value.is_finite() else None


def _round2(value: Decimal) -> Decimal:

    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Kódy, ktoré boli z EAS/ISO 6523 číselníka odstránené vo verzii 3.0.21
# (máj 2026, povinná od 17.8.2026) - overené webovým vyhľadávaním. Nie
# je to úplný zoznam všetkých historicky odstránených kódov, len tie,
# ktoré appka predtým sama používala alebo ponúkala ako príklad.
_REMOVED_EAS_SCHEMES = {"0037", "9901", "9906"}


def _is_plausible_eas_scheme(scheme_id: str) -> bool:
    """
    Len tvarová/hrubá kontrola (NIE plná zhoda s oficiálnym číselníkom,
    ten má rádovo stovky kódov a appka ho v celku neudržiava - pozri
    PEPPOL_EAS_SCHEMES vo validators.py pre výber, ktorý appka pozná
    podrobnejšie vrátane priradenia ku krajine).
    """

    stripped = (scheme_id or "").strip().upper()

    return bool(re.match(r"^[A-Z0-9]{4}$", stripped)) and stripped not in _REMOVED_EAS_SCHEMES


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

    except ParseError as exc:

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

    if not _text(root, "cbc:InvoiceTypeCode"):

        issues.append(
            "BR-04: Faktúra nemá kód typu dokladu "
            "(cbc:InvoiceTypeCode / BT-3)."
        )

    # --- BR-06, BR-07 - meno predávajúceho a odberateľa ---

    supplier_party = root.find("cac:AccountingSupplierParty/cac:Party", NS)

    supplier_name = _text(
        root,
        "cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name"
    )

    if not supplier_name:

        issues.append(
            "BR-06: Chýba meno predávajúceho (BT-27)."
        )

    customer_party = root.find("cac:AccountingCustomerParty/cac:Party", NS)

    customer_name = _text(
        root,
        "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name"
    )

    if not customer_name:

        issues.append(
            "BR-07: Chýba meno odberateľa (BT-44)."
        )

    # --- BR-08, BR-09 - adresa a krajina predávajúceho ---

    supplier_address = (
        supplier_party.find("cac:PostalAddress", NS)
        if supplier_party is not None
        else None
    )

    if supplier_address is None:

        issues.append(
            "BR-08: Chýba poštová adresa predávajúceho (BG-5)."
        )

    supplier_country = (
        _text(supplier_address, "cac:Country/cbc:IdentificationCode")
        if supplier_address is not None
        else None
    )

    if not supplier_country:

        issues.append(
            "BR-09: Chýba kód krajiny predávajúceho (BT-40)."
        )

    # --- BR-10, BR-11 - adresa a krajina odberateľa ---

    customer_address = (
        customer_party.find("cac:PostalAddress", NS)
        if customer_party is not None
        else None
    )

    if customer_address is None:

        issues.append(
            "BR-10: Chýba poštová adresa odberateľa (BG-8)."
        )

    customer_country = (
        _text(customer_address, "cac:Country/cbc:IdentificationCode")
        if customer_address is not None
        else None
    )

    if not customer_country:

        issues.append(
            "BR-11: Chýba kód krajiny odberateľa (BT-55)."
        )

    # --- PEPPOL-EN16931-R020, PEPPOL-EN16931-R010 - elektronická
    #     adresa (EndpointID) predávajúceho a odberateľa (BT-34, BT-49) -
    #     Peppol (na rozdiel od holého EN16931) ju vyžaduje s
    #     kardinalitou 1..1 pre oboch.
    #
    #     BR-62/BR-63 - ak EndpointID existuje, MUSÍ mať atribút
    #     schemeID. Appka to sama zámerne negeneruje bez schémy
    #     (peppol_xml.py), takže tu na to explicitne upozorníme.
    #
    #     BR-CL-25 - schemeID musí patriť do EAS/ISO 6523 číselníka.
    #     Overuje sa len tvar (4 alfanumerické znaky) a niekoľko kódov,
    #     ktoré boli z číselníka vo verzii 3.0.21 (máj 2026) odstránené
    #     - NEJDE o kontrolu voči úplnému oficiálnemu číselníku (má
    #     rádovo stovky kódov), len o odchytenie najčastejších chýb.

    supplier_endpoint = (
        supplier_party.find("cbc:EndpointID", NS)
        if supplier_party is not None
        else None
    )

    if supplier_endpoint is None or not (supplier_endpoint.text or "").strip():

        issues.append(
            "PEPPOL-EN16931-R020: Chýba elektronická adresa "
            "predávajúceho (cbc:EndpointID / BT-34) - vyplň Peppol "
            "schému aj Endpoint ID predávajúceho v Nastaveniach."
        )

    else:

        supplier_scheme = supplier_endpoint.get("schemeID")

        if not supplier_scheme:

            issues.append(
                "BR-62: Elektronická adresa predávajúceho (BT-34) nemá "
                "atribút schemeID."
            )

        elif not _is_plausible_eas_scheme(supplier_scheme):

            issues.append(
                f"BR-CL-25: Schéma predávajúceho '{supplier_scheme}' "
                "nevyzerá ako platný EAS/ISO 6523 kód."
            )

    customer_endpoint = (
        customer_party.find("cbc:EndpointID", NS)
        if customer_party is not None
        else None
    )

    if customer_endpoint is None or not (customer_endpoint.text or "").strip():

        issues.append(
            "PEPPOL-EN16931-R010: Chýba elektronická adresa odberateľa "
            "(cbc:EndpointID / BT-49) - vyplň Peppol schému aj "
            "Endpoint ID odberateľa v jeho karte."
        )

    else:

        customer_scheme = customer_endpoint.get("schemeID")

        if not customer_scheme:

            issues.append(
                "BR-63: Elektronická adresa odberateľa (BT-49) nemá "
                "atribút schemeID."
            )

        elif not _is_plausible_eas_scheme(customer_scheme):

            issues.append(
                f"BR-CL-25: Schéma odberateľa '{customer_scheme}' "
                "nevyzerá ako platný EAS/ISO 6523 kód."
            )

    # --- BR-16 - aspoň jedna položka ---

    invoice_lines = root.findall("cac:InvoiceLine", NS)

    if not invoice_lines:

        issues.append(
            "BR-16: Faktúra nemá žiadnu položku (cac:InvoiceLine / BG-25)."
        )

    # --- BR-21 až BR-26 - povinné polia na úrovni položky ---
    # Číslujeme položky od 1 (ako appka pri generovaní XML - viď
    # peppol_xml.py) len pre čitateľnosť chybovej hlášky, nie preto,
    # že by BR-21 vyžadovalo konkrétnu hodnotu ID.
    #
    # Súbežne (aby sa cez položky neprechádzalo dvakrát) sa tu zbiera
    # súčet LineExtensionAmount podľa kategórie DPH (line_amounts_by_category)
    # - použije sa nižšie pri BR-Z-08/BR-AE-08 (súčet netto súm riadkov
    # danej kategórie sa musí rovnať TaxableAmount v TaxSubtotal).

    line_amounts_by_category: dict[str, Decimal] = {}

    for position, line in enumerate(invoice_lines, start=1):

        if not _text(line, "cbc:ID"):

            issues.append(
                f"BR-21: Položka č. {position} nemá ID (cbc:ID / BT-126)."
            )

        quantity_el = line.find("cbc:InvoicedQuantity", NS)

        if quantity_el is None or not (quantity_el.text or "").strip():

            issues.append(
                f"BR-22: Položka č. {position} nemá fakturované "
                "množstvo (cbc:InvoicedQuantity / BT-129)."
            )

        elif not quantity_el.get("unitCode"):

            issues.append(
                f"BR-23: Položka č. {position} nemá kódovanú mernú "
                "jednotku (unitCode / BT-130)."
            )

        line_amount = _decimal(line, "cbc:LineExtensionAmount")

        if line_amount is None:

            issues.append(
                f"BR-24: Položka č. {position} nemá sumu "
                "(cbc:LineExtensionAmount / BT-131)."
            )

        if not _text(line, "cac:Item/cbc:Name"):

            issues.append(
                f"BR-25: Položka č. {position} nemá názov "
                "(cac:Item/cbc:Name / BT-153)."
            )

        if _decimal(line, "cac:Price/cbc:PriceAmount") is None:

            issues.append(
                f"BR-26: Položka č. {position} nemá jednotkovú cenu "
                "(cac:Price/cbc:PriceAmount / BT-146)."
            )

        line_category = _text(line, "cac:Item/cac:ClassifiedTaxCategory/cbc:ID")
        line_rate = _decimal(line, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent")

        if line_category == "Z" and line_rate is not None and line_rate != Decimal("0"):

            issues.append(
                f"BR-Z-05: Položka č. {position} má kategóriu DPH \"Z\" "
                f"(nulová sadzba), ale jej sadzba je {line_rate}, nie 0."
            )

        if line_category == "AE" and line_rate is not None and line_rate != Decimal("0"):

            issues.append(
                f"BR-AE-05: Položka č. {position} má kategóriu DPH \"AE\" "
                f"(prenesenie daňovej povinnosti), ale jej sadzba je "
                f"{line_rate}, nie 0."
            )

        if line_category and line_amount is not None:

            line_amounts_by_category[line_category] = (
                line_amounts_by_category.get(line_category, Decimal("0"))
                + line_amount
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

    has_reverse_charge_category = False
    has_zero_rated_category = False
    reverse_charge_subtotal_count = 0
    zero_rated_subtotal_count = 0

    for subtotal in tax_subtotals:

        taxable_amount = _decimal(subtotal, "cbc:TaxableAmount")
        tax_amount = _decimal(subtotal, "cbc:TaxAmount")
        percent = _decimal(subtotal, "cac:TaxCategory/cbc:Percent")
        category_id = _text(subtotal, "cac:TaxCategory/cbc:ID")
        exemption_code = _text(subtotal, "cac:TaxCategory/cbc:TaxExemptionReasonCode")
        exemption_text = _text(subtotal, "cac:TaxCategory/cbc:TaxExemptionReason")

        if category_id == "AE":

            has_reverse_charge_category = True
            reverse_charge_subtotal_count += 1

            if tax_amount is not None and tax_amount != Decimal("0"):

                issues.append(
                    "BR-AE-09: TaxAmount v DPH kategórii \"AE\" "
                    f"(prenesenie daňovej povinnosti) musí byť 0, "
                    f"nájdená hodnota je {tax_amount}."
                )

            if not exemption_code and not exemption_text:

                issues.append(
                    "BR-AE-10: DPH kategória \"AE\" (prenesenie daňovej "
                    "povinnosti) musí mať dôvod oslobodenia od DPH "
                    "(TaxExemptionReasonCode alebo TaxExemptionReason)."
                )

            if taxable_amount is not None and "AE" in line_amounts_by_category:

                expected_taxable = _round2(line_amounts_by_category["AE"])

                if _round2(taxable_amount) != expected_taxable:

                    issues.append(
                        "BR-AE-08: TaxableAmount v DPH kategórii \"AE\" "
                        f"({taxable_amount}) nesedí so súčtom súm "
                        f"položiek s touto kategóriou ({expected_taxable})."
                    )

        elif category_id == "Z":

            has_zero_rated_category = True
            zero_rated_subtotal_count += 1

            if tax_amount is not None and tax_amount != Decimal("0"):

                issues.append(
                    "BR-Z-09: TaxAmount v DPH kategórii \"Z\" (nulová "
                    f"sadzba) musí byť 0, nájdená hodnota je {tax_amount}."
                )

            if exemption_code or exemption_text:

                issues.append(
                    "BR-Z-10: DPH kategória \"Z\" (nulová sadzba) nesmie "
                    "mať dôvod oslobodenia od DPH (TaxExemptionReasonCode "
                    "ani TaxExemptionReason)."
                )

            if taxable_amount is not None and "Z" in line_amounts_by_category:

                expected_taxable = _round2(line_amounts_by_category["Z"])

                if _round2(taxable_amount) != expected_taxable:

                    issues.append(
                        "BR-Z-08: TaxableAmount v DPH kategórii \"Z\" "
                        f"({taxable_amount}) nesedí so súčtom súm "
                        f"položiek s touto kategóriou ({expected_taxable})."
                    )

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

    # --- BR-AE-01, BR-Z-01: ak faktúra obsahuje riadok/zľavu/prirážku
    #     v danej kategórii, musí mať v DPH rozpise PRÁVE JEDEN
    #     zodpovedajúci TaxSubtotal (nie 0, nie 2+) ---

    if "AE" in line_amounts_by_category and reverse_charge_subtotal_count != 1:

        issues.append(
            "BR-AE-01: Faktúra obsahuje položku s kategóriou DPH "
            f"\"AE\", ale v DPH rozpise je {reverse_charge_subtotal_count} "
            "zodpovedajúcich riadkov namiesto presne jedného."
        )

    if "Z" in line_amounts_by_category and zero_rated_subtotal_count != 1:

        issues.append(
            "BR-Z-01: Faktúra obsahuje položku s kategóriou DPH "
            f"\"Z\", ale v DPH rozpise je {zero_rated_subtotal_count} "
            "zodpovedajúcich riadkov namiesto presne jedného."
        )

    # --- BR-AE-02: prenesenie daňovej povinnosti vyžaduje IČ DPH
    #     predávajúceho AJ odberateľa ---

    if has_reverse_charge_category:

        supplier_vat_id = (
            _text(supplier_party, "cac:PartyTaxScheme/cbc:CompanyID")
            if supplier_party is not None
            else None
        )

        customer_vat_id = (
            _text(customer_party, "cac:PartyTaxScheme/cbc:CompanyID")
            if customer_party is not None
            else None
        )

        if not supplier_vat_id:

            issues.append(
                "BR-AE-02: Pri prenesení daňovej povinnosti (Reverse "
                "Charge) musí faktúra obsahovať IČ DPH predávajúceho "
                "(BT-31) - vyplň ho v Nastaveniach."
            )

        if not customer_vat_id:

            issues.append(
                "BR-AE-02: Pri prenesení daňovej povinnosti (Reverse "
                "Charge) musí faktúra obsahovať IČ DPH odberateľa "
                "(BT-48) - vyplň ho v karte zákazníka."
            )

    # --- BR-Z-02: nulová sadzba DPH vyžaduje IČ DPH predávajúceho ---

    if has_zero_rated_category:

        supplier_vat_id = (
            _text(supplier_party, "cac:PartyTaxScheme/cbc:CompanyID")
            if supplier_party is not None
            else None
        )

        if not supplier_vat_id:

            issues.append(
                "BR-Z-02: Pri nulovej sadzbe DPH (kategória \"Z\") "
                "musí faktúra obsahovať IČ DPH predávajúceho (BT-31) "
                "- vyplň ho v Nastaveniach."
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
