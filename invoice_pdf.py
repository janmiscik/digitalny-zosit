import os
from datetime import date
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Image,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import ParagraphStyle
from PIL import Image as PILImage

from invoice_utils import calculate_invoice_totals
from qr_payment import generate_payment_qr_image
from uploads_utils import image_path


def scaled_image(path, max_width_mm: float, max_height_mm: float) -> Image:
    """
    Vytvorí reportlab Image flowable s rozmermi zmenšenými tak,
    aby sa zmestil do zadaného boxu a zachoval si pomer strán.
    """

    with PILImage.open(path) as pil_img:
        original_width, original_height = pil_img.size

    max_width = max_width_mm * mm
    max_height = max_height_mm * mm

    width_ratio = max_width / original_width
    height_ratio = max_height / original_height
    scale = min(width_ratio, height_ratio)

    return Image(
        str(path),
        width=original_width * scale,
        height=original_height * scale
    )


# =========================================
# FONT (DejaVu Sans - podporuje slovenskú diakritiku,
# štandardné PDF fonty ju nepodporujú)
# =========================================

FONTS_DIR = os.path.join(
    os.path.dirname(__file__),
    "static",
    "fonts"
)

_fonts_registered = False


def _register_fonts():

    global _fonts_registered

    if _fonts_registered:
        return

    pdfmetrics.registerFont(
        TTFont("DejaVuSans", os.path.join(FONTS_DIR, "DejaVuSans.ttf"))
    )

    pdfmetrics.registerFont(
        TTFont("DejaVuSans-Bold", os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf"))
    )

    _fonts_registered = True


def format_money(value: Decimal) -> str:

    return f"{value:.2f} €"


def format_date(value: date | None) -> str:

    if value is None:
        return "-"

    return value.strftime("%d.%m.%Y")


DEFAULT_THANK_YOU_NOTE = "Ďakujeme za využitie našich služieb."


def generate_invoice_pdf(invoice, company) -> bytes:
    """
    Vygeneruje PDF faktúru.

    `invoice` - Invoice (SQLAlchemy model, s načítanými items a customer)
    `company` - Company (SQLAlchemy model) alebo None, ak si používateľ
                ešte nevyplnil fakturačné údaje firmy
    """

    _register_fonts()

    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm
    )


    styles = {

        "doctype": ParagraphStyle(
            "doctype",
            fontName="DejaVuSans-Bold",
            fontSize=13,
            textColor=colors.HexColor("#8A9184"),
            leading=15
        ),

        "title": ParagraphStyle(
            "title",
            fontName="DejaVuSans-Bold",
            fontSize=22,
            leading=27,
        ),

        "normal": ParagraphStyle(
            "normal",
            fontName="DejaVuSans",
            fontSize=9,
            leading=13
        ),

        "bold": ParagraphStyle(
            "bold",
            fontName="DejaVuSans-Bold",
            fontSize=9,
            leading=13
        ),

        "heading": ParagraphStyle(
            "heading",
            fontName="DejaVuSans-Bold",
            fontSize=10,
            spaceAfter=4
        ),

        "small": ParagraphStyle(
            "small",
            fontName="DejaVuSans",
            fontSize=8,
            leading=11,
            textColor=colors.HexColor("#62676d")
        ),

        "summary_label": ParagraphStyle(
            "summary_label",
            fontName="DejaVuSans",
            fontSize=10,
            leading=16,
            textColor=colors.HexColor("#3a3f37")
        ),

        "summary_value": ParagraphStyle(
            "summary_value",
            fontName="DejaVuSans-Bold",
            fontSize=10,
            leading=16,
            alignment=2
        ),

        "summary_total_label": ParagraphStyle(
            "summary_total_label",
            fontName="DejaVuSans-Bold",
            fontSize=13,
            leading=20
        ),

        "summary_total_value": ParagraphStyle(
            "summary_total_value",
            fontName="DejaVuSans-Bold",
            fontSize=16,
            leading=20,
            alignment=2
        )

    }


    story = []


    # =====================================
    # HLAVIČKA - veľký nadpis FAKTÚRA + číslo
    # =====================================

    title_block = [
        Paragraph("FAKTÚRA", styles["doctype"]),
        Paragraph(
            f"č. {invoice.invoice_number}",
            styles["title"]
        ),
        Paragraph(
            f"Variabilný symbol: {invoice.variable_symbol or invoice.invoice_number}",
            styles["small"]
        )
    ]

    logo_file = image_path(company.logo_filename) if company else None

    if logo_file is not None:

        logo_flowable = scaled_image(logo_file, max_width_mm=45, max_height_mm=20)

        title_table = Table(
            [[title_block, logo_flowable]],
            colWidths=[125 * mm, 45 * mm]
        )

        title_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ])
        )

        story.append(title_table)

    else:

        story.extend(title_block)

    story.append(Spacer(1, 16))


    # =====================================
    # DODÁVATEĽ / ODBERATEĽ
    # =====================================

    supplier_lines = [
        Paragraph("Dodávateľ", styles["heading"])
    ]

    if company is not None:

        supplier_lines.append(Paragraph(company.name, styles["bold"]))

        if company.address:
            supplier_lines.append(Paragraph(company.address, styles["normal"]))

        city_line = " ".join(
            part
            for part in [company.zip_code, company.city]
            if part
        )

        if city_line:
            supplier_lines.append(Paragraph(city_line, styles["normal"]))

        if company.ico:
            supplier_lines.append(Paragraph(f"IČO: {company.ico}", styles["normal"]))

        if company.dic:
            supplier_lines.append(Paragraph(f"DIČ: {company.dic}", styles["normal"]))

        if company.ic_dph:
            supplier_lines.append(Paragraph(f"IČ DPH: {company.ic_dph}", styles["normal"]))

        if company.phone:
            supplier_lines.append(Paragraph(company.phone, styles["normal"]))

        if company.email:
            supplier_lines.append(Paragraph(company.email, styles["normal"]))

        if company.website:
            supplier_lines.append(Paragraph(company.website, styles["normal"]))

    else:

        supplier_lines.append(
            Paragraph(
                "Fakturačné údaje firmy nie sú vyplnené (Nastavenia).",
                styles["normal"]
            )
        )


    customer = invoice.customer

    customer_lines = [
        Paragraph("Odberateľ", styles["heading"]),
        Paragraph(customer.name, styles["bold"])
    ]

    if customer.address:
        customer_lines.append(Paragraph(customer.address, styles["normal"]))

    if customer.ico:
        customer_lines.append(Paragraph(f"IČO: {customer.ico}", styles["normal"]))

    if customer.dic:
        customer_lines.append(Paragraph(f"DIČ: {customer.dic}", styles["normal"]))

    if customer.ic_dph:
        customer_lines.append(Paragraph(f"IČ DPH: {customer.ic_dph}", styles["normal"]))

    if customer.phone:
        customer_lines.append(Paragraph(customer.phone, styles["normal"]))

    if customer.email:
        customer_lines.append(Paragraph(customer.email, styles["normal"]))


    header_table = Table(
        [[supplier_lines, customer_lines]],
        colWidths=[85 * mm, 85 * mm]
    )

    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )

    story.append(header_table)

    story.append(Spacer(1, 14))


    # =====================================
    # DÔLEŽITÉ DÁTUMY - výrazný blok
    # =====================================

    dates_data = [
        [
            Paragraph("Dátum vystavenia", styles["small"]),
            Paragraph("Dátum dodania", styles["small"]),
            Paragraph("Dátum splatnosti", styles["small"]),
        ],
        [
            Paragraph(format_date(invoice.issue_date), styles["bold"]),
            Paragraph(format_date(invoice.delivery_date or invoice.issue_date), styles["bold"]),
            Paragraph(format_date(invoice.due_date), styles["bold"]),
        ]
    ]

    dates_table = Table(
        dates_data,
        colWidths=[56.6 * mm, 56.6 * mm, 56.6 * mm]
    )

    dates_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1F1E7")),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#DBDCCC")),
            ("INNERGRID", (0, 0), (-1, -1), 0.75, colors.HexColor("#DBDCCC")),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ])
    )

    story.append(dates_table)

    story.append(Spacer(1, 18))


    # =====================================
    # POLOŽKY
    # =====================================

    items_header = [
        Paragraph("Popis", styles["bold"]),
        Paragraph("Množ.", styles["bold"]),
        Paragraph("MJ", styles["bold"]),
        Paragraph("Cena/MJ", styles["bold"]),
        Paragraph("DPH", styles["bold"]),
        Paragraph("Spolu", styles["bold"]),
    ]

    items_rows = [items_header]

    for item in invoice.items:

        quantity = Decimal(str(item.quantity))
        unit_price = Decimal(str(item.unit_price))

        line_total = (quantity * unit_price)

        items_rows.append([
            Paragraph(item.description, styles["normal"]),
            Paragraph(f"{quantity:g}", styles["normal"]),
            Paragraph(item.unit, styles["normal"]),
            Paragraph(format_money(unit_price), styles["normal"]),
            Paragraph(f"{item.vat_rate} %", styles["normal"]),
            Paragraph(format_money(line_total), styles["normal"]),
        ])


    items_table = Table(
        items_rows,
        colWidths=[70 * mm, 15 * mm, 15 * mm, 25 * mm, 15 * mm, 30 * mm]
    )

    items_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E2B2F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#1E2B2F")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#e1e4e8")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ])
    )

    story.append(items_table)

    story.append(Spacer(1, 18))


    # =====================================
    # SÚHRN - základ dane / DPH / CELKOM K ÚHRADE
    # (výrazný zvýraznený blok)
    # =====================================

    totals = calculate_invoice_totals(invoice.items)

    summary_rows = [
        [
            Paragraph("Základ dane", styles["summary_label"]),
            Paragraph(format_money(totals["total_base"]), styles["summary_value"])
        ],
        [
            Paragraph("DPH spolu", styles["summary_label"]),
            Paragraph(format_money(totals["total_vat"]), styles["summary_value"])
        ],
        [
            Paragraph("CELKOM K ÚHRADE", styles["summary_total_label"]),
            Paragraph(format_money(totals["total_gross"]), styles["summary_total_value"])
        ],
    ]

    summary_table = Table(
        summary_rows,
        colWidths=[100 * mm, 70 * mm]
    )

    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F1F1E7")),
            ("LINEABOVE", (0, 2), (-1, 2), 1, colors.HexColor("#1E2B2F")),
            ("TOPPADDING", (0, 0), (-1, 1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, 1), 3),
            ("TOPPADDING", (0, 2), (-1, 2), 8),
            ("BOTTOMPADDING", (0, 2), (-1, 2), 8),
            ("LEFTPADDING", (0, 2), (0, 2), 8),
            ("RIGHTPADDING", (-1, 2), (-1, 2), 8),
        ])
    )

    story.append(summary_table)

    story.append(Spacer(1, 14))


    # =====================================
    # REKAPITULÁCIA DPH PODĽA SADZIEB
    # =====================================

    if len(totals["vat_breakdown"]) > 1:

        vat_rows = [[
            Paragraph("Sadzba DPH", styles["bold"]),
            Paragraph("Základ", styles["bold"]),
            Paragraph("DPH", styles["bold"]),
            Paragraph("Spolu", styles["bold"]),
        ]]

        for row in totals["vat_breakdown"]:

            vat_rows.append([
                Paragraph(f"{row['rate']} %", styles["normal"]),
                Paragraph(format_money(row["base"]), styles["normal"]),
                Paragraph(format_money(row["vat"]), styles["normal"]),
                Paragraph(format_money(row["gross"]), styles["normal"]),
            ])


        vat_table = Table(
            vat_rows,
            colWidths=[30 * mm, 40 * mm, 40 * mm, 40 * mm]
        )

        vat_table.setStyle(
            TableStyle([
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#1E2B2F")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#e1e4e8")),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )

        story.append(vat_table)

        story.append(Spacer(1, 18))


    # =====================================
    # PLATOBNÉ ÚDAJE + QR KÓD
    # =====================================

    payment_lines = [
        Paragraph("Platobné údaje", styles["heading"]),
        Paragraph(
            f"Spôsob úhrady: {invoice.payment_method or 'Prevodom'}",
            styles["normal"]
        ),
    ]

    if company is not None and company.iban:

        payment_lines.append(
            Paragraph(f"IBAN: {company.iban}", styles["normal"])
        )

    if company is not None and company.swift_bic:

        payment_lines.append(
            Paragraph(f"SWIFT/BIC: {company.swift_bic}", styles["normal"])
        )

    payment_lines.append(
        Paragraph(
            f"Variabilný symbol: {invoice.variable_symbol or invoice.invoice_number}",
            styles["normal"]
        )
    )


    qr_buffer = None

    if company is not None and company.iban:

        qr_buffer = generate_payment_qr_image(
            iban=company.iban,
            amount=totals["total_gross"],
            variable_symbol=invoice.variable_symbol or invoice.invoice_number,
            beneficiary_name=company.name,
            swift=company.swift_bic,
            note=f"Faktura {invoice.invoice_number}"
        )


    if qr_buffer is not None:

        qr_image = Image(
            qr_buffer,
            width=30 * mm,
            height=30 * mm
        )

        payment_table = Table(
            [[payment_lines, qr_image]],
            colWidths=[135 * mm, 35 * mm]
        )

        payment_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ])
        )

        story.append(payment_table)

    else:

        story.extend(payment_lines)

    story.append(Spacer(1, 18))


    # =====================================
    # POZNÁMKA
    # =====================================

    story.append(Paragraph(DEFAULT_THANK_YOU_NOTE, styles["normal"]))

    if invoice.note:

        story.append(Spacer(1, 8))

        story.append(Paragraph(invoice.note, styles["normal"]))


    # =====================================
    # PODPIS / PEČIATKA
    # =====================================

    signature_file = image_path(company.signature_filename) if company else None

    if signature_file is not None:

        story.append(Spacer(1, 24))

        signature_flowable = scaled_image(signature_file, max_width_mm=50, max_height_mm=25)

        signature_table = Table(
            [[signature_flowable]],
            colWidths=[170 * mm]
        )

        signature_table.setStyle(
            TableStyle([
                ("ALIGN", (0, 0), (0, 0), "RIGHT"),
            ])
        )

        story.append(signature_table)


    doc.build(story)


    return buffer.getvalue()
