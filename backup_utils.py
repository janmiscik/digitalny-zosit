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
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

import tenancy
from uploads_utils import uploads_dir

logger = logging.getLogger(__name__)


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


def backups_dir() -> Path:
    """
    Priečinok so zálohami AKTUÁLNE obsluhovaného konta (tenancy.py) -
    každé konto má svoje vlastné zálohy, oddelené od ostatných kont.
    """

    return tenancy.account_backups_dir(tenancy.get_current_tenant())


MAX_UPLOAD_SIZE_BYTES = 200 * 1024 * 1024  # 200 MB - záloha teraz obsahuje aj fotky

# Ochrana proti "zip bomb" pri obnove zo zálohy - škodlivo skonštruovaný
# ZIP môže mať malú KOMPRIMOVANÚ veľkosť (v rámci MAX_UPLOAD_SIZE_BYTES
# vyššie, ktorý limituje len veľkosť nahrávaného súboru), ale po
# rozbalení zabrať rádovo väčšie miesto na disku/v pamäti appky.
# ZipInfo pozná nekomprimovanú veľkosť aj počet položiek priamo z
# centrálneho adresára ZIPu (koniec súboru) - BEZ toho, aby appka
# čokoľvek reálne rozbaľovala - takže túto kontrolu vieme spraviť skôr,
# než appka čo i len začne dáta dekomprimovať.
MAX_BACKUP_ENTRY_COUNT = 20_000
MAX_BACKUP_UNCOMPRESSED_ENTRY_BYTES = 500 * 1024 * 1024  # 500 MB na 1 súbor
MAX_BACKUP_UNCOMPRESSED_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB spolu


def _sqlite_file_path() -> Path:
    """
    Cesta k súboru business databázy AKTUÁLNE obsluhovaného konta
    (tenancy.py) - každé konto má vlastný súbor.
    """

    return tenancy.account_database_path(tenancy.get_current_tenant()).resolve()


def ensure_backups_dir() -> None:

    backups_dir().mkdir(
        parents=True,
        exist_ok=True
    )


def _integrity_check_failure(conn: sqlite3.Connection) -> str | None:
    """
    Spustí `PRAGMA integrity_check` nad danou SQLite databázou. Vráti
    None, ak je všetko v poriadku ("ok"), inak textový popis prvých
    nájdených problémov. Ak sa kontrola nedá ani spustiť (poškodený
    súbor), vráti text chyby - nikdy nevyhodí výnimku.
    """

    try:

        rows = conn.execute("PRAGMA integrity_check").fetchall()

    except sqlite3.DatabaseError as exc:

        return f"kontrolu sa nepodarilo spustiť ({exc})"

    results = [str(row[0]) for row in rows]

    if results == ["ok"]:
        return None

    return "; ".join(results[:3])


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

        # Záloha poškodenej databázy by bola falošnou istotou - radšej
        # ju vôbec nevytvoríme a upozorníme, kým je ešte čo zachraňovať.
        problem = _integrity_check_failure(destination)

        destination.close()

        if problem is not None:

            raise HTTPException(
                status_code=500,
                detail=(
                    "Databáza appky je poškodená (PRAGMA integrity_check: "
                    f"{problem}) - záloha nebola vytvorená."
                )
            )

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

    _write_backup_zip(zip_buffer, db_bytes)

    return zip_buffer.getvalue()


def _write_backup_zip(target, db_bytes: bytes) -> None:
    """Zapíše obsah zálohy (database.db + uploads/) do ZIP-u `target`
    (súbor alebo BytesIO). Spoločné pre stiahnutie aj automatické zálohy."""

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:

        zf.writestr("database.db", db_bytes)

        if uploads_dir().exists():

            for file_path in uploads_dir().rglob("*"):

                if file_path.is_file():

                    arcname = "uploads/" + str(
                        file_path.relative_to(uploads_dir())
                    ).replace("\\", "/")

                    zf.write(file_path, arcname)


# Tabuľky, ktorých počty riadkov sa porovnávajú pri overení zálohy
# (záloha vs. stav databázy v okamihu zálohovania).
COUNTED_TABLES = ("customers", "jobs", "invoices", "invoice_items", "quotes", "job_photos")


def table_counts_from_connection(conn: sqlite3.Connection) -> dict[str, int]:
    """Počty riadkov vo vybraných tabuľkách (chýbajúce tabuľky sa preskočia)."""

    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in COUNTED_TABLES
        if table in existing
    }


def table_counts_from_db_bytes(db_bytes: bytes) -> dict[str, int]:
    """Rovnaké ako table_counts_from_connection, nad SQLite súborom v pamäti."""

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:

        tmp.write(db_bytes)
        tmp_path = Path(tmp.name)

    try:

        conn = sqlite3.connect(str(tmp_path))

        try:
            return table_counts_from_connection(conn)

        finally:
            conn.close()

    finally:
        tmp_path.unlink(missing_ok=True)


def write_backup_to_file(destination: Path) -> dict[str, int]:
    """
    Vytvorí kompletnú zálohu AKTUÁLNEHO konta (databáza + uploads/)
    priamo do súboru `destination` (bez držania celého ZIPu v pamäti)
    a vráti počty riadkov z databázovej snímky - na neskoršie
    porovnanie pri overení zálohy.

    Zapisuje sa do dočasného súboru `.part` a až po úspešnom dokončení
    sa premenuje - nedokončená záloha (pád, plný disk) tak nikdy
    neskončí pod "platným" názvom.
    """

    db_path = _sqlite_file_path()

    if not db_path.exists():

        raise FileNotFoundError("Databázový súbor konta neexistuje.")

    db_bytes = _create_db_snapshot_bytes(db_path)

    counts = table_counts_from_db_bytes(db_bytes)

    destination.parent.mkdir(parents=True, exist_ok=True)

    partial = destination.with_name(destination.name + ".part")

    try:

        with open(partial, "wb") as target:
            _write_backup_zip(target, db_bytes)

        os.replace(partial, destination)

    finally:

        partial.unlink(missing_ok=True)

    return counts


def save_automatic_backup() -> Path:
    """
    Uloží časovo označenú kompletnú zálohu (ZIP) do lokálneho priečinka
    backups/ - volá sa automaticky PRED obnovou, nech je vždy k
    dispozícii posledný stav pred prípadnou chybnou obnovou.

    Názov je založený na čase s presnosťou na sekundy - dve obnovy v
    tej istej sekunde (napr. dvojklik na tlačidlo, alebo obnova
    spustená z dvoch otvorených kariet) by inak vygenerovali RUJNÚCI
    rovnaký názov súboru a druhý zápis by ticho PREPÍSAL prvú
    bezpečnostnú zálohu - teda presne tú zálohu, ktorá mala zachrániť
    dáta pred prvou (možno neúspešnou) obnovou. Preto sa pri kolízii
    pripojí číselná prípona (-2, -3, ...), kým sa nenájde voľný názov.

    Súbor sa otvára s O_EXCL (výhradné vytvorenie) namiesto najprv
    "existuje?" a potom zápisu - tá dvojica krokov by pri dvoch
    naozaj súbežných požiadavkách stále mohla skolidovať (obe stihnú
    zistiť "neexistuje" skôr, než ktorákoľvek zapíše). S O_EXCL zápis
    zlyhá, ak medzitým vznikol súbor s rovnakým menom, a skúsi sa
    ďalšia prípona - správne aj pri súbežnosti, nielen pri opakovanom
    volaní za sebou.
    """

    ensure_backups_dir()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_bytes = create_backup_bytes()

    suffix = 1

    while True:

        name = (
            f"pred-obnovou-{timestamp}.zip"
            if suffix == 1
            else f"pred-obnovou-{timestamp}-{suffix}.zip"
        )
        backup_path = backups_dir() / name

        try:

            fd = os.open(str(backup_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

        except FileExistsError:

            suffix += 1
            continue

        with os.fdopen(fd, "wb") as f:
            f.write(backup_bytes)

        return backup_path


def _validate_zip_members(zf: zipfile.ZipFile) -> None:
    """
    Overí METADÁTA položiek ZIP archívu (počet, nekomprimovaná
    veľkosť každej z nich aj spolu) priamo z centrálneho adresára ZIPu
    - BEZ toho, aby sa čokoľvek reálne rozbaľovalo. Ochrana proti "zip
    bomb": maličký komprimovaný súbor (stále v rámci
    MAX_UPLOAD_SIZE_BYTES), ktorý by sa po rozbalení "nafúkol" na
    rádovo väčšiu veľkosť a zaplnil appke disk alebo pamäť.

    Volať PRED akýmkoľvek zf.read()/zf.extract() na jednotlivých
    položkách.
    """

    infos = zf.infolist()

    if len(infos) > MAX_BACKUP_ENTRY_COUNT:

        raise HTTPException(
            status_code=422,
            detail=(
                f"Záloha obsahuje príliš veľa súborov ({len(infos)}, "
                f"limit je {MAX_BACKUP_ENTRY_COUNT})."
            )
        )

    total_uncompressed = 0

    for info in infos:

        if info.file_size > MAX_BACKUP_UNCOMPRESSED_ENTRY_BYTES:

            raise HTTPException(
                status_code=422,
                detail=(
                    f"Súbor '{info.filename}' v zálohe je po rozbalení "
                    "príliš veľký."
                )
            )

        total_uncompressed += info.file_size

        if total_uncompressed > MAX_BACKUP_UNCOMPRESSED_TOTAL_BYTES:

            raise HTTPException(
                status_code=422,
                detail="Záloha je po rozbalení príliš veľká."
            )


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

            integrity_problem = _integrity_check_failure(conn)

        except sqlite3.DatabaseError:

            raise HTTPException(
                status_code=422,
                detail="database.db v zálohe sa nepodarilo otvoriť ako SQLite databázu."
            )

        finally:

            conn.close()

        if integrity_problem is not None:

            raise HTTPException(
                status_code=422,
                detail=(
                    "database.db v zálohe je poškodený "
                    f"(PRAGMA integrity_check: {integrity_problem})."
                )
            )

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

            _validate_zip_members(zf)

            # CRC VŠETKÝCH súborov (aj fotiek), nielen databázy - poškodená
            # položka sa tak odhalí pred akoukoľvek zmenou živých dát.
            try:

                damaged = zf.testzip()

            except (zlib.error, zipfile.BadZipFile, EOFError, OSError, RuntimeError):

                damaged = "(nečitateľná položka)"

            if damaged is not None:

                raise HTTPException(
                    status_code=422,
                    detail=f"Záloha je poškodená (súbor '{damaged}' v archíve).",
                )

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


def _rollback_to_safety_backup(safety_backup_path: Path) -> None:
    """Vráti databázu a uploads/ zo zálohy vytvorenej tesne pred obnovou."""

    with zipfile.ZipFile(safety_backup_path) as zf:

        db_bytes = _extract_and_validate_db_bytes(zf)

        tenancy.discard_account_engine(tenancy.get_current_tenant())

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:

            tmp.write(db_bytes)
            tmp_path = Path(tmp.name)

        try:

            source = sqlite3.connect(str(tmp_path))
            destination = sqlite3.connect(str(_sqlite_file_path()))

            with destination:
                source.backup(destination)

            source.close()
            destination.close()

        finally:
            tmp_path.unlink(missing_ok=True)

        if uploads_dir().exists():
            shutil.rmtree(uploads_dir())

        uploads_dir().mkdir(parents=True, exist_ok=True)

        for member in zf.namelist():

            if not member.startswith("uploads/") or member.endswith("/"):
                continue

            target_path = uploads_dir() / member[len("uploads/"):]

            if uploads_dir() not in target_path.resolve().parents:
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(zf.read(member))


# =========================================
# ŽURNÁL OBNOVY (ochrana pred pádom procesu uprostred výmeny)
#
# Chyby (výnimky) počas výmeny dát rieši rollback v restore_from_upload.
# Ak ale proces zomrie MEDZI výmenou databázy a výmenou uploads/ (OOM kill,
# reštart kontajnera, výpadok), žiadny kód sa už nespustí. Preto sa tesne
# pred prvou zmenou živých dát zapíše žurnál (kde je poistná záloha) a po
# úspešnom konci alebo vrátení sa zmaže. Pri štarte appky (a v start.sh pred
# migráciami) recover_interrupted_restore() nájde zostatok žurnálu a vráti
# databázu aj súbory z poistnej zálohy - konto tak nikdy nezostane v
# polovične obnovenom stave.
# =========================================

JOURNAL_NAME = "restore-journal.json"


def journal_path(slug: str) -> Path:

    return tenancy.account_dir(slug) / JOURNAL_NAME


def _write_journal(slug: str, safety_backup_path: Path, staging: Path) -> None:

    path = journal_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")

    payload = json.dumps({
        "safety_backup": safety_backup_path.name,
        "staging": staging.name,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }).encode("utf-8")

    # Otvor súbor na zápis, flushni Python buffer a až potom fsyncni
    # zapisovateľný file descriptor. Otvorenie len na čítanie spôsobovalo
    # na Windows OSError: [Errno 9] Bad file descriptor.
    with open(partial, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(partial, path)


def _clear_journal(slug: str) -> None:

    journal_path(slug).unlink(missing_ok=True)


def has_unrecovered_restore(slug: str) -> bool:

    return journal_path(slug).exists()


def _cleanup_restore_leftovers(slug: str) -> None:

    directory = tenancy.account_dir(slug)

    if not directory.exists():
        return

    for item in directory.glob("restore-*"):

        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)


def recover_interrupted_restore(slug: str) -> str | None:
    """
    Vráti konto do stavu spred prerušenej obnovy. Vráti None, ak nebolo čo
    riešiť, "recovered" po úspešnom vrátení, "failed" ak vrátenie nevyšlo
    (žurnál ostáva, aby sa to dalo riešiť ručne a monitoring to hlásil).
    Dočasné adresáre po obnove (aj po páde ešte pred výmenou) sa upracú vždy.
    """

    path = journal_path(slug)

    if not path.exists():

        _cleanup_restore_leftovers(slug)
        return None

    try:

        journal = json.loads(path.read_text(encoding="utf-8"))
        safety = tenancy.account_backups_dir(slug) / journal["safety_backup"]

        if not safety.is_file():
            raise FileNotFoundError(journal["safety_backup"])

        with tenancy.tenant_context(slug):

            _rollback_to_safety_backup(safety)

        _cleanup_restore_leftovers(slug)
        _clear_journal(slug)

        logger.warning("Prerušená obnova konta %s bola vrátená zo zálohy %s", slug, safety.name)

        _audit(slug, "backup.restore_interrupted",
               f"Obnova bola prerušená (pád procesu); stav vrátený zo zálohy {safety.name}.")

        return "recovered"

    except Exception:   # noqa: BLE001

        logger.exception("Vrátenie prerušenej obnovy konta %s zlyhalo", slug)

        return "failed"


def _audit(slug: str, action: str, detail: str) -> None:
    """Záznam do audit_log konta surovým SQL (chyba ho nesmie zhodiť)."""

    try:

        connection = sqlite3.connect(str(tenancy.account_database_path(slug)))

        try:

            connection.execute(
                "INSERT INTO audit_log (created_at, action, entity_type, entity_id, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), action, "backup", None, detail),
            )
            connection.commit()

        finally:
            connection.close()

    except sqlite3.Error:
        pass


def _stage_restore(zf: zipfile.ZipFile, db_bytes: bytes, staging: Path) -> tuple[Path, Path]:
    """
    Pripraví obnovu v DOČASNOM adresári, bez dotyku živých dát:
    databázu zmigruje na aktuálnu schému, overí ju a rozbalí uploads/.
    Akákoľvek chyba tu znamená, že živé dáta ostali nedotknuté.
    """

    staged_db = staging / "database.db"
    staged_uploads = staging / "uploads"

    staged_db.write_bytes(db_bytes)

    # Záloha môže pochádzať zo staršej verzie appky (bez novších tabuliek
    # a stĺpcov) - schéma sa dopĺňa na kópii, ešte pred výmenou živých dát.
    try:

        tenancy.upgrade_database_file(staged_db)

    except Exception as exc:

        logger.exception("Migrácia databázy zo zálohy zlyhala - živé dáta ostávajú nedotknuté")

        raise HTTPException(
            status_code=422,
            detail=(
                "Záloha sa nepodarilo aktualizovať na aktuálnu schému "
                f"databázy ({type(exc).__name__}) - možno pochádza z novšej "
                "verzie appky. Pôvodné dáta zostali nedotknuté."
            )
        )

    conn = sqlite3.connect(str(staged_db))

    try:

        problem = _integrity_check_failure(conn)

    finally:

        conn.close()

    if problem is not None:

        raise HTTPException(
            status_code=422,
            detail=(
                "Databáza po aktualizácii schémy je poškodená "
                f"({problem}). Pôvodné dáta zostali nedotknuté."
            )
        )

    staged_uploads.mkdir(parents=True, exist_ok=True)

    for member in zf.namelist():

        if not member.startswith("uploads/") or member.endswith("/"):
            continue

        target_path = staged_uploads / member[len("uploads/"):]

        # Obrana proti path traversal v mene súboru v ZIP archíve -
        # nikdy nezapisovať mimo uploads/.
        if staged_uploads.resolve() not in target_path.resolve().parents:
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)

        with zf.open(member) as source_file, open(target_path, "wb") as out:

            shutil.copyfileobj(source_file, out)

    return staged_db, staged_uploads


def restore_from_upload(file_bytes: bytes) -> Path:
    """
    Obnoví appku (databázu aj uploads/) z nahranej zálohy ako jednu
    operáciu: buď sa podarí celá, alebo ostane pôvodný stav.

    Postup:
    1. Overí celý ZIP (štruktúru, CRC všetkých súborov, databázu).
    2. Uloží AUTOMATICKÚ kompletnú zálohu súčasného stavu.
    3. V dočasnom adresári pripraví databázu (zmigrovanú na aktuálnu
       schému a overenú) aj súbory. Chyba tu = živé dáta nedotknuté.
    4. Zavrie pooled spojenia a vymení databázu aj uploads/ (staré
       súbory, ktoré v zálohe nie sú, sa odstránia).
    5. Ak zlyhá čokoľvek po začatí výmeny, vráti databázu aj súbory
       z poistnej zálohy.

    Vráti cestu k automatickej zálohe vytvorenej pred obnovou.
    """

    _validate_backup_file(file_bytes)

    safety_backup_path = save_automatic_backup()

    db_path = _sqlite_file_path()
    uploads_path = uploads_dir()

    staging = Path(tempfile.mkdtemp(prefix="restore-", dir=str(db_path.parent)))
    old_uploads = staging / "old-uploads"

    try:

        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:

            db_bytes = _extract_and_validate_db_bytes(zf)

            staged_db, staged_uploads = _stage_restore(zf, db_bytes, staging)

        # Od tohto bodu sa menia živé dáta - každá chyba vracia pôvodný stav
        # a pád procesu rieši žurnál (recover_interrupted_restore pri štarte).
        slug = tenancy.get_current_tenant()

        _write_journal(slug, safety_backup_path, staging)

        try:

            tenancy.discard_account_engine(slug)

            source = sqlite3.connect(str(staged_db))
            destination = sqlite3.connect(str(db_path))

            try:

                with destination:
                    source.backup(destination)

            finally:

                source.close()
                destination.close()

            if uploads_path.exists():
                shutil.move(str(uploads_path), str(old_uploads))

            shutil.move(str(staged_uploads), str(uploads_path))

            _clear_journal(slug)

        except Exception as exc:

            logger.exception("Obnova zlyhala počas výmeny dát - vraciam pôvodný stav")

            try:

                _rollback_to_safety_backup(safety_backup_path)
                _clear_journal(slug)

            except Exception:

                logger.exception("Návrat k poistnej zálohe zlyhal")

                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Obnova zlyhala a návrat pôvodného stavu sa tiež nepodaril. "
                        f"Poistná záloha je v súbore {safety_backup_path.name}."
                    )
                )

            raise HTTPException(
                status_code=500,
                detail=(
                    f"Obnova zlyhala ({type(exc).__name__}). "
                    "Pôvodné dáta boli vrátené."
                )
            )

    finally:

        shutil.rmtree(staging, ignore_errors=True)

    # Záznam o obnove sa musí zapísať AŽ TERAZ, priamo do (už
    # obnoveného) DB súboru - logovať PRED obnovou by bolo zbytočné,
    # keďže obnova celý obsah audit_log tabuľky (spolu so zvyškom
    # databázy) prepíše obsahom zo zálohy. Robí sa to surovým SQL cez
    # čerstvé sqlite3 spojenie (nie cez SQLAlchemy session) - spojenia
    # z poolu boli práve zatvorené vyššie (tenancy.discard_account_engine(tenancy.get_current_tenant())) a appka si
    # nové vytvorí až pri ďalšej požiadavke. Ak by obnovovaná záloha
    # bola zo staršej verzie appky ešte bez audit_log tabuľky, zápis sa
    # ticho preskočí - chýbajúci log záznam nesmie zablokovať inak
    # úspešnú obnovu.
    try:

        connection = sqlite3.connect(str(db_path))

        connection.execute(
            "INSERT INTO audit_log (created_at, action, entity_type, "
            "entity_id, detail) VALUES (?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                "backup.restore",
                "backup",
                None,
                (
                    "Databáza obnovená zo zálohy. Automatická záloha "
                    f"predchádzajúceho stavu: {safety_backup_path.name}"
                )
            )
        )

        connection.commit()
        connection.close()

    except sqlite3.Error:
        pass

    return safety_backup_path
