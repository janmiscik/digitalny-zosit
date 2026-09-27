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
        peppol_scheme_id=None
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
        peppol_scheme_id="9946",
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

    assert issues == ["BR-06: Chýba meno predávajúceho (BT-27)."]


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
