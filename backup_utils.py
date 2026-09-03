"""
Záloha a obnova databázy.

Appka je jednopoužívateľská a beží nad jedným SQLite súborom - záloha
je preto jednoducho kópia tohto súboru, ale robená BEZPEČNE cez natívne
SQLite backup API (sqlite3.Connection.backup()), nie hrubým skopírovaním
súboru. Dôvod: appka môže mať v čase zálohovania otvorené spojenie/
transakciu, a priame kopírovanie súboru na disku by mohlo zachytiť
nekonzistentný stav (napr. uprostred zápisu). Backup API rieši toto
korektne - vytvorí konzistentnú kópiu bez ohľadu na prebiehajúcu
aktivitu.
"""

import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from database import DATABASE_URL


# Tabuľky, ktoré musí mať KAŽDÁ platná záloha tejto appky - slúžia na
# rýchlu kontrolu "je toto vôbec súbor z Digitálneho zošita", skôr než
# ním prepíšeme aktuálnu databázu.
REQUIRED_TABLES = {
    "customers",
    "invoices",
    "invoice_items",
    "jobs",
    "company",
    "alembic_version",
}

BACKUPS_DIR = Path(__file__).parent / "backups"

MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB - veľkorysá rezerva


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


def create_backup_bytes() -> bytes:
    """
    Vytvorí konzistentnú zálohu aktuálnej databázy a vráti jej obsah
    ako bajty (na priame stiahnutie cez appku).
    """

    db_path = _sqlite_file_path()

    if not db_path.exists():

        raise HTTPException(
            status_code=404,
            detail="Databázový súbor neexistuje - appka ešte nemá žiadne dáta."
        )


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


def save_automatic_backup() -> Path:
    """
    Uloží časovo označenú zálohu do lokálneho priečinka backups/ -
    volá sa automaticky PRED obnovou, nech je vždy k dispozícii posledný
    stav pred prípadnou chybnou obnovou.
    """

    ensure_backups_dir()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUPS_DIR / f"pred-obnovou-{timestamp}.db"

    backup_path.write_bytes(create_backup_bytes())

    return backup_path


def _validate_backup_file(file_bytes: bytes) -> None:
    """
    Overí, že nahraný súbor je SKUTOČNE platná SQLite databáza tejto
    appky (obsahuje očakávané tabuľky) - nie hocijaký súbor s príponou
    .db. Vyhodí HTTPException, ak validácia zlyhá.
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

    # SQLite súbory začínajú týmto presným 16-bajtovým hlavičkovým
    # reťazcom - rýchla kontrola pred tým, než to vôbec skúšame otvoriť.
    if file_bytes[:16] != b"SQLite format 3\x00":

        raise HTTPException(
            status_code=422,
            detail="Súbor nie je platná SQLite databáza."
        )


    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:

        tmp.write(file_bytes)
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
                detail="Súbor sa nepodarilo otvoriť ako SQLite databázu."
            )

        finally:

            conn.close()


        missing_tables = REQUIRED_TABLES - table_names

        if missing_tables:

            raise HTTPException(
                status_code=422,
                detail=(
                    "Súbor nevyzerá ako záloha Digitálneho zošita - "
                    f"chýbajú tabuľky: {', '.join(sorted(missing_tables))}"
                )
            )

    finally:

        tmp_path.unlink(missing_ok=True)


def restore_from_upload(file_bytes: bytes) -> Path:
    """
    Obnoví databázu z nahraného súboru.

    Postup:
    1. Overí, že nahraný súbor je platná záloha tejto appky.
    2. Uloží AUTOMATICKÚ zálohu súčasného stavu (pre prípad chyby).
    3. Nahraný obsah skopíruje do živého DB súboru cez SQLite backup API
       (rovnaký bezpečný mechanizmus ako pri zálohovaní, len opačným
       smerom).

    Vráti cestu k automatickej zálohe vytvorenej pred obnovou.
    """

    _validate_backup_file(file_bytes)

    safety_backup_path = save_automatic_backup()

    db_path = _sqlite_file_path()


    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:

        tmp.write(file_bytes)
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


    return safety_backup_path
