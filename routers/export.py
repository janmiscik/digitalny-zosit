"""
Export pre účtovníka (CSV a Excel) + ZIP s PDF faktúrami za obdobie.
"""

import io
import re
import zipfile
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from audit_log import log_action
from auth import require_login_page
from csrf import verify_csrf
from database import get_db
from export_utils import (
    COST_COLUMNS,
    EXPORT_STATUSES,
    INVOICE_COLUMNS,
    PRESETS,
    build_csv,
    build_xlsx,
    cost_rows,
    invoice_rows,
    resolve_period,
    select_invoices,
    totals_of,
    vat_summary,
)
from invoice_pdf import generate_invoice_pdf
from models import Company, Invoice, Job, JobCost
from templates_config import templates


router = APIRouter(dependencies=[Depends(verify_csrf)])

# Horná hranica počtu PDF v jednom ZIP-e (generovanie je pomalšie).
MAX_PDF_IN_ZIP = 300


def _parse_date(raw: str, label: str) -> date | None:

    if not raw:
        return None

    try:

        return date.fromisoformat(raw)

    except ValueError:

        raise HTTPException(status_code=422, detail=f"Neplatný dátum ({label}).")


def _period(preset: str, date_from: str, date_to: str):

    try:

        return resolve_period(
            preset,
            _parse_date(date_from, "od"),
            _parse_date(date_to, "do"),
            date.today(),
        )

    except ValueError as exc:

        raise HTTPException(status_code=422, detail=str(exc))


def _load(db: Session, start: date, end: date, include_proforma: bool):
    """Faktúry a náklady za obdobie (+ čísla pôvodných faktúr dobropisov)."""

    invoices = select_invoices(
        db
        .query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.customer))
        .filter(
            Invoice.issue_date >= start,
            Invoice.issue_date <= end,
            Invoice.status.in_(EXPORT_STATUSES),
        )
        .all(),
        start, end, include_proforma
    )

    original_ids = {i.original_invoice_id for i in invoices if i.original_invoice_id}

    original_numbers = {}

    if original_ids:

        original_numbers = dict(
            db.query(Invoice.id, Invoice.invoice_number).filter(Invoice.id.in_(original_ids)).all()
        )

    costs = cost_rows(
        db
        .query(JobCost)
        .options(joinedload(JobCost.job).joinedload(Job.customer))
        .filter(JobCost.cost_date >= start, JobCost.cost_date <= end)
        .all(),
        start, end
    )

    return invoices, invoice_rows(invoices, original_numbers), costs


def _query_string(preset: str, date_from: str, date_to: str, proforma: str) -> str:

    params = {"preset": preset}

    if preset == "custom":

        params["date_from"] = date_from
        params["date_to"] = date_to

    if proforma:
        params["proforma"] = "1"

    return urlencode(params)


def _file_stem(start: date, end: date) -> str:
    return f"{start.isoformat()}_{end.isoformat()}"


@router.get("/export")
def export_page(

    request: Request,

    preset: str = Query("last_month"),

    date_from: str = Query(""),

    date_to: str = Query(""),

    proforma: str = Query(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    context = {
        "presets": PRESETS,
        "preset": preset,
        "date_from": date_from,
        "date_to": date_to,
        "proforma": bool(proforma),
        "error": None,
        "preview": None,
    }

    try:

        start, end, label = resolve_period(
            preset,
            _parse_date(date_from, "od"),
            _parse_date(date_to, "do"),
            date.today(),
        )

    except (ValueError, HTTPException) as exc:

        context["error"] = exc.detail if isinstance(exc, HTTPException) else str(exc)

        return templates.TemplateResponse(request=request, name="export.html", context=context)

    invoices, rows, costs = _load(db, start, end, bool(proforma))

    totals = totals_of(rows)

    context["preview"] = {
        "label": label,
        "start": start,
        "end": end,
        "totals": totals,
        "cancelled": totals["count"] - totals["counted"],
        "costs_count": len(costs),
        "costs_total": sum((row["Suma bez DPH"] for row in costs), Decimal("0")),
        "pdf_too_many": len(invoices) > MAX_PDF_IN_ZIP,
        "query": _query_string(preset, date_from, date_to, proforma),
    }

    return templates.TemplateResponse(request=request, name="export.html", context=context)


def _audit(db: Session, kind: str, label: str) -> None:

    log_action(db, "export.download", entity_type="export", detail=f"{kind} za obdobie {label}")

    db.commit()


@router.get("/export/faktury.csv")
def export_invoices_csv(

    preset: str = Query("last_month"),
    date_from: str = Query(""),
    date_to: str = Query(""),
    proforma: str = Query(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    start, end, label = _period(preset, date_from, date_to)

    _invoices, rows, _costs = _load(db, start, end, bool(proforma))

    _audit(db, "CSV faktúr", label)

    return Response(
        content=build_csv(rows, INVOICE_COLUMNS),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="faktury-{_file_stem(start, end)}.csv"'},
    )


@router.get("/export/naklady.csv")
def export_costs_csv(

    preset: str = Query("last_month"),
    date_from: str = Query(""),
    date_to: str = Query(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    start, end, label = _period(preset, date_from, date_to)

    _invoices, _rows, costs = _load(db, start, end, False)

    _audit(db, "CSV nákladov", label)

    return Response(
        content=build_csv(costs, COST_COLUMNS),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="naklady-{_file_stem(start, end)}.csv"'},
    )


@router.get("/export/uctovnik.xlsx")
def export_xlsx(

    preset: str = Query("last_month"),
    date_from: str = Query(""),
    date_to: str = Query(""),
    proforma: str = Query(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    start, end, label = _period(preset, date_from, date_to)

    invoices, rows, costs = _load(db, start, end, bool(proforma))

    content = build_xlsx(
        period_label=label,
        company=db.query(Company).first(),
        invoices_rows=rows,
        vat_rows=vat_summary(invoices),
        costs=costs,
        generated=date.today(),
    )

    _audit(db, "Excel pre účtovníka", label)

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="uctovnik-{_file_stem(start, end)}.xlsx"'},
    )


@router.get("/export/faktury-pdf.zip")
def export_invoice_pdfs(

    preset: str = Query("last_month"),
    date_from: str = Query(""),
    date_to: str = Query(""),
    proforma: str = Query(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    start, end, label = _period(preset, date_from, date_to)

    invoices, _rows, _costs = _load(db, start, end, bool(proforma))

    if not invoices:

        raise HTTPException(status_code=404, detail="V tomto období nie sú žiadne faktúry.")

    if len(invoices) > MAX_PDF_IN_ZIP:

        raise HTTPException(
            status_code=422,
            detail=f"Faktúr je príliš veľa ({len(invoices)}). Skráť obdobie na najviac {MAX_PDF_IN_ZIP}."
        )

    company = db.query(Company).first()

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        for invoice in invoices:

            safe_number = re.sub(r"[^A-Za-z0-9_-]", "_", invoice.invoice_number)

            archive.writestr(f"faktura-{safe_number}.pdf", generate_invoice_pdf(invoice, company))

    _audit(db, "ZIP s PDF faktúrami", label)

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="faktury-pdf-{_file_stem(start, end)}.zip"'},
    )
