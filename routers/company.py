from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from auth import require_login_page
from database import get_db
from backup_utils import create_backup_bytes, restore_from_upload
from invoice_utils import NON_VAT_PAYER_NOTICE
from models import Company
from templates_config import templates
from uploads_utils import delete_image, save_image_upload


router = APIRouter()


def get_or_create_company(db: Session) -> Company:
    """
    Appka počíta s jedným riadkom fakturačných údajov firmy.
    Ak ešte neexistuje, vytvorí prázdny.
    """

    company = db.query(Company).first()

    if company is None:

        company = Company(
            name=""
        )

        db.add(company)

        db.commit()

        db.refresh(company)

    return company


# =========================================
# NASTAVENIA - FORM
# =========================================

@router.get("/settings")
def settings_form(

    request: Request,

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    company = get_or_create_company(db)


    return templates.TemplateResponse(

        request=request,

        name="settings.html",

        context={

            "company": company,

            "NON_VAT_PAYER_NOTICE": NON_VAT_PAYER_NOTICE

        }

    )


# =========================================
# NASTAVENIA - ULOŽENIE
# =========================================

@router.post("/settings")
async def settings_save(

    name: str = Form(...),

    ico: str = Form(""),

    dic: str = Form(""),

    ic_dph: str = Form(""),

    is_vat_payer: str = Form(""),

    address: str = Form(""),

    city: str = Form(""),

    zip_code: str = Form(""),

    iban: str = Form(""),

    swift_bic: str = Form(""),

    email: str = Form(""),

    phone: str = Form(""),

    website: str = Form(""),

    peppol_scheme_id: str = Form(""),

    logo: UploadFile | None = None,

    signature: UploadFile | None = None,

    remove_logo: str = Form(""),

    remove_signature: str = Form(""),

    db: Session = Depends(get_db),

    user: str = Depends(require_login_page)

):

    company = get_or_create_company(db)


    company.name = name
    company.ico = ico or None
    company.dic = dic or None
    company.ic_dph = ic_dph or None
    company.is_vat_payer = is_vat_payer == "1"
    company.address = address or None
    company.city = city or None
    company.zip_code = zip_code or None
    company.iban = iban or None
    company.swift_bic = swift_bic or None
    company.email = email or None
    company.phone = phone or None
    company.website = website or None
    company.peppol_scheme_id = peppol_scheme_id or None


    if logo is not None and logo.filename:

        company.logo_filename = await save_image_upload(logo, "logo")

    elif remove_logo == "1":

        delete_image("logo")
        company.logo_filename = None


    if signature is not None and signature.filename:

        company.signature_filename = await save_image_upload(signature, "signature")

    elif remove_signature == "1":

        delete_image("signature")
        company.signature_filename = None


    db.commit()


    return RedirectResponse(

        url="/settings",

        status_code=303

    )


# =========================================
# ZÁLOHA / OBNOVA DATABÁZY
# =========================================

@router.get("/settings/backup")
def download_backup(

    user: str = Depends(require_login_page)

):

    backup_bytes = create_backup_bytes()

    filename = f"digitalny-zosit-zaloha-{date.today().isoformat()}.db"

    return Response(

        content=backup_bytes,

        media_type="application/x-sqlite3",

        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }

    )


@router.post("/settings/restore")
async def upload_restore(

    request: Request,

    user: str = Depends(require_login_page)

):

    form = await request.form()

    upload = form.get("backup_file")

    if upload is None or not getattr(upload, "filename", None):

        raise HTTPException(
            status_code=422,
            detail="Nebol vybraný žiadny súbor na obnovu."
        )

    file_bytes = await upload.read()

    restore_from_upload(file_bytes)


    return RedirectResponse(

        url="/settings?restored=1",

        status_code=303

    )
