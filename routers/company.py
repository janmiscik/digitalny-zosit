from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from csrf import verify_csrf
from auth import require_login_page
from database import get_db
from backup_utils import create_backup_bytes, restore_from_upload
from invoice_utils import NON_VAT_PAYER_NOTICE
from models import Company
from templates_config import templates
from uploads_utils import (
    delete_image,
    discard_staged_image,
    finalize_staged_image,
    stage_image_upload,
)


router = APIRouter(dependencies=[Depends(verify_csrf)])


def get_or_create_company(db: Session) -> Company:
    """
    Appka počíta s jedným riadkom fakturačných údajov firmy (id je v DB
    pevne vynútené na hodnotu 1 - viď model Company).

    Ak dva požiadavky pri úplne prvom spustení appky pretekárske
    pristanú tu obaja naraz (napr. dva otvorené taby), DB constraint
    (PRIMARY KEY / CHECK id=1) druhý INSERT odmietne - to je presne to,
    čo má singleton zaručiť. V tom prípade jednoducho zahodíme vlastný
    pokus a načítame riadok, ktorý medzitým vytvoril ten druhý
    požiadavok.
    """

    company = db.query(Company).first()

    if company is not None:
        return company

    company = Company(
        name=""
    )

    db.add(company)

    try:

        db.commit()

    except IntegrityError:

        db.rollback()

        company = db.query(Company).first()

        if company is None:
            # Prakticky by sa toto nemalo stať (IntegrityError na
            # vloženie jediného riadku znamená, že už jeden existuje) -
            # ale radšej explicitná chyba než ticho vrátiť None.
            raise

    else:

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


    # Nahrávanie súborov je zámerne "stage -> DB commit -> finalize/
    # discard": nový súbor sa zapíše pod dočasným menom, staré súbory sa
    # zmažú AŽ PO úspešnom uložení DB záznamu. Ak by DB commit zlyhal, DB
    # by inak mohla odkazovať na medzičasom zmazaný starý súbor.

    staged_logo = None
    staged_signature = None

    if logo is not None and logo.filename:

        staged_logo = await stage_image_upload(logo, "logo")
        company.logo_filename = staged_logo[1]

    elif remove_logo == "1":

        company.logo_filename = None


    if signature is not None and signature.filename:

        staged_signature = await stage_image_upload(signature, "signature")
        company.signature_filename = staged_signature[1]

    elif remove_signature == "1":

        company.signature_filename = None


    try:

        db.commit()

    except Exception:

        db.rollback()

        if staged_logo is not None:
            discard_staged_image(staged_logo[0])

        if staged_signature is not None:
            discard_staged_image(staged_signature[0])

        raise


    if staged_logo is not None:

        finalize_staged_image(staged_logo[0], staged_logo[1], "logo")

    elif remove_logo == "1":

        delete_image("logo")


    if staged_signature is not None:

        finalize_staged_image(staged_signature[0], staged_signature[1], "signature")

    elif remove_signature == "1":

        delete_image("signature")


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

    filename = f"digitalny-zosit-zaloha-{date.today().isoformat()}.zip"

    return Response(

        content=backup_bytes,

        media_type="application/zip",

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
