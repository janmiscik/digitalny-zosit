from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import ParagraphStyle

from invoice_pdf import _register_fonts, format_date, scaled_image
from uploads_utils import image_path


def generate_delivery_note_pdf(

    customer,
    items,
    document_number: str,
    document_label: str,
    issue_date: date,
    company

) -> bytes:
    """
    Vygeneruje dodací list - zoznam odovzdaných položiek BEZ CIEN
    (dodací list nie je daňový/platobný doklad), s podpisovými riadkami
    pre potvrdenie prevzatia. Dá sa vygenerovať z faktúry aj z ponuky -
    `document_number`/`document_label` len popisujú, z čoho vznikol
    (napr. "Faktúra č. 2026001" alebo "Ponuka č. CP2026001").
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

    }


    story = []


    # =====================================
    # HLAVIČKA
    # =====================================

    title_block = [
        Paragraph("DODACÍ LIST", styles["doctype"]),
        Paragraph(
            f"č. {document_number}",
            styles["title"]
        ),
        Paragraph(
            f"Vystavené na základe: {document_label}",
            styles["small"]
        ),
        Paragraph(
            f"Dátum: {format_date(issue_date)}",
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

    customer_lines = [
        Paragraph("Odberateľ", styles["heading"]),
        Paragraph(customer.name, styles["bold"])
    ]

    if customer.address:
        customer_lines.append(Paragraph(customer.address, styles["normal"]))


    parties_table = Table(
        [[supplier_lines, customer_lines]],
        colWidths=[85 * mm, 85 * mm]
    )

    parties_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ])
    )

    story.append(parties_table)
    story.append(Spacer(1, 20))


    # =====================================
    # POLOŽKY - ZÁMERNE BEZ CIEN
    # =====================================

    items_rows = [[
        Paragraph("Popis", styles["bold"]),
        Paragraph("Množstvo", styles["bold"]),
        Paragraph("MJ", styles["bold"]),
    ]]

    for item in items:

        items_rows.append([
            Paragraph(item.description, styles["normal"]),
            Paragraph(f"{item.quantity:g}", styles["normal"]),
            Paragraph(item.unit, styles["normal"]),
        ])

    items_table = Table(
        items_rows,
        colWidths=[110 * mm, 35 * mm, 25 * mm]
    )

    items_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E2B2F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#1E2B2F")),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#E4E4DC")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )

    story.append(items_table)
    story.append(Spacer(1, 60))


    # =====================================
    # PODPISY
    # =====================================

    signature_table = Table(
        [
            ["", ""],
            ["....................................", "...................................."],
            ["Odovzdal (dodávateľ)", "Prevzal (odberateľ)"],
        ],
        colWidths=[85 * mm, 85 * mm]
    )

    signature_table.setStyle(
        TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 2), (-1, 2), "DejaVuSans"),
            ("FONTSIZE", (0, 2), (-1, 2), 9),
            ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor("#62676d")),
            ("TOPPADDING", (0, 2), (-1, 2), 4),
        ])
    )

    story.append(signature_table)


    doc.build(story)

    return buffer.getvalue()
