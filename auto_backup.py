"""
AUTOMATICKÉ ZÁLOHY KONT + TEST OBNOVY

Čo to rieši: appka beží na jednom disku (Railway Volume). Ručné
sťahovanie zálohy z Nastavení je fajn, ale spolieha sa na to, že si naň
niekto spomenie. Tento modul:

  * raz za deň (BACKUP_INTERVAL_HOURS) vytvorí zálohu KAŽDÉHO konta
    (databáza + nahraté súbory) do ACCOUNTS_DATA_DIR/<konto>/backups/,
  * každú zálohu HNEĎ OVERÍ "testom obnovy" (verify_backup_zip) - záloha,
    ktorá sa nedá obnoviť, je horšia než žiadna, lebo dáva falošnú istotu,
  * drží len posledných N záloh od každého druhu (retencia), aby záloha
    s fotkami nezaplnila disk,
  * pred migráciou schémy (nasadenie novej verzie) spraví poistnú zálohu
    (viď manage_accounts.py --backup-all, volá ho start.sh),
  * stav (posledná záloha, výsledok overenia) ukladá do
    ACCOUNTS_DATA_DIR/<konto>/backup-status.json - zobrazuje sa v
    Nastaveniach.

DÔLEŽITÉ: tieto zálohy ležia na TOM ISTOM disku ako dáta. Chránia pred
chybou v appke, nechceným zmazaním, zlou migráciou či poškodením
databázy - NECHRÁNIA pred stratou celého disku. Preto je v Nastaveniach
aj stiahnutie zálohy (ulož ju mimo Railway) a odporúča sa zapnúť aj
Railway Volume Backups.

Nastavenie cez premenné prostredia:
    BACKUP_ENABLED          true/false (predvolené true)
    BACKUP_INTERVAL_HOURS   ako často (predvolené 24)
    BACKUP_KEEP             koľko automatických záloh držať (predvolené 7)
    BACKUP_CHECK_MINUTES    ako často plánovač kontroluje (predvolené 30)
    BACKUP_MIN_FREE_MB      minimum voľného miesta po zálohe (predvolené 200)
    APP_TIMEZONE            zobrazovanie časov (predvolené Europe/Bratislava)
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

import backup_utils
import tenancy

logger = logging.getLogger(__name__)


# =========================================
# NASTAVENIE
# =========================================

def _env_int(name: str, default: int) -> int:

    try:
        return max(1, int(os.getenv(name, "")))

    except ValueError:
        return default


def backups_enabled() -> bool:

    return os.getenv("BACKUP_ENABLED", "true").strip().lower() not in ("false", "0", "no")


def interval_hours() -> int:
    return _env_int("BACKUP_INTERVAL_HOURS", 24)


def check_minutes() -> int:
    return _env_int("BACKUP_CHECK_MINUTES", 30)


def min_free_bytes() -> int:
    return _env_int("BACKUP_MIN_FREE_MB", 200) * 1024 * 1024


# druh zálohy -> (prefix názvu súboru, koľko posledných držať)
def _kinds() -> dict[str, tuple[str, int]]:

    return {
        "auto": ("auto", _env_int("BACKUP_KEEP", 7)),
        "manual": ("rucne", 10),
        "pre-upgrade": ("pred-aktualizaciou", 3),
        "pre-restore": ("pred-obnovou", 5),
    }


KIND_LABELS = {
    "auto": "Automatická",
    "manual": "Ručná",
    "pre-upgrade": "Pred aktualizáciou",
    "pre-restore": "Pred obnovou",
}

BACKUP_NAME_RE = re.compile(
    r"^(auto|rucne|pred-aktualizaciou|pred-obnovou)-\d{8}-\d{6}(-\d+)?\.zip$"
)


def kind_of(name: str) -> str | None:

    match = BACKUP_NAME_RE.match(name)

    if not match:
        return None

    for kind, (prefix, _keep) in _kinds().items():

        if match.group(1) == prefix:
            return kind

    return None


# =========================================
# ČAS
# =========================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:

    if not value:
        return None

    try:

        parsed = datetime.fromisoformat(value)

    except ValueError:
        return None

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def format_local(value: datetime | str | None) -> str:
    """Čas na zobrazenie v časovej zóne APP_TIMEZONE (predvolene
    Europe/Bratislava; ak zóna nie je dostupná, UTC)."""

    if isinstance(value, str):
        value = _parse_iso(value)

    if value is None:
        return "-"

    try:
        zone = ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/Bratislava"))

    except ZoneInfoNotFoundError:
        zone = timezone.utc

    return value.astimezone(zone).strftime("%d.%m.%Y %H:%M")


# =========================================
# STAV (backup-status.json)
# =========================================

def status_path(slug: str) -> Path:
    return tenancy.account_dir(slug) / "backup-status.json"


def read_status(slug: str) -> dict:

    try:
        return json.loads(status_path(slug).read_text(encoding="utf-8"))

    except (OSError, ValueError):
        return {}


def _write_status(slug: str, status: dict) -> None:

    path = status_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)

    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, path)


# =========================================
# ZOZNAM ZÁLOH
# =========================================

@dataclass
class BackupInfo:

    name: str
    kind: str
    size_bytes: int
    created_at: datetime


def list_backups(slug: str) -> list[BackupInfo]:
    """Zálohy konta, najnovšie prvé."""

    directory = tenancy.account_backups_dir(slug)

    if not directory.exists():
        return []

    items = []

    for path in directory.iterdir():

        kind = kind_of(path.name)

        if kind is None or not path.is_file():
            continue

        stat = path.stat()

        items.append(BackupInfo(
            name=path.name,
            kind=kind,
            size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        ))

    items.sort(key=lambda item: (item.created_at, item.name), reverse=True)

    return items


def backup_file_path(slug: str, name: str) -> Path | None:
    """Cesta k zálohe podľa názvu - len ak názov presne zodpovedá vzoru
    zálohy a súbor existuje (žiadne "/", ".." ani cudzie súbory)."""

    if kind_of(name) is None:
        return None

    path = tenancy.account_backups_dir(slug) / name

    return path if path.is_file() else None


def _unique_destination(slug: str, prefix: str) -> Path:

    directory = tenancy.account_backups_dir(slug)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = utc_now().strftime("%Y%m%d-%H%M%S")

    suffix = 1

    while True:

        name = f"{prefix}-{stamp}.zip" if suffix == 1 else f"{prefix}-{stamp}-{suffix}.zip"
        candidate = directory / name

        if not candidate.exists():
            return candidate

        suffix += 1


def apply_retention(slug: str) -> list[str]:
    """Pre každý druh zálohy ponechá len N najnovších, staršie zmaže.
    Vráti názvy zmazaných súborov."""

    removed = []

    kept_by_kind = {kind: keep for kind, (_p, keep) in _kinds().items()}

    counters: dict[str, int] = {}

    for info in list_backups(slug):   # najnovšie prvé

        counters[info.kind] = counters.get(info.kind, 0) + 1

        if counters[info.kind] > kept_by_kind[info.kind]:

            try:

                (tenancy.account_backups_dir(slug) / info.name).unlink()
                removed.append(info.name)

            except OSError:
                logger.exception("Nepodarilo sa zmazať starú zálohu %s", info.name)

    return removed


# =========================================
# TEST OBNOVY (overenie zálohy)
# =========================================

@dataclass
class VerifyReport:

    ok: bool = False
    problems: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    files_checked: int = 0
    revision: str | None = None
    schema_current: bool = False

    def as_dict(self) -> dict:

        return {
            "ok": self.ok,
            "problems": self.problems,
            "counts": self.counts,
            "files_checked": self.files_checked,
            "revision": self.revision,
            "schema_current": self.schema_current,
        }


def _referenced_archive_names(conn: sqlite3.Connection) -> list[str]:
    """Súbory z uploads/, na ktoré odkazuje databáza (logo, podpis,
    fotky zákaziek), ako názvy položiek v ZIP-e zálohy."""

    names = []

    try:

        row = conn.execute(
            "SELECT logo_filename, signature_filename FROM company LIMIT 1"
        ).fetchone()

        for filename in (row or ()):

            if filename:
                names.append(f"uploads/{filename}")

    except sqlite3.Error:
        pass

    try:

        for (filename,) in conn.execute("SELECT filename FROM job_photos"):

            if filename:
                names.append(f"uploads/job_photos/{filename}")

    except sqlite3.Error:
        pass

    return names


def _head_revision() -> str | None:

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(tenancy.PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(tenancy.PROJECT_ROOT / "alembic"))

    return ScriptDirectory.from_config(cfg).get_current_head()


def _orm_smoke_test(db_file: Path) -> str | None:
    """Prečíta záložnú databázu cez modely appky (zákazníci, faktúry s
    položkami, zákazky, ponuky). Vráti text chyby alebo None."""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import joinedload, sessionmaker

    from models import Customer, Invoice, Job, Quote

    engine = create_engine(f"sqlite:///{db_file}")

    try:

        db = sessionmaker(bind=engine)()

        try:

            db.query(Customer).limit(5).all()
            db.query(Job).limit(5).all()
            db.query(Quote).limit(5).all()
            db.query(Invoice).options(joinedload(Invoice.items)).limit(5).all()

        finally:
            db.close()

    except Exception as exc:   # noqa: BLE001 - každá chyba = záloha sa nedá použiť

        return str(exc).splitlines()[0][:200]

    finally:
        engine.dispose()

    return None


def verify_backup_zip(path: Path, expected_counts: dict[str, int] | None = None) -> VerifyReport:
    """
    TEST OBNOVY: zálohu rozbalí do dočasného priečinka (rovnako ako by
    sa obnovovala), ale nič živé nemení. Overí:

      1. ZIP je čitateľný a všetky súbory v ňom prejdú kontrolou CRC,
      2. database.db je platná SQLite databáza appky a prejde
         `PRAGMA integrity_check`,
      3. počty riadkov (zákazníci, faktúry, zákazky...) sedia s tým, čo
         bolo v databáze v okamihu zálohovania (ak ich poznáme),
      4. každý súbor, na ktorý odkazuje databáza (logo, podpis, fotky),
         v zálohe naozaj je,
      5. ak je schéma záložnej databázy aktuálna, appka z nej vie čítať
         cez svoje modely (zákazníci, faktúry s položkami, zákazky...).
    """

    report = VerifyReport()

    try:

        with zipfile.ZipFile(path) as zf:

            backup_utils._validate_zip_members(zf)

            corrupted = zf.testzip()

            if corrupted:

                report.problems.append(f"Súbor '{corrupted}' v zálohe je poškodený (CRC).")
                return report

            db_bytes = backup_utils._extract_and_validate_db_bytes(zf)

            archive_names = set(zf.namelist())

    except zipfile.BadZipFile:

        report.problems.append("Záloha nie je platný ZIP archív.")
        return report

    except HTTPException as exc:

        report.problems.append(str(exc.detail))
        return report

    except OSError as exc:

        report.problems.append(f"Zálohu sa nepodarilo prečítať: {exc}")
        return report

    except Exception as exc:   # noqa: BLE001 - poškodený archív môže vyhodiť čokoľvek (zlib.error, EOFError, NotImplementedError...)

        report.problems.append(f"Záloha je poškodená a nedá sa prečítať ({exc.__class__.__name__}).")
        return report

    with tempfile.TemporaryDirectory() as tmp:

        db_file = Path(tmp) / "database.db"
        db_file.write_bytes(db_bytes)

        conn = sqlite3.connect(str(db_file))

        try:

            report.counts = backup_utils.table_counts_from_connection(conn)

            try:

                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
                report.revision = row[0] if row else None

            except sqlite3.Error:
                report.revision = None

            referenced = _referenced_archive_names(conn)

        finally:
            conn.close()

        if expected_counts:

            for table, expected in expected_counts.items():

                actual = report.counts.get(table)

                if actual != expected:

                    report.problems.append(
                        f"Počet záznamov v '{table}' nesedí "
                        f"(pri zálohovaní {expected}, v zálohe {actual})."
                    )

        for archive_name in referenced:

            report.files_checked += 1

            if archive_name not in archive_names:
                report.problems.append(f"V zálohe chýba súbor {archive_name}.")

        report.schema_current = report.revision is not None and report.revision == _head_revision()

        if report.schema_current:

            smoke_problem = _orm_smoke_test(db_file)

            if smoke_problem:
                report.problems.append(f"Appka nevie čítať databázu zo zálohy: {smoke_problem}")

    report.ok = not report.problems

    return report


# =========================================
# VYTVORENIE ZÁLOHY
# =========================================

_backup_lock = threading.RLock()


@dataclass
class BackupResult:

    ok: bool
    slug: str
    kind: str
    file: str | None = None
    error: str | None = None
    verify: VerifyReport | None = None


def _dir_size(path: Path) -> int:

    if not path.exists():
        return 0

    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _check_free_space(slug: str) -> None:

    directory = tenancy.account_backups_dir(slug)
    directory.mkdir(parents=True, exist_ok=True)

    database = tenancy.account_database_path(slug)

    estimate = (database.stat().st_size if database.exists() else 0) \
        + _dir_size(tenancy.account_uploads_dir(slug))

    needed = int(estimate * 1.2) + min_free_bytes()

    free = shutil.disk_usage(directory).free

    if free < needed:

        raise RuntimeError(
            f"Na disku je málo voľného miesta na zálohu (voľných {free // (1024 * 1024)} MB, "
            f"potrebných približne {needed // (1024 * 1024)} MB)."
        )


def run_backup(slug: str, kind: str = "auto") -> BackupResult:
    """
    Vytvorí a OVERÍ zálohu konta `slug`. Záloha, ktorá overením
    neprejde, sa zmaže (nesmie vytlačiť dobré zálohy cez retenciu ani
    budiť dojem, že je v poriadku). Výsledok sa zapíše do
    backup-status.json. Nikdy nevyhodí výnimku.
    """

    prefix = _kinds()[kind][0]

    with _backup_lock, tenancy.tenant_context(slug):

        now = utc_now()

        status = read_status(slug)
        status["last_attempt_at"] = now.isoformat()

        destination = None

        try:

            _check_free_space(slug)

            destination = _unique_destination(slug, prefix)

            counts = backup_utils.write_backup_to_file(destination)

            report = verify_backup_zip(destination, expected_counts=counts)

        except HTTPException as exc:

            return _record_failure(slug, kind, status, str(exc.detail), destination)

        except Exception as exc:   # noqa: BLE001 - plánovač nesmie spadnúť

            logger.exception("Záloha konta %s zlyhala", slug)

            return _record_failure(slug, kind, status, str(exc) or exc.__class__.__name__, destination)

        status["verification"] = {
            "at": utc_now().isoformat(),
            "file": destination.name,
            **report.as_dict(),
        }

        if not report.ok:

            destination.unlink(missing_ok=True)

            return _record_failure(
                slug, kind, status,
                "Záloha neprešla overením obnovy: " + "; ".join(report.problems),
                None,
            )

        status["last_success_at"] = utc_now().isoformat()
        status["last_file"] = destination.name
        status["last_kind"] = kind
        status["last_error"] = None

        if kind == "auto":
            status["last_auto_success_at"] = status["last_success_at"]

        removed = apply_retention(slug)

        _write_status(slug, status)

        logger.info(
            "Záloha konta %s (%s) vytvorená a overená: %s; zmazané staré: %s",
            slug, kind, destination.name, removed or "-"
        )

        return BackupResult(ok=True, slug=slug, kind=kind, file=destination.name, verify=report)


def _record_failure(slug, kind, status, message, destination) -> BackupResult:

    if destination is not None:
        Path(destination).unlink(missing_ok=True)
        Path(str(destination) + ".part").unlink(missing_ok=True)

    status["last_error"] = message
    status["last_error_at"] = utc_now().isoformat()

    try:
        _write_status(slug, status)

    except OSError:
        logger.exception("Nepodarilo sa zapísať stav zálohovania konta %s", slug)

    logger.error("Záloha konta %s (%s) zlyhala: %s", slug, kind, message)

    return BackupResult(ok=False, slug=slug, kind=kind, error=message)


def verify_existing_backup(slug: str, name: str | None = None) -> BackupResult:
    """Spustí test obnovy nad už existujúcou zálohou (predvolene
    najnovšou) a výsledok zapíše do stavu. Nič nevytvára ani nemaže."""

    with _backup_lock:

        backups = list_backups(slug)

        if name is None:
            target = backups[0] if backups else None

        else:
            target = next((item for item in backups if item.name == name), None)

        status = read_status(slug)

        if target is None:

            return BackupResult(ok=False, slug=slug, kind="auto", error="Zatiaľ neexistuje žiadna záloha na overenie.")

        report = verify_backup_zip(tenancy.account_backups_dir(slug) / target.name)

        status["verification"] = {
            "at": utc_now().isoformat(),
            "file": target.name,
            **report.as_dict(),
        }

        _write_status(slug, status)

        return BackupResult(
            ok=report.ok, slug=slug, kind=target.kind, file=target.name,
            error=None if report.ok else "; ".join(report.problems), verify=report,
        )


# =========================================
# PLÁNOVAČ
# =========================================

def account_slugs() -> list[str]:

    from accounts_db import AccountsSessionLocal
    from accounts_models import Account

    db = AccountsSessionLocal()

    try:
        return [account.slug for account in db.query(Account).order_by(Account.id).all()]

    finally:
        db.close()


RETRY_AFTER_FAILURE = timedelta(hours=1)


def backup_due(slug: str, now: datetime) -> bool:

    if not tenancy.account_database_path(slug).exists():
        return False

    status = read_status(slug)

    last_attempt = _parse_iso(status.get("last_attempt_at"))
    last_success = _parse_iso(status.get("last_auto_success_at"))

    if last_success is not None and now - last_success < timedelta(hours=interval_hours()):
        return False

    # po neúspechu nie častejšie než raz za hodinu (nezahlcovať log a disk)
    if status.get("last_error") and last_attempt is not None and now - last_attempt < RETRY_AFTER_FAILURE:
        return False

    return True


def run_due_backups(now: datetime | None = None) -> list[BackupResult]:
    """Zálohuje všetky kontá, ktorým už uplynul interval."""

    now = now or utc_now()

    results = []

    for slug in account_slugs():

        if backup_due(slug, now):
            results.append(run_backup(slug, "auto"))

    return results


class BackupScheduler:
    """Vlákno na pozadí: po štarte chvíľu počká a potom v pravidelných
    intervaloch kontroluje, či niektoré konto nepotrebuje zálohu."""

    def __init__(self, start_delay_seconds: float = 60.0):

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_delay = start_delay_seconds

    def start(self) -> None:

        if self._thread is not None:
            return

        self._thread = threading.Thread(target=self._loop, name="backup-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:

        self._stop.set()

        if self._thread is not None:
            self._thread.join(timeout=5)

        self._thread = None

    def _loop(self) -> None:

        if self._stop.wait(self._start_delay):
            return

        while True:

            try:
                run_due_backups()

            except Exception:   # noqa: BLE001 - vlákno nesmie skončiť
                logger.exception("Plánovač záloh narazil na chybu")

            if self._stop.wait(check_minutes() * 60):
                return


_scheduler: BackupScheduler | None = None


def start_scheduler() -> None:

    global _scheduler

    if not backups_enabled() or _scheduler is not None:
        return

    _scheduler = BackupScheduler(start_delay_seconds=float(os.getenv("BACKUP_START_DELAY_SECONDS", "60")))
    _scheduler.start()

    logger.info("Plánovač automatických záloh spustený (interval %s h)", interval_hours())


def stop_scheduler() -> None:

    global _scheduler

    if _scheduler is not None:

        _scheduler.stop()
        _scheduler = None


# =========================================
# PREHĽAD PRE NASTAVENIA
# =========================================

def backup_overview(slug: str) -> dict:
    """Údaje pre sekciu Zálohy v Nastaveniach."""

    status = read_status(slug)
    backups = list_backups(slug)
    verification = status.get("verification")
    now = utc_now()

    warnings = []

    last_success = _parse_iso(status.get("last_success_at"))

    if backups_enabled():

        if last_success is None:
            warnings.append("Zatiaľ neprebehla žiadna záloha.")

        elif now - last_success > timedelta(hours=interval_hours() * 2 + 1):
            warnings.append(f"Posledná záloha je stará ({format_local(last_success)}) - skontroluj chyby v logu.")

    else:

        warnings.append("Automatické zálohovanie je vypnuté (BACKUP_ENABLED=false).")

    if status.get("last_error"):
        warnings.append(f"Posledná chyba zálohovania: {status['last_error']}")

    if verification and not verification.get("ok"):
        warnings.append("Posledné overenie obnovy zlyhalo: " + "; ".join(verification.get("problems", [])))

    try:
        free_bytes = shutil.disk_usage(tenancy.account_dir(slug) if tenancy.account_dir(slug).exists() else tenancy.ACCOUNTS_DATA_DIR).free

    except OSError:
        free_bytes = None

    return {
        "enabled": backups_enabled(),
        "interval_hours": interval_hours(),
        "keep": _kinds()["auto"][1],
        "last_success_at": format_local(last_success),
        "last_file": status.get("last_file"),
        "verification": (
            {**verification, "at_display": format_local(verification.get("at"))}
            if verification else None
        ),
        "warnings": warnings,
        "backups": [
            {
                "name": item.name,
                "label": KIND_LABELS[item.kind],
                "size_mb": round(item.size_bytes / (1024 * 1024), 2),
                "created_display": format_local(item.created_at),
            }
            for item in backups
        ],
        "total_size_mb": round(sum(item.size_bytes for item in backups) / (1024 * 1024), 2),
        "free_mb": None if free_bytes is None else free_bytes // (1024 * 1024),
    }


# =========================================
# MIGRÁCIE KONT PRI ŠTARTE APPKY
# =========================================

def upgrade_accounts_on_startup() -> list[str]:
    """
    Kontám, ktorým čaká migrácia schémy, ju pri štarte appky spraví
    (s poistnou zálohou pred ňou). Na Railway to robí aj start.sh -
    toto pokrýva lokálny beh (`uvicorn main:app`), kde po aktualizácii
    kódu inak ostane databáza bez nových stĺpcov ("no such column").
    Idempotentné; zlyhanie jedného konta nezastaví štart ani ostatné.
    Vypnúť sa dá cez AUTO_MIGRATE_ACCOUNTS=false.
    Vráti slugy kont, ktoré sa zmigrovali.
    """

    if os.getenv("AUTO_MIGRATE_ACCOUNTS", "true").strip().lower() in ("false", "0", "no"):
        return []

    upgraded = []

    for slug in account_slugs():

        try:

            if not tenancy.account_needs_migration(slug):
                continue

            backup = run_backup(slug, "pre-upgrade")

            if not backup.ok:
                logger.warning("Poistná záloha konta %s pred migráciou zlyhala: %s", slug, backup.error)

            tenancy.upgrade_account_database(slug)
            tenancy.discard_account_engine(slug)

            upgraded.append(slug)

            logger.info("Konto %s zmigrované na najnovšiu schému.", slug)

        except Exception:   # noqa: BLE001

            logger.exception("Migrácia konta %s pri štarte zlyhala", slug)

    return upgraded
