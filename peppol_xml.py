"""
Generovanie faktúry vo formáte UBL 2.1 / Peppol BIS Billing 3.0.

DÔLEŽITÉ: Toto je príprava dát do štandardizovaného formátu, NIE reálne
odoslanie cez Peppol sieť. Vygenerovaný XML treba pred ostrým použitím
overiť u certifikovaného poskytovateľa (tzv. Digitálny poštár), ktorý
appku napojí na skutočnú Peppol sieť a potvrdí presné identifikačné
kódy (schéma Participant ID a pod.).

Referencia: OpenPEPPOL Peppol BIS Billing 3.0
https://github.com/OpenPEPPOL/peppol-bis-invoice-3
"""

from decimal import Decimal
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring

from invoice_utils import calculate_invoice_totals


# =========================================
# NÁZVOVÉ PRIESTORY (UBL 2.1)
# =========================================

NS_INVOICE = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
NS_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
NS_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

# Peppol validátory očakávajú konkrétne prefixy cac:/cbc: (nie automaticky
# generované ns0/ns1/...), preto ich zaregistrujeme explicitne.
register_namespace("", NS_INVOICE)
register_namespace("cac", NS_CAC)
register_namespace("cbc", NS_CBC)

CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

DEFAULT_COUNTRY_CODE = "SK"


# =========================================
# MAPOVANIE MERNÝCH JEDNOTIEK NA UN/ECE REC 20 KÓDY
# (Peppol/UBL vyžaduje kódovanú mernú jednotku, nie voľný text)
# =========================================

UNIT_CODE_MAP = {
    "ks": "C62",
    "kus": "C62",
    "kusy": "C62",
    "hod": "HUR",
    "hodina": "HUR",
    "hodiny": "HUR",
    "deň": "DAY",
    "den": "DAY",
    "dni": "DAY",
    "m": "MTR",
    "m2": "MTK",
    "m3": "MTQ",
    "kg": "KGM",
    "t": "TNE",
    "l": "LTR",
    "km": "KMT",
    "bal": "PK",
    "balenie": "PK",
    "sada": "SET",
    "kpl": "SET",
}


def map_unit_code(unit: str) -> str:

    normalized = (unit or "").strip().lower()

    return UNIT_CODE_MAP.get(normalized, "C62")


def vat_category_code(vat_rate: int) -> str:
    """
    Peppol/UBL kód kategórie DPH podľa UNCL5305.
    S = štandardná/znížená sadzba (percento je v samostatnom elemente),
    Z = nulová sadzba.
    """

    if vat_rate == 0:
        return "Z"

    return "S"


def money(value: Decimal) -> str:

    return f"{value:.2f}"


# =========================================
# POMOCNÉ FUNKCIE PRE STAVBU XML STROMU
# =========================================

def qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def add_text(parent: Element, namespace: str, tag: str, text: str, **attrib) -> Element:

    el = SubElement(parent, qn(namespace, tag), attrib)
    el.text = text

    return el


def build_postal_address(parent: Element, address: str | None, city: str | None, zip_code: str | None, country_code: str = DEFAULT_COUNTRY_CODE) -> Element:

    postal_address = SubElement(parent, qn(NS_CAC, "PostalAddress"))

    if address:
        add_text(postal_address, NS_CBC, "StreetName", address)

    if city:
        add_text(postal_address, NS_CBC, "CityName", city)

    if zip_code:
        add_text(postal_address, NS_CBC, "PostalZone", zip_code)

    country = SubElement(postal_address, qn(NS_CAC, "Country"))
    add_text(country, NS_CBC, "IdentificationCode", country_code)

    return postal_address


def build_party(
    parent_tag_name: str,
    root: Element,
    name: str,
    ico: str | None,
    ic_dph: str | None,
    address: str | None,
    city: str | None = None,
    zip_code: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    peppol_scheme_id: str | None = None
) -> Element:

    party_wrapper = SubElement(root, qn(NS_CAC, parent_tag_name))
    party = SubElement(party_wrapper, qn(NS_CAC, "Party"))


    if peppol_scheme_id and ico:

        add_text(
            party,
            NS_CBC,
            "EndpointID",
            ico,
            schemeID=peppol_scheme_id
        )


    if name:

        party_name = SubElement(party, qn(NS_CAC, "PartyName"))
        add_text(party_name, NS_CBC, "Name", name)


    build_postal_address(party, address, city, zip_code)


    if ic_dph:

        tax_scheme_wrapper = SubElement(party, qn(NS_CAC, "PartyTaxScheme"))
        add_text(tax_scheme_wrapper, NS_CBC, "CompanyID", ic_dph)

        tax_scheme = SubElement(tax_scheme_wrapper, qn(NS_CAC, "TaxScheme"))
        add_text(tax_scheme, NS_CBC, "ID", "VAT")


    legal_entity = SubElement(party, qn(NS_CAC, "PartyLegalEntity"))
    add_text(legal_entity, NS_CBC, "RegistrationName", name or "")

    if ico:
        add_text(legal_entity, NS_CBC, "CompanyID", ico)


    if email or phone:

        contact = SubElement(party, qn(NS_CAC, "Contact"))

        if phone:
            add_text(contact, NS_CBC, "Telephone", phone)

        if email:
            add_text(contact, NS_CBC, "ElectronicMail", email)


    return party_wrapper


# =========================================
# HLAVNÁ FUNKCIA
# =========================================

def generate_peppol_xml(invoice, company) -> bytes:
    """
    Vygeneruje UBL 2.1 / Peppol BIS Billing 3.0 XML pre danú faktúru.

    `invoice` - Invoice (SQLAlchemy model, s načítanými items a customer)
    `company` - Company (SQLAlchemy model) alebo None
    """

    root = Element(
        qn(NS_INVOICE, "Invoice")
    )


    add_text(root, NS_CBC, "CustomizationID", CUSTOMIZATION_ID)
    add_text(root, NS_CBC, "ProfileID", PROFILE_ID)
    add_text(root, NS_CBC, "ID", invoice.invoice_number)
    add_text(root, NS_CBC, "IssueDate", invoice.issue_date.isoformat())
    add_text(root, NS_CBC, "DueDate", invoice.due_date.isoformat())
    add_text(root, NS_CBC, "InvoiceTypeCode", "380")

    if invoice.note:
        add_text(root, NS_CBC, "Note", invoice.note)

    add_text(root, NS_CBC, "DocumentCurrencyCode", "EUR")

    add_text(
        root,
        NS_CBC,
        "BuyerReference",
        invoice.variable_symbol or invoice.invoice_number
    )


    # =====================================
    # DODÁVATEĽ
    # =====================================

    company_name = company.name if company else ""
    company_ico = company.ico if company else None
    company_ic_dph = company.ic_dph if company else None
    company_address = company.address if company else None
    company_city = company.city if company else None
    company_zip = company.zip_code if company else None
    company_email = company.email if company else None
    company_phone = company.phone if company else None
    company_peppol_scheme = company.peppol_scheme_id if company else None

    build_party(
        "AccountingSupplierParty",
        root,
        name=company_name,
        ico=company_ico,
        ic_dph=company_ic_dph,
        address=company_address,
        city=company_city,
        zip_code=company_zip,
        email=company_email,
        phone=company_phone,
        peppol_scheme_id=company_peppol_scheme
    )


    # =====================================
    # ODBERATEĽ
    # =====================================

    customer = invoice.customer

    build_party(
        "AccountingCustomerParty",
        root,
        name=customer.name,
        ico=customer.ico,
        ic_dph=customer.ic_dph,
        address=customer.address,
        email=customer.email,
        phone=customer.phone,
        peppol_scheme_id=company_peppol_scheme
    )


    # =====================================
    # SPÔSOB PLATBY
    # =====================================

    if company and company.iban:

        payment_means = SubElement(root, qn(NS_CAC, "PaymentMeans"))

        add_text(payment_means, NS_CBC, "PaymentMeansCode", "30")

        add_text(
            payment_means,
            NS_CBC,
            "PaymentID",
            invoice.variable_symbol or invoice.invoice_number
        )

        payee_account = SubElement(payment_means, qn(NS_CAC, "PayeeFinancialAccount"))
        add_text(payee_account, NS_CBC, "ID", company.iban)

        if company.swift_bic:

            branch = SubElement(payee_account, qn(NS_CAC, "FinancialInstitutionBranch"))
            add_text(branch, NS_CBC, "ID", company.swift_bic)


    # =====================================
    # SÚČTY DPH
    # =====================================

    totals = calculate_invoice_totals(invoice.items)

    tax_total = SubElement(root, qn(NS_CAC, "TaxTotal"))

    add_text(
        tax_total,
        NS_CBC,
        "TaxAmount",
        money(totals["total_vat"]),
        currencyID="EUR"
    )

    for row in totals["vat_breakdown"]:

        tax_subtotal = SubElement(tax_total, qn(NS_CAC, "TaxSubtotal"))

        add_text(
            tax_subtotal,
            NS_CBC,
            "TaxableAmount",
            money(row["base"]),
            currencyID="EUR"
        )

        add_text(
            tax_subtotal,
            NS_CBC,
            "TaxAmount",
            money(row["vat"]),
            currencyID="EUR"
        )

        tax_category = SubElement(tax_subtotal, qn(NS_CAC, "TaxCategory"))

        add_text(
            tax_category,
            NS_CBC,
            "ID",
            vat_category_code(row["rate"])
        )

        add_text(
            tax_category,
            NS_CBC,
            "Percent",
            str(row["rate"])
        )

        tax_scheme = SubElement(tax_category, qn(NS_CAC, "TaxScheme"))
        add_text(tax_scheme, NS_CBC, "ID", "VAT")


    # =====================================
    # CELKOVÉ SUMY
    # =====================================

    legal_monetary_total = SubElement(root, qn(NS_CAC, "LegalMonetaryTotal"))

    add_text(
        legal_monetary_total,
        NS_CBC,
        "LineExtensionAmount",
        money(totals["total_base"]),
        currencyID="EUR"
    )

    add_text(
        legal_monetary_total,
        NS_CBC,
        "TaxExclusiveAmount",
        money(totals["total_base"]),
        currencyID="EUR"
    )

    add_text(
        legal_monetary_total,
        NS_CBC,
        "TaxInclusiveAmount",
        money(totals["total_gross"]),
        currencyID="EUR"
    )

    add_text(
        legal_monetary_total,
        NS_CBC,
        "PayableAmount",
        money(totals["total_gross"]),
        currencyID="EUR"
    )


    # =====================================
    # POLOŽKY FAKTÚRY
    # =====================================

    for index, item in enumerate(invoice.items, start=1):

        quantity = Decimal(str(item.quantity))
        unit_price = Decimal(str(item.unit_price))
        line_amount = (quantity * unit_price).quantize(Decimal("0.01"))

        unit_code = map_unit_code(item.unit)


        invoice_line = SubElement(root, qn(NS_CAC, "InvoiceLine"))

        add_text(invoice_line, NS_CBC, "ID", str(index))

        add_text(
            invoice_line,
            NS_CBC,
            "InvoicedQuantity",
            f"{quantity:g}",
            unitCode=unit_code
        )

        add_text(
            invoice_line,
            NS_CBC,
            "LineExtensionAmount",
            money(line_amount),
            currencyID="EUR"
        )

        line_item = SubElement(invoice_line, qn(NS_CAC, "Item"))
        add_text(line_item, NS_CBC, "Name", item.description)

        classified_tax_category = SubElement(line_item, qn(NS_CAC, "ClassifiedTaxCategory"))

        add_text(
            classified_tax_category,
            NS_CBC,
            "ID",
            vat_category_code(item.vat_rate)
        )

        add_text(
            classified_tax_category,
            NS_CBC,
            "Percent",
            str(item.vat_rate)
        )

        line_tax_scheme = SubElement(classified_tax_category, qn(NS_CAC, "TaxScheme"))
        add_text(line_tax_scheme, NS_CBC, "ID", "VAT")

        price = SubElement(invoice_line, qn(NS_CAC, "Price"))

        add_text(
            price,
            NS_CBC,
            "PriceAmount",
            money(unit_price),
            currencyID="EUR"
        )


    # =====================================
    # SERIALIZÁCIA
    # =====================================

    raw_xml = tostring(root, encoding="utf-8")

    pretty_xml = minidom.parseString(raw_xml).toprettyxml(
        indent="  ",
        encoding="UTF-8"
    )

    return pretty_xml
