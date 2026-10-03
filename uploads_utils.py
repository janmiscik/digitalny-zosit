import io
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

import tenancy

logger = logging.getLogger(__name__)


def uploads_dir() -> Path:
    """
    Priečinok s nahranými súbormi (logo, podpis, fotky zákaziek)
    AKTUÁLNE obsluhovaného konta (tenancy.py) - každé konto má svoj
    vlastný, oddelený priečinok.
    """

    return tenancy.account_uploads_dir(tenancy.get_current_tenant())

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

# Ochrana proti "decompression bomb" - malému súboru (v rámci limitu
# vyššie), ktorý sa ale rozbalí do obrovského rozlíšenia a pri
# dekódovaní/resize zožerie enormné množstvo pamäte a CPU. Kontroluje sa
# HNEĎ po otvorení súboru - v tej chvíli Pillow pozná rozmery len z
# hlavičky (napr. IHDR pri PNG), ešte NEDEKÓDUJE pixelové dáta, takže
# nevalidný súbor padne skôr, než by appka čokoľvek reálne alokovala.
# Pillow sám má vlastný vstavaný limit (Image.MAX_IMAGE_PIXELS, cca 89
# megapixelov), ale ten len VAROVANIE (warnings.warn), nie chybu, kým sa
# neprekročí dvojnásobok - to sa dá ľahko prehliadnuť a appka by aj tak
# skončila alokáciou stoviek MB. 40 megapixelov je veľkorysé aj pre
# bežnú fotku z mobilu (appka ju aj tak hneď zmenší na max. 1600px
# dlhšej strany, viď MAX_PHOTO_DIMENSION nižšie).
MAX_IMAGE_PIXELS = 40_000_000


def ensure_uploads_dir() -> None:

    uploads_dir().mkdir(
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

            # Rozmery sa dajú zistiť z hlavičky bez dekódovania
            # pixelových dát - kontrolujeme PRED image.verify(), nech
            # sa Pillow vôbec nezačne zaoberať podozrivo obrovským
            # obrázkom.
            width, height = image.size

            if width * height > MAX_IMAGE_PIXELS:

                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Obrázok má príliš vysoké rozlíšenie "
                        f"({width}×{height} px). Zmenši ho prosím pred "
                        "nahraním."
                    )
                )

            image.verify()

    except HTTPException:

        raise

    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ):

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

    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ):

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


async def read_upload_with_limit(
    upload,
    max_bytes: int,
    too_large_detail: str,
    chunk_size: int = 1024 * 1024
) -> bytes:
    """
    Načíta súbor (UploadFile alebo objekt s rovnakým `async .read(n)`
    rozhraním, napr. z `await request.form()`) PO ČASTIACH a PRERUŠÍ
    čítanie hneď, ako obsah prekročí `max_bytes`.

    Bežné `await upload.read()` by najprv načítalo CELÝ súbor do
    pamäte (aj keby mal niekoľko GB) a limit veľkosti by sa overil až
    POTOM, na už hotovom `bytes` objekte - veľkostný limit by teda v
    skutočnosti vôbec nechránil pred tým, aby appka najprv celý taký
    súbor nevcucla do RAM. Táto funkcia limit presadzuje priebežne.
    """

    chunks = bytearray()

    while True:

        chunk = await upload.read(chunk_size)

        if not chunk:
            break

        chunks.extend(chunk)

        if len(chunks) > max_bytes:

            raise HTTPException(
                status_code=422,
                detail=too_large_detail
            )

    return bytes(chunks)


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

    contents = await read_upload_with_limit(
        upload,
        max_bytes=MAX_UPLOAD_SIZE_BYTES,
        too_large_detail="Obrázok je príliš veľký (max. 2 MB)"
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
    Jednoduché uloženie obrázka (logo/podpis) - validuje, zmaže staré
    súbory s rovnakým base_name a zapíše nový.

    POZOR: toto NIE JE bezpečné voči zlyhaniu DB commitu po zápise -
    ak appka potrebuje istotu, že sa súbor zmení len keď sa naozaj
    uloží aj DB záznam (napr. /settings), použi namiesto tejto funkcie
    dvojicu stage_image_upload() + finalize_staged_image()/
    discard_staged_image() nižšie.
    """

    ensure_uploads_dir()

    contents, extension = await _read_and_validate_image(upload)

    delete_image(base_name)


    filename = f"{base_name}{extension}"

    file_path = uploads_dir() / filename

    with open(file_path, "wb") as f:
        f.write(contents)


    return filename


async def stage_image_upload(upload: UploadFile, base_name: str) -> tuple[Path, str]:
    """
    Overí a zapíše nahraný obrázok pod DOČASNÝM názvom - existujúci
    súbor s rovnakým base_name sa ešte NEDOTKNE. Použi spolu s
    finalize_staged_image() (po úspešnom DB commite) alebo
    discard_staged_image() (ak DB commit zlyhá).

    Toto rieši scenár: appka zapíše nové logo na disk, zmaže staré,
    a AŽ POTOM zlyhá DB commit - výsledkom by bol stav, kde DB stále
    odkazuje na (už zmazané) staré logo. So stage/finalize sa staré
    súbory zmažú až vtedy, keď je nový DB záznam bezpečne uložený.
    """

    ensure_uploads_dir()

    contents, extension = await _read_and_validate_image(upload)

    final_filename = f"{base_name}{extension}"

    temp_filename = f".tmp-{uuid.uuid4().hex[:12]}-{final_filename}"

    temp_path = uploads_dir() / temp_filename

    with open(temp_path, "wb") as f:
        f.write(contents)

    return temp_path, final_filename


def finalize_staged_image(temp_path: Path, final_filename: str, base_name: str) -> None:
    """
    Zavolať PO úspešnom DB commite - premenuje dočasný súbor na finálny
    názov a AŽ POTOM zmaže prípadné staré súbory s rovnakým base_name
    (napr. predošlé logo s inou príponou).

    Poradie je zámerné a je to presne ten "nie úplne atomický" krok,
    ktorý táto funkcia rieši: os.replace() je na väčšine systémov
    atomická operácia, takže hneď po nej na final_path existuje platný
    súbor. Keby sme (ako predtým) najprv zmazali starý súbor a AŽ POTOM
    premenovávali nový, pád appky presne medzi týmito dvoma krokmi
    (výpadok napájania, OOM kill) by mohol nechať DB odkazovať na
    súbor, ktorý v tej chvíli vôbec neexistuje - ani starý (už
    zmazaný), ani nový (ešte nepremenovaný). V opačnom poradí takýto
    stav nikdy nenastane: buď je na final_path stále starý súbor
    (replace ešte neprebehol), alebo už nový (replace prebehol).
    """

    final_path = uploads_dir() / final_filename

    os.replace(temp_path, final_path)

    # Prípadné staré súbory s INOU príponou (napr. logo bolo .jpg,
    # nahradené za .png) - final_path už obsahuje nový súbor, ten preto
    # vynecháme, aj keby náhodou mal rovnaké meno ako niektorá z
    # kontrolovaných prípon.
    for extension in ALLOWED_EXTENSIONS:

        candidate = uploads_dir() / f"{base_name}{extension}"

        if candidate != final_path and candidate.exists():
            os.remove(candidate)


def discard_staged_image(temp_path: Path) -> None:
    """
    Zavolať, ak DB commit ZLYHAL - zmaže len dočasný súbor, pôvodný
    (starý) súbor ostáva netknutý.
    """

    if temp_path.exists():
        os.remove(temp_path)


# =========================================
# FOTKY ZÁKAZIEK (pred/po)
#
# Na rozdiel od loga/podpisu (jeden pevný súbor na firmu) môže mať
# zákazka ĽUBOVOĽNÝ počet fotiek - každá potrebuje jedinečný názov a
# žiadna sa pri nahraní ďalšej nemaže. Ukladajú sa do vlastného
# podpriečinka, nech sa nemiešajú s logom/podpisom.
# =========================================

def job_photos_dir() -> Path:
    """Rovnaký princíp ako uploads_dir(), pre fotky zákaziek konkrétneho konta."""

    return uploads_dir() / "job_photos"

# Fotky zákaziek bývajú z mobilu často zbytočne veľké (aj v rámci
# 2 MB limitu) - pre zobrazenie v appke aj v PDF plne stačí dlhšia
# strana do 1600 px. Kratšia strana sa zmenší proporcionálne.
MAX_PHOTO_DIMENSION = 1600
JPEG_QUALITY = 85


def ensure_job_photos_dir() -> None:

    job_photos_dir().mkdir(
        parents=True,
        exist_ok=True
    )


def _resize_and_reencode_photo(contents: bytes, pillow_format: str) -> bytes:
    """
    Zmenší fotku zákazky tak, aby jej dlhšia strana nepresiahla
    MAX_PHOTO_DIMENSION px (nikdy nezväčšuje) a opraví orientáciu
    podľa EXIF - fotky z mobilu bývajú na výšku/na šírku často iba
    vďaka EXIF príznaku, nie skutočným pixelom, takže bez tejto
    opravy by po zmene veľkosti (alebo v prehliadači, ktorý EXIF
    ignoruje) mohli vyjsť pootočené. Pri JPEG sa zároveň dorovná
    kompresia (bez toho by resize samotný veľkosť súboru zas tak
    nezmenšil).

    Volá sa AŽ PO _verify_real_image_type, takže vieme, že obsah je
    naozaj platný a čitateľný obrázok zodpovedajúceho formátu. Ak by
    napriek tomu spracovanie zlyhalo, radšej ticho vrátime pôvodný
    (už validovaný) obsah, než aby kvôli tomu nahranie fotky úplne
    zlyhalo.
    """

    try:

        with Image.open(io.BytesIO(contents)) as image:

            image = ImageOps.exif_transpose(image)

            width, height = image.size
            longest_side = max(width, height)

            if longest_side > MAX_PHOTO_DIMENSION:

                scale = MAX_PHOTO_DIMENSION / longest_side

                new_size = (
                    max(1, round(width * scale)),
                    max(1, round(height * scale))
                )

                image = image.resize(new_size, Image.LANCZOS)

            output = io.BytesIO()

            if pillow_format == "JPEG":

                # JPEG nepozná priehľadnosť/paletu - ak by po
                # exif_transpose ostal obrázok v inom móde (napr.
                # CMYK, P), pred uložením ho prevedieme na RGB.
                if image.mode != "RGB":
                    image = image.convert("RGB")

                image.save(
                    output,
                    format="JPEG",
                    quality=JPEG_QUALITY,
                    optimize=True
                )

            else:  # PNG

                image.save(output, format="PNG", optimize=True)

            return output.getvalue()

    except (
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError
    ) as exc:

        # Len chyby, ktoré Pillow reálne vyhadzuje pri poškodenom/
        # nezvyčajnom obrázku (OSError vrátane UnidentifiedImageError a
        # skrátených súborov, ValueError, SyntaxError pri poškodenom
        # PNG, DecompressionBombError). Iné (programátorské) chyby sa
        # zámerne NEZACHYTIA - inak by ich tento "ticho vráť pôvodný
        # obsah" fallback skryl.
        #
        # logger.exception() namiesto .warning() - zaloguje aj celý
        # traceback (kde presne v Pillow to spadlo), nielen typ a text
        # výnimky. Bez neho by sa pri nezvyčajnom/vzácnom formáte fotky
        # ťažko hľadalo, čo presne sa deje.
        logger.exception(
            "Zmena veľkosti fotky zlyhala (%s) - ukladá sa pôvodný obsah.",
            type(exc).__name__
        )

        return contents


async def save_job_photo_upload(upload: UploadFile, job_id: int) -> str:
    """
    Uloží fotku zákazky do uploads/job_photos/ priečinka pod jedinečným
    názvom (po zmenšení na rozumnú veľkosť, viď
    _resize_and_reencode_photo). Vráti názov uloženého súboru
    (napr. "job5-3f9a1c2b.jpg").
    """

    ensure_job_photos_dir()

    contents, extension = await _read_and_validate_image(upload)

    contents = _resize_and_reencode_photo(
        contents,
        ALLOWED_IMAGE_FORMATS[extension]
    )

    unique_id = uuid.uuid4().hex[:12]

    filename = f"job{job_id}-{unique_id}{extension}"

    file_path = job_photos_dir() / filename

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

    path = job_photos_dir() / filename

    if path.exists() and path.parent == job_photos_dir():
        os.remove(path)


def job_photo_path(filename: str) -> Path | None:

    if not filename or not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        return None

    # Názov nesmie začínať bodkou - inak by prešlo ".." (priečinok, nie
    # fotka; FileResponse by na ňom spadla s 500) aj skryté súbory.
    if filename.startswith("."):
        return None

    path = job_photos_dir() / filename

    # is_file() namiesto exists() - musí to byť skutočný súbor.
    if not path.is_file() or path.parent != job_photos_dir():
        return None

    return path


def delete_image(base_name: str) -> None:
    """
    Zmaže všetky súbory v uploads/ priečinku s daným base_name
    (bez ohľadu na príponu).
    """

    if not uploads_dir().exists():
        return

    for extension in ALLOWED_EXTENSIONS:

        candidate = uploads_dir() / f"{base_name}{extension}"

        if candidate.exists():

            os.remove(candidate)


def image_path(filename: str | None) -> Path | None:

    if not filename:
        return None

    path = uploads_dir() / filename

    if not path.exists():
        return None

    return path
