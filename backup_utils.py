"""
Záloha a obnova celej appky (databáza + nahraté súbory).

Predtým appka zálohovala len SQLite súbor - ale databáza obsahuje
odkazy na súbory (logo_filename, signature_filename, JobPhoto.filename),
ktoré fyzicky ležia v uploads/, MIMO databázy. Obnova samotnej DB bez
týchto súborov by viedla k "osirotenym" záznamom odkazujúcim na
neexistujúce obrázky. Záloha preto teraz balí OBOJE do jedného ZIP
súboru:

    digitalny-zosit-zaloha-2026-09-05.zip
    ├── database.db
    └── uploads/
        ├── logo.png
        ├── signature.png
        └── job_photos/...

Databáza sa do zálohy pridáva cez natívne SQLite backup API
(sqlite3.Connection.backup()), nie hrubým skopírovaním súboru - appka
môže mať v čase zálohovania otvorené spojenie/transakciu a priame
kopírovanie by mohlo zachytiť nekonzistentný stav.
"""

import io
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from database import DATABASE_URL, engine


# Tabuľky, ktoré musí mať KAŽDÁ platná záloha tejto appky - slúžia na
# rýchlu kontrolu "je toto vôbec databáza z Digitálneho zošita", skôr
# než ňou prepíšeme aktuálnu databázu.
REQUIRED_TABLES = {
    "customers",
    "invoices",
    "invoice_items",
    "jobs",
    "company",
    "alembic_version",
}

BACKUPS_DIR = Path(__file__).parent / "backups"

UPLOADS_DIR = Path(__file__).parent / "uploads"

MAX_UPLOAD_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB - záloha teraz obsahuje aj fotky


def _sqlite_file_path() -> Path:
    """
    Vytiahne cestu k súboru zo SQLAlchemy DATABASE_URL
    (napr. "sqlite:///./digitalny-zosit.db" -> "./digitalny-zosit.db").

    Vyhodí HTTPException, ak appka nebeží nad SQLite (záloha/obnova v
    tejto podobe dáva zmysel len pre jednosúborovú SQLite databázu).
    """

    match = re.match(r"^sqlite:///(.+)$", DATABASE_URL)

    if not match:

        raise HTTPException(
            status_code=400,
            detail=(
                "Záloha/obnova je podporovaná len pre SQLite databázu. "
                f"Aktuálna DATABASE_URL: {DATABASE_URL}"
            )
        )

    return Path(match.group(1)).resolve()


def ensure_backups_dir() -> None:

    BACKUPS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


def _create_db_snapshot_bytes(db_path: Path) -> bytes:
    """
    Vytvorí konzistentnú kópiu SQLite súboru cez natívne backup API a
    vráti jej obsah ako bajty.
    """

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:

        tmp_path = Path(tmp.name)

    try:

        source = sqlite3.connect(str(db_path))
        destination = sqlite3.connect(str(tmp_path))

        with destination:
            source.backup(destination)

        source.close()
        destination.close()

        return tmp_path.read_bytes()

    finally:

        tmp_path.unlink(missing_ok=True)


def create_backup_bytes() -> bytes:
    """
    Vytvorí kompletnú zálohu appky (databáza + uploads/) ako ZIP a
    vráti jej obsah ako bajty (na priame stiahnutie cez appku).
    """

    db_path = _sqlite_file_path()

    if not db_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Databázový súbor neexistuje - appka ešte nemá žiadne dáta."
        )

    db_bytes = _create_db_snapshot_bytes(db_path)

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

        zf.writestr("database.db", db_bytes)

        if UPLOADS_DIR.exists():

            for file_path in UPLOADS_DIR.rglob("*"):

                if file_path.is_file():

                    arcname = "uploads/" + str(
                        file_path.relative_to(UPLOADS_DIR)
                    ).replace("\\", "/")

                    zf.write(file_path, arcname)

    return zip_buffer.getvalue()


def save_automatic_backup() -> Path:
    """
    Uloží časovo označenú kompletnú zálohu (ZIP) do lokálneho priečinka
    backups/ - volá sa automaticky PRED obnovou, nech je vždy k
    dispozícii posledný stav pred prípadnou chybnou obnovou.
    """

    ensure_backups_dir()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUPS_DIR / f"pred-obnovou-{timestamp}.zip"

    backup_path.write_bytes(create_backup_bytes())

    return backup_path


def _extract_and_validate_db_bytes(zf: zipfile.ZipFile) -> bytes:
    """
    Vytiahne database.db zo ZIP archívu zálohy a overí, že je to
    skutočne platná SQLite databáza tejto appky (obsahuje očakávané
    tabuľky) - nie hocijaký súbor. Vyhodí HTTPException, ak validácia
    zlyhá.
    """

    if "database.db" not in zf.namelist():

        raise HTTPException(
            status_code=422,
            detail="Záloha neobsahuje database.db - nie je to platná záloha tejto appky."
        )

    db_bytes = zf.read("database.db")

    if len(db_bytes) == 0:

        raise HTTPException(
            status_code=422,
            detail="database.db v zálohe je prázdny."
        )

    # SQLite súbory začínajú týmto presným 16-bajtovým hlavičkovým
    # reťazcom - rýchla kontrola pred tým, než to vôbec skúšame otvoriť.
    if db_bytes[:16] != b"SQLite format 3\x00":

        raise HTTPException(
            status_code=422,
            detail="database.db v zálohe nie je platná SQLite databáza."
        )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:

        tmp.write(db_bytes)
        tmp_path = Path(tmp.name)

    try:

        conn = sqlite3.connect(str(tmp_path))

        try:

            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )

            table_names = {row[0] for row in cursor.fetchall()}

        except sqlite3.DatabaseError:

            raise HTTPException(
                status_code=422,
                detail="database.db v zálohe sa nepodarilo otvoriť ako SQLite databázu."
            )

        finally:

            conn.close()

        missing_tables = REQUIRED_TABLES - table_names

        if missing_tables:

            raise HTTPException(
                status_code=422,
                detail=(
                    "Záloha nevyzerá ako záloha Digitálneho zošita - "
                    f"chýbajú tabuľky: {', '.join(sorted(missing_tables))}"
                )
            )

    finally:

        tmp_path.unlink(missing_ok=True)

    return db_bytes


def _validate_backup_file(file_bytes: bytes) -> None:
    """
    Overí, že nahraný súbor je platný ZIP so zálohou tejto appky.
    Vyhodí HTTPException, ak validácia zlyhá.
    """

    if len(file_bytes) == 0:

        raise HTTPException(
            status_code=422,
            detail="Nahraný súbor je prázdny."
        )

    if len(file_bytes) > MAX_UPLOAD_SIZE_BYTES:

        raise HTTPException(
            status_code=422,
            detail="Nahraný súbor je príliš veľký."
        )

    try:

        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:

            _extract_and_validate_db_bytes(zf)

    except zipfile.BadZipFile:

        raise HTTPException(
            status_code=422,
            detail=(
                "Súbor nie je platný ZIP archív. Od tejto verzie appky "
                "je záloha celý ZIP (databáza + nahraté súbory), nie "
                "samostatný .db súbor - stiahni si novú zálohu."
            )
        )


def restore_from_upload(file_bytes: bytes) -> Path:
    """
    Obnoví appku (databázu aj uploads/) z nahranej zálohy.

    Postup:
    1. Overí, že nahraný súbor je platná záloha tejto appky.
    2. Uloží AUTOMATICKÚ kompletnú zálohu súčasného stavu (pre prípad chyby).
    3. Zavrie všetky pooled SQLAlchemy spojenia (engine.dispose()) - inak
       by appka mohla po obnove pracovať so zastaraným spojením/cache.
    4. Nahraný obsah databázy skopíruje do živého DB súboru cez SQLite
       backup API.
    5. Nahradí obsah uploads/ presne tým, čo je v zálohe (staré súbory,
       ktoré v zálohe nie sú, sa odstránia - obnova má appku vrátiť do
       PRESNE toho stavu, aký zachytáva záloha).

    Vráti cestu k automatickej zálohe vytvorenej pred obnovou.
    """

    _validate_backup_file(file_bytes)

    safety_backup_path = save_automatic_backup()

    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:

        db_bytes = _extract_and_validate_db_bytes(zf)

        # Zatvoríme všetky poolované SQLAlchemy spojenia PRED obnovou -
        # appka si pri ďalšom použití vytvorí nové spojenia nad (už
        # obnoveným) súborom. Bez tohto kroku by mohlo staré poolované
        # spojenie držať zastaraný stav alebo zámok na pôvodnom súbore.
        engine.dispose()

        db_path = _sqlite_file_path()

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:

            tmp.write(db_bytes)
            tmp_path = Path(tmp.name)

        try:

            source = sqlite3.connect(str(tmp_path))
            destination = sqlite3.connect(str(db_path))

            with destination:
                source.backup(destination)

            source.close()
            destination.close()

        finally:

            tmp_path.unlink(missing_ok=True)


        # Nahradenie uploads/ - appka sa má po obnove nachádzať PRESNE v
        # stave, ktorý záloha zachytáva (nie zlúčenie starého a nového).
        if UPLOADS_DIR.exists():

            shutil.rmtree(UPLOADS_DIR)

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

        for member in zf.namelist():

            if not member.startswith("uploads/") or member.endswith("/"):
                continue

            relative_path = member[len("uploads/"):]

            target_path = UPLOADS_DIR / relative_path

            # Obrana proti path traversal v mene súboru v ZIP archíve -
            # nikdy nezapisovať mimo UPLOADS_DIR.
            if UPLOADS_DIR not in target_path.resolve().parents:
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)

            with zf.open(member) as source_file:

                target_path.write_bytes(source_file.read())


    return safety_backup_path
