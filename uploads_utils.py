import os
from pathlib import Path

from fastapi import HTTPException, UploadFile


UPLOADS_DIR = Path(__file__).parent / "uploads"

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}

MAX_UPLOAD_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB


def ensure_uploads_dir() -> None:

    UPLOADS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


def _extension_for(filename: str) -> str:

    return Path(filename).suffix.lower()


async def save_image_upload(upload: UploadFile, base_name: str) -> str:
    """
    Uloží nahraný obrázok (logo/podpis) do uploads/ priečinka.

    Pred uložením zmaže existujúce súbory s rovnakým `base_name`
    (bez ohľadu na príponu), aby po zmene formátu nezostali staré súbory.

    Vráti názov uloženého súboru (napr. "logo.png").
    """

    ensure_uploads_dir()


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


    delete_image(base_name)


    filename = f"{base_name}{extension}"

    file_path = UPLOADS_DIR / filename

    with open(file_path, "wb") as f:
        f.write(contents)


    return filename


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
