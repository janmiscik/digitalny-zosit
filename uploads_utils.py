import io
import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError


UPLOADS_DIR = Path(__file__).parent / "uploads"

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}

# Ktorý skutočný (Pillow rozpoznaný) formát obrázka je prípustný pre danú
# príponu súboru - kontroluje sa OBSAH súboru, nielen jeho prípona/názov.
# Bez tejto kontroly by stačilo premenovať ľubovoľný súbor (napr. .html,
# .svg so skriptom, alebo poškodený/škodlivo upravený súbor) na "logo.png"
# a appka by ho prijala a servovala ako obrázok.
ALLOWED_IMAGE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
}

MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB


def ensure_uploads_dir() -> None:

    UPLOADS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


def _extension_for(filename: str) -> str:

    return Path(filename).suffix.lower()


def _verify_real_image_type(contents: bytes, extension: str) -> None:
    """
    Overí, že obsah súboru je NAOZAJ platný obrázok zodpovedajúci danej
    prípone - nestačí, že sa tak súbor len volá. Používa Pillow na
    skutočné dekódovanie obrázka, nie len kontrolu "magických bajtov".

    Vyhodí HTTPException 422, ak súbor nie je platný/čitateľný obrázok,
    alebo ak jeho skutočný formát nezodpovedá deklarovanej prípone
    (napr. súbor s príponou .png, ktorý v skutočnosti nie je PNG).
    """

    expected_format = ALLOWED_IMAGE_FORMATS[extension]

    try:

        with Image.open(io.BytesIO(contents)) as image:

            image.verify()

    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):

        raise HTTPException(
            status_code=422,
            detail=(
                "Súbor nie je platný alebo je poškodený obrázok "
                "(obsah nezodpovedá deklarovanému formátu)."
            )
        )


    # image.verify() zneplatní pôvodný objekt na ďalšie použitie - na
    # zistenie skutočného formátu preto obrázok otvoríme nanovo.
    try:

        with Image.open(io.BytesIO(contents)) as image:

            actual_format = image.format

    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):

        raise HTTPException(
            status_code=422,
            detail=(
                "Súbor nie je platný alebo je poškodený obrázok "
                "(obsah nezodpovedá deklarovanému formátu)."
            )
        )


    if actual_format != expected_format:

        raise HTTPException(
            status_code=422,
            detail=(
                f"Obsah súboru nezodpovedá prípone '{extension}' "
                f"(skutočný formát: {actual_format or 'neznámy'})."
            )
        )


async def _read_and_validate_image(upload: UploadFile) -> tuple[bytes, str]:
    """
    Zdieľaná validácia pre všetky obrázkové uploady v appke (logo/podpis
    aj fotky zákaziek) - kontrola prípony, veľkosti a SKUTOČNÉHO obsahu
    súboru. Vráti (obsah_súboru, prípona).
    """

    extension = _extension_for(upload.filename or "")

    if extension not in ALLOWED_EXTENSIONS:

        raise HTTPException(
            status_code=422,
            detail=(
                f"Nepodporovaný formát obrázka '{extension}'. "
                f"Povolené sú: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        )

    contents = await upload.read()

    if len(contents) > MAX_UPLOAD_SIZE_BYTES:

        raise HTTPException(
            status_code=422,
            detail="Obrázok je príliš veľký (max. 2 MB)"
        )

    if len(contents) == 0:

        raise HTTPException(
            status_code=422,
            detail="Nahraný súbor je prázdny"
        )

    # Skutočná kontrola obsahu súboru - AŽ TERAZ, keď vieme, že súbor má
    # rozumnú veľkosť (nemá zmysel dekódovať obrovský súbor len preto,
    # aby sme zistili, že prekračuje limit).
    _verify_real_image_type(contents, extension)

    return contents, extension


async def save_image_upload(upload: UploadFile, base_name: str) -> str:
    """
    Uloží nahraný obrázok (logo/podpis) do uploads/ priečinka.

    Pred uložením zmaže existujúce súbory s rovnakým `base_name`
    (bez ohľadu na príponu), aby po zmene formátu nezostali staré súbory.

    Vráti názov uloženého súboru (napr. "logo.png").
    """

    ensure_uploads_dir()

    contents, extension = await _read_and_validate_image(upload)

    delete_image(base_name)


    filename = f"{base_name}{extension}"

    file_path = UPLOADS_DIR / filename

    with open(file_path, "wb") as f:
        f.write(contents)


    return filename


# =========================================
# FOTKY ZÁKAZIEK (pred/po)
#
# Na rozdiel od loga/podpisu (jeden pevný súbor na firmu) môže mať
# zákazka ĽUBOVOĽNÝ počet fotiek - každá potrebuje jedinečný názov a
# žiadna sa pri nahraní ďalšej nemaže. Ukladajú sa do vlastného
# podpriečinka, nech sa nemiešajú s logom/podpisom.
# =========================================

JOB_PHOTOS_DIR = UPLOADS_DIR / "job_photos"


def ensure_job_photos_dir() -> None:

    JOB_PHOTOS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


async def save_job_photo_upload(upload: UploadFile, job_id: int) -> str:
    """
    Uloží fotku zákazky do uploads/job_photos/ priečinka pod jedinečným
    názvom. Vráti názov uloženého súboru (napr. "job5-3f9a1c2b.jpg").
    """

    ensure_job_photos_dir()

    contents, extension = await _read_and_validate_image(upload)

    unique_id = uuid.uuid4().hex[:12]

    filename = f"job{job_id}-{unique_id}{extension}"

    file_path = JOB_PHOTOS_DIR / filename

    with open(file_path, "wb") as f:
        f.write(contents)

    return filename


def delete_job_photo(filename: str) -> None:

    if not filename:
        return

    # Whitelist na bezpečné znaky - žiadne "../" alebo iné cesty mimo
    # priečinka (rovnaký princíp ako pri servovaní, viď main.py).
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        return

    path = JOB_PHOTOS_DIR / filename

    if path.exists() and path.parent == JOB_PHOTOS_DIR:
        os.remove(path)


def job_photo_path(filename: str) -> Path | None:

    if not filename or not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        return None

    path = JOB_PHOTOS_DIR / filename

    if not path.exists() or path.parent != JOB_PHOTOS_DIR:
        return None

    return path


def delete_image(base_name: str) -> None:
    """
    Zmaže všetky súbory v uploads/ priečinku s daným base_name
    (bez ohľadu na príponu).
    """

    if not UPLOADS_DIR.exists():
        return

    for extension in ALLOWED_EXTENSIONS:

        candidate = UPLOADS_DIR / f"{base_name}{extension}"

        if candidate.exists():

            os.remove(candidate)


def image_path(filename: str | None) -> Path | None:

    if not filename:
        return None

    path = UPLOADS_DIR / filename

    if not path.exists():
        return None

    return path
