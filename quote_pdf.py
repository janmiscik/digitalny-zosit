from decimal import Decimal
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

from invoice_pdf import (
    DEFAULT_THANK_YOU_NOTE,
    _register_fonts,
    format_date,
    format_money,
    scaled_image,
)
from invoice_utils import calculate_invoice_totals
from uploads_utils import image_path


def generate_quote_pdf(quote, company) -> bytes:
    """
    Vygeneruje PDF cenovej ponuky. Zámerne vynecháva platobné údaje a QR
    kód (ponuka sa ešte nemá platiť) - inak zdieľa vizuálny štýl s
    faktúrou (viď invoice_pdf.py), nech appka pôsobí jednotne.
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
    # HLAVIČKA
    # =====================================

    validity_line = (
        f"Platná do {format_date(quote.valid_until)}"
        if quote.valid_until
        else "Platnosť nie je časovo obmedzená"
    )

    title_block = [
        Paragraph("CENOVÁ PONUKA", styles["doctype"]),
        Paragraph(
            f"č. {quote.quote_number}",
            styles["title"]
        ),
        Paragraph(
            validity_line,
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

        if company.ic_dph and company.is_vat_payer:
            supplier_lines.append(Paragraph(f"IČ DPH: {company.ic_dph}", styles["normal"]))

    customer = quote.customer

    customer_lines = [
        Paragraph("Odberateľ", styles["heading"]),
        Paragraph(customer.name, styles["bold"])
    ]

    if customer.address:
        customer_lines.append(Paragraph(customer.address, styles["normal"]))

    if customer.ico:
        customer_lines.append(Paragraph(f"IČO: {customer.ico}", styles["normal"]))

    if customer.ic_dph:
        customer_lines.append(Paragraph(f"IČ DPH: {customer.ic_dph}", styles["normal"]))


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
    # POLOŽKY
    # =====================================

    company_is_vat_payer = bool(company is not None and company.is_vat_payer)

    if company_is_vat_payer:

        items_header = [
            Paragraph("Popis", styles["bold"]),
            Paragraph("Množ.", styles["bold"]),
            Paragraph("MJ", styles["bold"]),
            Paragraph("Cena/MJ", styles["bold"]),
            Paragraph("DPH", styles["bold"]),
            Paragraph("Spolu", styles["bold"]),
        ]

        items_col_widths = [70 * mm, 15 * mm, 15 * mm, 25 * mm, 15 * mm, 30 * mm]

    else:

        items_header = [
            Paragraph("Popis", styles["bold"]),
            Paragraph("Množ.", styles["bold"]),
            Paragraph("MJ", styles["bold"]),
            Paragraph("Cena/MJ", styles["bold"]),
            Paragraph("Spolu", styles["bold"]),
        ]

        items_col_widths = [80 * mm, 20 * mm, 20 * mm, 25 * mm, 25 * mm]

    items_rows = [items_header]

    for item in quote.items:

        quantity = Decimal(str(item.quantity))
        unit_price = Decimal(str(item.unit_price))

        line_total = quantity * unit_price

        row = [
            Paragraph(item.description, styles["normal"]),
            Paragraph(f"{quantity:g}", styles["normal"]),
            Paragraph(item.unit, styles["normal"]),
            Paragraph(format_money(unit_price), styles["normal"]),
        ]

        if company_is_vat_payer:
            row.append(Paragraph(f"{item.vat_rate} %", styles["normal"]))

        row.append(Paragraph(format_money(line_total), styles["normal"]))

        items_rows.append(row)

    items_table = Table(
        items_rows,
        colWidths=items_col_widths
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
    story.append(Spacer(1, 14))


    # =====================================
    # SÚHRN
    # =====================================

    totals = calculate_invoice_totals(quote.items)

    summary_rows = [
        [
            Paragraph("CELKOM", styles["summary_total_label"]),
            Paragraph(format_money(totals["total_gross"]), styles["summary_total_value"])
        ],
    ]

    summary_table = Table(
        summary_rows,
        colWidths=[100 * mm, 70 * mm]
    )

    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F1E7")),
            ("LINEABOVE", (0, 0), (-1, 0), 1, colors.HexColor("#1E2B2F")),
            ("TOPPADDING", (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("LEFTPADDING", (0, 0), (0, 0), 8),
            ("RIGHTPADDING", (-1, 0), (-1, 0), 8),
        ])
    )

    story.append(summary_table)
    story.append(Spacer(1, 20))


    if quote.note:

        story.append(Paragraph(quote.note, styles["normal"]))
        story.append(Spacer(1, 14))


    story.append(
        Paragraph(
            "Toto je cenová ponuka, nie daňový doklad.",
            styles["small"]
        )
    )


    doc.build(story)

    return buffer.getvalue()
