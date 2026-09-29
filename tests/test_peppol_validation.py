"""
Testy pre peppol_validation.py.

Časť generuje reálne XML cez peppol_xml.generate_peppol_xml() (rôzne
scenáre: štandardná sadzba, nulová sadzba, prenesenie daňovej
povinnosti, viacero sadzieb naraz, bez firmy/IBAN) a overuje, že
validácia nehlási ŽIADNE problémy - to je hlavná hodnota tohto súboru:
regresná poistka, že budúca úprava výpočtov v appke omylom nerozbije
aritmetickú konzistenciu vygenerovaného XML.

Druhá časť naschvál poškodí vygenerované XML (zlé súčty, chýbajúce
polia, zlé CustomizationID) a overuje, že validátor to skutočne
odhalí - inak by prvá časť (samé "žiadny problém") mohla byť len
falošne upokojujúca (validátor, ktorý nikdy nič nenájde).
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1])
)


from peppol_validation import validate_peppol_invoice_xml
from peppol_xml import generate_peppol_xml


def make_customer(**overrides):

    defaults = dict(
        name="Zákazník s.r.o.",
        ico="10482245",
        ic_dph="SK2020123456",
        address="Ulica 1",
        city="Mesto",
        zip_code="81101",
        email="zakaznik@example.com",
        phone="0900111222",
        country_code="SK",
        # Peppol vyžaduje elektronickú adresu (EndpointID) aj pre
        # odberateľa s kardinalitou 1..1 (PEPPOL-EN16931-R010) - v
        # "šťastných" scenároch (žiadny nález) preto musí byť vyplnená
        # aj tu, nielen na strane firmy. Pri schéme 9950 (SK:VAT) je
        # hodnotou IČ DPH, NIE IČO (viď peppol_xml.py).
        peppol_scheme_id="9950",
        peppol_endpoint_id="SK2020123456"
    )
    defaults.update(overrides)

    return SimpleNamespace(**defaults)


def make_company(**overrides):

    defaults = dict(
        name="Moja Firma s.r.o.",
        ico="87654326",
        ic_dph="SK2020123456",
        address="Firemná 5",
        city="Bratislava",
        zip_code="82109",
        email="firma@example.com",
        phone="0900999888",
        # 9950 = SK:VAT. Endpoint ID je IČ DPH, NIE IČO - predtým
        # appka do EndpointID posielala IČO bez ohľadu na schému.
        peppol_scheme_id="9950",
        peppol_endpoint_id="SK2020123456",
        iban="SK3112000000198742637541",
        swift_bic="TATRSKBX"
    )
    defaults.update(overrides)

    return SimpleNamespace(**defaults)


def make_item(**overrides):

    defaults = dict(
        description="Práca",
        quantity=Decimal("1"),
        unit="ks",
        unit_price=Decimal("100.00"),
        vat_rate=23
    )
    defaults.update(overrides)

    return SimpleNamespace(**defaults)


def make_invoice(items, customer=None, company_kwargs=None, **overrides):

    defaults = dict(
        invoice_number="2026-0001",
        issue_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        note=None,
        variable_symbol="20260001",
        reverse_charge=False
    )
    defaults.update(overrides)

    invoice = SimpleNamespace(
        **defaults,
        items=items,
        customer=customer or make_customer()
    )

    return invoice


# =========================================
# REÁLNE (SPRÁVNE) SCENÁRE - VŽDY ŽIADNY NÁLEZ
# =========================================

def test_standard_rate_invoice_has_no_issues():

    invoice = make_invoice([make_item(vat_rate=23)])

    xml_bytes = generate_peppol_xml(invoice, make_company())

    assert validate_peppol_invoice_xml(xml_bytes) == []


def test_zero_rate_invoice_has_no_issues():

    invoice = make_invoice([make_item(vat_rate=0)])

    xml_bytes = generate_peppol_xml(invoice, make_company())

    assert validate_peppol_invoice_xml(xml_bytes) == []


def test_reverse_charge_invoice_has_no_issues():

    invoice = make_invoice(
        [make_item(vat_rate=0)],
        reverse_charge=True
    )

    xml_bytes = generate_peppol_xml(invoice, make_company())

    assert validate_peppol_invoice_xml(xml_bytes) == []


def test_mixed_vat_rates_invoice_has_no_issues():

    invoice = make_invoice([
        make_item(description="Práca", quantity=Decimal("2"), unit_price=Decimal("25.50"), vat_rate=23),
        make_item(description="Materiál", quantity=Decimal("1"), unit_price=Decimal("10.00"), vat_rate=23),
        make_item(description="Poradenstvo", quantity=Decimal("1"), unit_price=Decimal("15.00"), vat_rate=0),
        make_item(description="Znížená sadzba", quantity=Decimal("3"), unit_price=Decimal("7.30"), vat_rate=10),
    ])

    xml_bytes = generate_peppol_xml(invoice, make_company())

    assert validate_peppol_invoice_xml(xml_bytes) == []


def test_invoice_without_company_has_no_arithmetic_issues():
    """
    Company môže byť None (appka ho ešte nemá nastavený) - export má
    stále vyprodukovať aritmeticky konzistentné XML. BR-06 (chýbajúce
    meno predávajúceho) sa v tomto prípade OČAKÁVANE nájde - bez
    nastavenej firmy appka meno predávajúceho skutočne nemá čo vypísať,
    a to je legitímny, správny nález validátora, nie chyba generátora.
    """

    invoice = make_invoice([make_item()])

    xml_bytes = generate_peppol_xml(invoice, None)

    issues = validate_peppol_invoice_xml(xml_bytes)

    # BR-06 (meno) + PEPPOL-EN16931-R020 (bez firmy nie je čo použiť
    # ako elektronickú adresu predávajúceho) - obe sú legitímne nálezy.
    assert len(issues) == 2
    assert issues[0] == "BR-06: Chýba meno predávajúceho (BT-27)."
    assert issues[1].startswith("PEPPOL-EN16931-R020")


def test_invoice_without_iban_has_no_issues():

    invoice = make_invoice([make_item()])
    company = make_company(iban=None, swift_bic=None)

    xml_bytes = generate_peppol_xml(invoice, company)

    assert validate_peppol_invoice_xml(xml_bytes) == []


def test_many_items_odd_prices_has_no_issues():
    """Veľa položiek s 'nepohodlnými' desatinnými cenami - najčastejší
    reálny zdroj zaokrúhľovacích nezrovnalostí."""

    items = [
        make_item(
            description=f"Položka {i}",
            quantity=Decimal(str(i + 1)),
            unit_price=Decimal("13.37"),
            vat_rate=[0, 10, 23][i % 3]
        )
        for i in range(7)
    ]

    invoice = make_invoice(items)

    xml_bytes = generate_peppol_xml(invoice, make_company())

    assert validate_peppol_invoice_xml(xml_bytes) == []


# =========================================
# ÚMYSELNE POŠKODENÉ XML - MUSÍ SA NÁJSŤ PROBLÉM
# =========================================

def _valid_xml_bytes() -> bytes:

    invoice = make_invoice([
        make_item(vat_rate=23),
        make_item(description="Nulová sadzba", vat_rate=0),
    ])

    return generate_peppol_xml(invoice, make_company())


def test_detects_malformed_xml():

    issues = validate_peppol_invoice_xml(b"toto nie je xml <<<")

    assert len(issues) == 1
    assert "naparsovať" in issues[0]


def test_detects_wrong_customization_id():

    broken = _valid_xml_bytes().replace(
        b"urn:cen.eu:en16931:2017",
        b"urn:cen.eu:CHYBA:2017"
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-01" in issue for issue in issues)


def test_detects_missing_invoice_number():

    broken = _valid_xml_bytes().replace(
        b"<cbc:ID>2026-0001</cbc:ID>",
        b""
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-02" in issue for issue in issues)


def test_detects_missing_invoice_lines():

    xml_bytes = _valid_xml_bytes()

    # Odstráň všetko medzi prvým a posledným <cac:InvoiceLine> tagom -
    # jednoduchšie než XML skladať naspäť, tu len potrebujeme XML bez
    # položiek.
    import re

    broken = re.sub(
        rb"<cac:InvoiceLine>.*?</cac:InvoiceLine>",
        b"",
        xml_bytes,
        flags=re.DOTALL
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-16" in issue for issue in issues)


def test_detects_line_extension_amount_mismatch():
    """BR-CO-10 - súčet položiek sa nezhoduje s hlavičkou."""

    xml_bytes = _valid_xml_bytes()

    import re

    # Zisti skutočnú hodnotu LineExtensionAmount v hlavičke
    # (LegalMonetaryTotal je v XML AŽ PO všetkých InvoiceLine
    # elementoch, takže prvý výskyt tagu v poradí "zospodu" - nájdeme
    # ho jednoducho ako posledný v poradí v surovom texte).
    all_matches = list(re.finditer(
        rb'<cbc:LineExtensionAmount currencyID="EUR">([\d.]+)</cbc:LineExtensionAmount>',
        xml_bytes
    ))
    header_match = all_matches[0]  # LegalMonetaryTotal je v XML PRED položkami

    broken_value = str(Decimal(header_match.group(1).decode()) + Decimal("999.00"))

    broken = (
        xml_bytes[:header_match.start()]
        + f'<cbc:LineExtensionAmount currencyID="EUR">{broken_value}</cbc:LineExtensionAmount>'.encode()
        + xml_bytes[header_match.end():]
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-CO-10" in issue for issue in issues)


def test_detects_tax_inclusive_mismatch():
    """BR-CO-15 - TaxInclusiveAmount nesedí s TaxExclusive + TaxAmount."""

    xml_bytes = _valid_xml_bytes()

    # nájdi presnú hodnotu TaxInclusiveAmount a zmeň ju
    import re

    match = re.search(
        rb"<cbc:TaxInclusiveAmount currencyID=\"EUR\">([\d.]+)</cbc:TaxInclusiveAmount>",
        xml_bytes
    )
    assert match is not None

    original = match.group(0)
    broken_value = str(Decimal(match.group(1).decode()) + Decimal("50.00"))
    broken_tag = (
        f'<cbc:TaxInclusiveAmount currencyID="EUR">{broken_value}'
        f'</cbc:TaxInclusiveAmount>'
    ).encode()

    broken = xml_bytes.replace(original, broken_tag)

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-CO-15" in issue for issue in issues)


def test_detects_wrong_vat_percent_calculation():
    """BR-CO-17 - TaxAmount v konkrétnej sadzbe nesedí s
    TaxableAmount × Percent / 100."""

    xml_bytes = _valid_xml_bytes()

    import re

    # nájdi prvý TaxSubtotal a pokaz jeho TaxAmount
    match = re.search(
        rb"(<cac:TaxSubtotal>.*?<cbc:TaxAmount currencyID=\"EUR\">)"
        rb"([\d.]+)(</cbc:TaxAmount>.*?</cac:TaxSubtotal>)",
        xml_bytes,
        re.DOTALL
    )
    assert match is not None

    broken_value = str(Decimal(match.group(2).decode()) + Decimal("5.00"))

    broken = xml_bytes.replace(
        match.group(0),
        match.group(1) + broken_value.encode() + match.group(3)
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any(
        "BR-CO-17" in issue or "BR-CO-14" in issue
        for issue in issues
    )


# =========================================
# NOVÉ PRAVIDLÁ - KAŽDÉ MUSÍ SKUTOČNE ZACHYTIŤ PROBLÉM
# =========================================

def test_detects_missing_invoice_type_code():
    """BR-04"""

    broken = _valid_xml_bytes()

    import re

    broken = re.sub(
        rb"<cbc:InvoiceTypeCode>.*?</cbc:InvoiceTypeCode>",
        b"",
        broken
    )

    assert any("BR-04" in i for i in validate_peppol_invoice_xml(broken))


def test_detects_missing_supplier_endpoint_id():
    """PEPPOL-EN16931-R020 (BT-34)"""

    invoice = make_invoice([make_item()])

    xml_bytes = generate_peppol_xml(
        invoice,
        make_company(peppol_scheme_id=None)
    )

    issues = validate_peppol_invoice_xml(xml_bytes)

    assert any("PEPPOL-EN16931-R020" in i for i in issues)


def test_detects_missing_customer_endpoint_id():
    """PEPPOL-EN16931-R010 (BT-49)"""

    invoice = make_invoice(
        [make_item()],
        customer=make_customer(peppol_scheme_id=None)
    )

    xml_bytes = generate_peppol_xml(invoice, make_company())

    issues = validate_peppol_invoice_xml(xml_bytes)

    assert any("PEPPOL-EN16931-R010" in i for i in issues)


def test_detects_endpoint_id_without_scheme_id():

    import re

    broken = re.sub(
        rb'(<cbc:EndpointID) schemeID="[^"]*"',
        rb"\1",
        _valid_xml_bytes()
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("schemeID" in i for i in issues)


def test_detects_missing_country_codes():
    """BR-09 a BR-11"""

    import re

    broken = re.sub(
        rb"<cbc:IdentificationCode>.*?</cbc:IdentificationCode>",
        b"",
        _valid_xml_bytes()
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-09" in i for i in issues)
    assert any("BR-11" in i for i in issues)


def test_detects_missing_postal_addresses():
    """BR-08 a BR-10"""

    import re

    broken = re.sub(
        rb"<cac:PostalAddress>.*?</cac:PostalAddress>",
        b"",
        _valid_xml_bytes(),
        flags=re.DOTALL
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-08" in i for i in issues)
    assert any("BR-10" in i for i in issues)


def test_detects_missing_line_fields():
    """BR-21 až BR-26"""

    import re

    xml_bytes = _valid_xml_bytes()

    def strip_in_lines(pattern):
        return re.sub(pattern, b"", xml_bytes, flags=re.DOTALL)

    # BR-21 - ID položky (prvé <cbc:ID> vnútri InvoiceLine)
    broken = re.sub(
        rb"(<cac:InvoiceLine>\s*)<cbc:ID>.*?</cbc:ID>",
        rb"\1",
        xml_bytes,
        flags=re.DOTALL
    )
    assert any("BR-21" in i for i in validate_peppol_invoice_xml(broken))

    # BR-22 - množstvo
    broken = strip_in_lines(rb"<cbc:InvoicedQuantity[^>]*>.*?</cbc:InvoicedQuantity>")
    assert any("BR-22" in i for i in validate_peppol_invoice_xml(broken))

    # BR-23 - unitCode
    broken = re.sub(rb' unitCode="[^"]*"', b"", xml_bytes)
    assert any("BR-23" in i for i in validate_peppol_invoice_xml(broken))

    # BR-25 - názov položky
    broken = re.sub(
        rb"(<cac:Item>\s*)<cbc:Name>.*?</cbc:Name>",
        rb"\1",
        xml_bytes,
        flags=re.DOTALL
    )
    assert any("BR-25" in i for i in validate_peppol_invoice_xml(broken))

    # BR-26 - cena položky
    broken = strip_in_lines(rb"<cbc:PriceAmount[^>]*>.*?</cbc:PriceAmount>")
    assert any("BR-26" in i for i in validate_peppol_invoice_xml(broken))


def test_detects_reverse_charge_without_vat_ids():
    """BR-AE-02 - chýba IČ DPH predávajúceho aj odberateľa"""

    invoice = make_invoice(
        [make_item(vat_rate=0)],
        customer=make_customer(ic_dph=None),
        reverse_charge=True
    )

    xml_bytes = generate_peppol_xml(
        invoice,
        make_company(ic_dph=None)
    )

    issues = validate_peppol_invoice_xml(xml_bytes)

    assert sum("BR-AE-02" in i for i in issues) == 2


def test_detects_reverse_charge_nonzero_tax():
    """BR-AE-09 - TaxAmount v kategórii AE musí byť 0"""

    import re

    invoice = make_invoice([make_item(vat_rate=0)], reverse_charge=True)

    xml_bytes = generate_peppol_xml(invoice, make_company())

    broken = re.sub(
        rb'(<cac:TaxSubtotal>.*?<cbc:TaxAmount currencyID="EUR">)[\d.]+',
        rb"\g<1>5.00",
        xml_bytes,
        count=1,
        flags=re.DOTALL
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-AE-09" in i for i in issues)


def test_detects_zero_vat_without_supplier_vat_id():
    """BR-Z-02"""

    invoice = make_invoice([make_item(vat_rate=0)])

    xml_bytes = generate_peppol_xml(
        invoice,
        make_company(ic_dph=None)
    )

    issues = validate_peppol_invoice_xml(xml_bytes)

    assert any("BR-Z-02" in i for i in issues)


def test_customer_country_code_is_used_in_xml():

    invoice = make_invoice(
        [make_item()],
        customer=make_customer(country_code="CZ", peppol_scheme_id="9929")
    )

    xml_bytes = generate_peppol_xml(invoice, make_company())

    customer_part = xml_bytes.split(b"<cac:AccountingCustomerParty>")[1]

    assert b"<cbc:IdentificationCode>CZ</cbc:IdentificationCode>" in customer_part


def test_non_finite_amount_in_xml_is_reported_not_crashing():

    import re

    broken = re.sub(
        rb'(<cbc:PayableAmount currencyID="EUR">)[\d.]+',
        rb"\g<1>NaN",
        _valid_xml_bytes(),
        count=1
    )

    # nesmie vyhodiť výnimku - chýbajúca/neplatná suma sa nahlási
    assert isinstance(validate_peppol_invoice_xml(broken), list)


def test_malformed_xml_returns_parse_issue():

    issues = validate_peppol_invoice_xml(b"<Invoice><nezavrete>")

    assert len(issues) == 1
    assert "naparsovať" in issues[0]


# =========================================
# BR-62, BR-63, BR-CL-25 - schemeID pri EndpointID
# =========================================

def test_detects_removed_eas_scheme_on_supplier():
    """BR-CL-25 - napr. 0037 bol z EAS číselníka vo verzii 3.0.21 odstránený."""

    invoice = make_invoice([make_item()])

    xml_bytes = generate_peppol_xml(
        invoice,
        make_company(peppol_scheme_id="0037", peppol_endpoint_id="123456")
    )

    issues = validate_peppol_invoice_xml(xml_bytes)

    assert any("BR-CL-25" in i for i in issues)


def test_detects_removed_eas_scheme_on_customer():
    """BR-CL-25 na strane odberateľa."""

    invoice = make_invoice(
        [make_item()],
        customer=make_customer(peppol_scheme_id="9901", peppol_endpoint_id="123456")
    )

    xml_bytes = generate_peppol_xml(invoice, make_company())

    issues = validate_peppol_invoice_xml(xml_bytes)

    assert any("BR-CL-25" in i for i in issues)


def test_plausible_but_unknown_eas_scheme_is_not_flagged_by_cl25():
    """Schéma mimo nášho (výberového) zoznamu sa nemá paušálne odmietať."""

    invoice = make_invoice(
        [make_item()],
        customer=make_customer(peppol_scheme_id="0230", peppol_endpoint_id="123456")
    )

    xml_bytes = generate_peppol_xml(invoice, make_company())

    issues = validate_peppol_invoice_xml(xml_bytes)

    assert not any("BR-CL-25" in i for i in issues)


# =========================================
# BR-Z-01, BR-Z-05, BR-Z-08, BR-Z-09, BR-Z-10
# =========================================

def test_detects_zero_rated_line_with_nonzero_rate():
    """BR-Z-05"""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)]),
        make_company()
    )

    # Nastavíme Percent v ClassifiedTaxCategory POLOŽKY (nie v
    # TaxSubtotal - obe majú rovnaký tvar <cbc:ID>Z</cbc:ID>, preto sa
    # kotví na okolitý <cac:ClassifiedTaxCategory> element).
    broken = re.sub(
        rb'(<cac:ClassifiedTaxCategory>\s*<cbc:ID>Z</cbc:ID>\s*<cbc:Percent>)0(</cbc:Percent>)',
        rb"\g<1>5\g<2>",
        xml_bytes,
        count=1
    )

    assert broken != xml_bytes

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-Z-05" in i for i in issues)


def test_detects_zero_rated_taxable_amount_mismatch():
    """BR-Z-08"""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)]),
        make_company()
    )

    broken = re.sub(
        rb'(<cac:TaxCategory>\s*<cbc:ID>Z</cbc:ID>)',
        rb"\g<1>",
        xml_bytes
    )
    broken = re.sub(
        rb'(<cbc:TaxableAmount currencyID="EUR">)[\d.]+(</cbc:TaxableAmount>\s*<cbc:TaxAmount currencyID="EUR">0\.00</cbc:TaxAmount>\s*<cac:TaxCategory>\s*<cbc:ID>Z</cbc:ID>)',
        rb"\g<1>999.00\g<2>",
        xml_bytes,
        count=1
    )

    assert broken != xml_bytes

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-Z-08" in i for i in issues)


def test_detects_zero_rated_nonzero_tax_amount():
    """BR-Z-09"""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)]),
        make_company()
    )

    broken = re.sub(
        rb'(<cbc:TaxAmount currencyID="EUR">)0\.00(</cbc:TaxAmount>\s*<cac:TaxCategory>\s*<cbc:ID>Z</cbc:ID>)',
        rb"\g<1>3.50\g<2>",
        xml_bytes,
        count=1
    )

    assert broken != xml_bytes

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-Z-09" in i for i in issues)


def test_detects_zero_rated_with_forbidden_exemption_reason():
    """BR-Z-10 - kategória Z nesmie mať dôvod oslobodenia."""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)]),
        make_company()
    )

    broken = re.sub(
        rb'(<cac:TaxCategory>\s*<cbc:ID>Z</cbc:ID>\s*<cbc:Percent>0</cbc:Percent>)',
        rb'\g<1><cbc:TaxExemptionReasonCode>VATEX-EU-O</cbc:TaxExemptionReasonCode>',
        xml_bytes,
        count=1
    )

    assert broken != xml_bytes

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-Z-10" in i for i in issues)


def test_detects_zero_rated_subtotal_duplicated():
    """BR-Z-01 - viac ako jeden TaxSubtotal s kategóriou Z je chyba."""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)]),
        make_company()
    )

    match = re.search(
        rb'<cac:TaxSubtotal>.*?</cac:TaxSubtotal>',
        xml_bytes,
        flags=re.DOTALL
    )
    duplicated_subtotal = match.group(0)

    broken = xml_bytes.replace(
        b"</cac:TaxTotal>",
        duplicated_subtotal + b"</cac:TaxTotal>",
        1
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-Z-01" in i for i in issues)


# =========================================
# BR-AE-01, BR-AE-05, BR-AE-08, BR-AE-10
# =========================================

def test_detects_reverse_charge_line_with_nonzero_rate():
    """BR-AE-05"""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)], reverse_charge=True),
        make_company()
    )

    broken = re.sub(
        rb'(<cac:ClassifiedTaxCategory>\s*<cbc:ID>AE</cbc:ID>\s*<cbc:Percent>)0(</cbc:Percent>)',
        rb"\g<1>19\g<2>",
        xml_bytes,
        count=1
    )

    assert broken != xml_bytes

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-AE-05" in i for i in issues)


def test_detects_reverse_charge_taxable_amount_mismatch():
    """BR-AE-08"""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)], reverse_charge=True),
        make_company()
    )

    broken = re.sub(
        rb'(<cbc:TaxableAmount currencyID="EUR">)[\d.]+(</cbc:TaxableAmount>\s*<cbc:TaxAmount currencyID="EUR">0\.00</cbc:TaxAmount>\s*<cac:TaxCategory>\s*<cbc:ID>AE</cbc:ID>)',
        rb"\g<1>555.00\g<2>",
        xml_bytes,
        count=1
    )

    assert broken != xml_bytes

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-AE-08" in i for i in issues)


def test_detects_reverse_charge_missing_exemption_reason():
    """BR-AE-10 - kategória AE musí mať dôvod oslobodenia."""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)], reverse_charge=True),
        make_company()
    )

    broken = re.sub(
        rb'<cbc:TaxExemptionReasonCode>[^<]*</cbc:TaxExemptionReasonCode>',
        b"",
        xml_bytes
    )
    broken = re.sub(
        rb'<cbc:TaxExemptionReason>[^<]*</cbc:TaxExemptionReason>',
        b"",
        broken
    )

    assert broken != xml_bytes

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-AE-10" in i for i in issues)


def test_detects_reverse_charge_subtotal_duplicated():
    """BR-AE-01 - viac ako jeden TaxSubtotal s kategóriou AE je chyba."""

    import re

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item(vat_rate=0)], reverse_charge=True),
        make_company()
    )

    match = re.search(
        rb'<cac:TaxSubtotal>.*?</cac:TaxSubtotal>',
        xml_bytes,
        flags=re.DOTALL
    )
    duplicated_subtotal = match.group(0)

    broken = xml_bytes.replace(
        b"</cac:TaxTotal>",
        duplicated_subtotal + b"</cac:TaxTotal>",
        1
    )

    issues = validate_peppol_invoice_xml(broken)

    assert any("BR-AE-01" in i for i in issues)


# =========================================
# peppol_endpoint_id (samotná hodnota adresy, oddelená od schémy)
# =========================================

def test_endpoint_id_value_is_not_ico():
    """
    Regresný test na hlavný nález: EndpointID musí niesť
    peppol_endpoint_id, NIE ico, aj keď sa (náhodou) líšia.
    """

    customer = make_customer(
        ico="10482245",
        peppol_scheme_id="9950",
        peppol_endpoint_id="SK9999999999"
    )

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item()], customer=customer),
        make_company()
    )

    customer_part = xml_bytes.split(b"<cac:AccountingCustomerParty>")[1]

    assert b"<cbc:EndpointID schemeID=\"9950\">SK9999999999</cbc:EndpointID>" in customer_part
    assert b">10482245<" not in customer_part.split(b"<cbc:EndpointID")[1].split(b"</cbc:EndpointID>")[0]


def test_endpoint_id_omitted_when_only_scheme_set_without_ico_fallback():
    """
    Ak appka (napr. cez priamy zápis do DB mimo formulára) má schému
    bez peppol_endpoint_id, EndpointID sa nemá vygenerovať s IČO ako
    tichým náhradným riešením - radšej nič, než nesprávna hodnota.
    """

    customer = make_customer(
        ico="10482245",
        peppol_scheme_id="9950",
        peppol_endpoint_id=None
    )

    xml_bytes = generate_peppol_xml(
        make_invoice([make_item()], customer=customer),
        make_company()
    )

    customer_part = xml_bytes.split(b"<cac:AccountingCustomerParty>")[1]

    assert b"<cbc:EndpointID" not in customer_part
