"""
Monitoring prevádzky pre externý nástroj (UptimeRobot, Better Stack, cron).

`/health` zostáva obyčajná kontrola "appka žije" (Railway podľa nej rozhoduje
o nasadení, preto nesmie padať na zastaraných zálohách). Podrobnejší stav
je na `/health/details`, chránený tajným tokenom `MONITOR_TOKEN`:

  * zálohy: posledná úspešná záloha každého konta nie je staršia než
    2 × interval + 1 h, posledný pokus nezlyhal,
  * kópia mimo servera (ak je zapnutá): posledné nahratie prešlo a nie je staré,
  * obnova: žiadna prerušená obnova, ktorú sa nepodarilo vrátiť,
  * migrácie: databáza každého konta je na poslednej revízii,
  * chyby servera: počet neošetrených chýb (HTTP 500) za poslednú hodinu
    nepresiahol MONITOR_MAX_ERRORS_PER_HOUR (predvolene 5),
  * úložisko: na disku s dátami zostáva aspoň 200 MB a 10 %,
  * plánovač záloh: vlákno na pozadí beží.

Odpoveď je 200 {"status": "ok"} alebo 503 {"status": "degraded", "problems": [...]}.
Problémy obsahujú len konto, druh a krátky pevný popis (žiadne cesty ani texty
výnimiek), aby sa dali bezpečne posielať do monitorovacích nástrojov.
"""

import hmac
import logging
import os
import shutil
import threading
import time
from collections import deque
from datetime import timedelta

import auto_backup
import backup_utils
import offsite_backup
import tenancy

logger = logging.getLogger(__name__)


# ---------- počítadlo chýb servera (v pamäti procesu) ----------

ERROR_WINDOW_SECONDS = 3600
MIN_FREE_BYTES = 200 * 1024 * 1024
MIN_FREE_FRACTION = 0.10

_errors: deque = deque(maxlen=1000)
_errors_lock = threading.Lock()


def record_server_error() -> None:

    with _errors_lock:
        _errors.append(time.time())


def recent_server_errors(now: float | None = None) -> int:

    cutoff = (now if now is not None else time.time()) - ERROR_WINDOW_SECONDS

    with _errors_lock:
        return sum(1 for moment in _errors if moment >= cutoff)


def max_errors_per_hour() -> int:

    try:
        return max(1, int(os.getenv("MONITOR_MAX_ERRORS_PER_HOUR", "")))

    except ValueError:
        return 5


def disk_problem() -> str | None:
    """Popis problému, ak na disku s dátami dochádza miesto (inak None)."""

    try:

        directory = tenancy.ACCOUNTS_DATA_DIR

        while not directory.exists() and directory != directory.parent:
            directory = directory.parent

        usage = shutil.disk_usage(directory)

    except OSError:

        logger.exception("Monitoring: zistenie voľného miesta zlyhalo")
        return "voľné miesto na úložisku sa nepodarilo zistiť"

    if usage.free < MIN_FREE_BYTES or usage.free < usage.total * MIN_FREE_FRACTION:
        return "na úložisku dochádza miesto"

    return None


def monitor_token() -> str:

    return os.getenv("MONITOR_TOKEN", "").strip()


def token_matches(provided: str | None) -> bool:

    expected = monitor_token()

    return bool(expected) and bool(provided) and hmac.compare_digest(provided, expected)


def collect(slugs: list[str]) -> dict:

    now = auto_backup.utc_now()
    max_age = timedelta(hours=auto_backup.interval_hours() * 2 + 1)
    problems: list[dict] = []

    def problem(slug: str, kind: str, detail: str) -> None:
        problems.append({"account": slug, "kind": kind, "detail": detail})

    for slug in slugs:

        status = auto_backup.read_status(slug)

        if auto_backup.backups_enabled():

            last_success = auto_backup._parse_iso(status.get("last_success_at"))

            if last_success is None:
                problem(slug, "backup", "zatiaľ neprebehla žiadna záloha")

            elif now - last_success > max_age:
                problem(slug, "backup", "posledná záloha je zastaraná")

            if status.get("last_error"):
                problem(slug, "backup", "posledný pokus o zálohu zlyhal")

        if offsite_backup.enabled():

            offsite = status.get("offsite")

            if offsite is None:
                problem(slug, "offsite", "kópia mimo servera zatiaľ neprebehla")

            elif not offsite.get("ok"):
                problem(slug, "offsite", "posledné nahratie mimo servera zlyhalo alebo neprešlo overením")

            else:

                uploaded = auto_backup._parse_iso(offsite.get("at"))

                if uploaded is None or now - uploaded > max_age:
                    problem(slug, "offsite", "kópia mimo servera je zastaraná")

        if backup_utils.has_unrecovered_restore(slug):
            problem(slug, "restore", "prerušená obnova nebola vrátená, treba zásah")

        try:

            if tenancy.account_needs_migration(slug):
                problem(slug, "migration", "databáza konta nie je na poslednej migrácii")

        except Exception:   # noqa: BLE001 - monitoring nesmie spadnúť na jednom konte

            logger.exception("Monitoring: kontrola migrácie konta %s zlyhala", slug)
            problem(slug, "migration", "stav migrácie sa nepodarilo zistiť")

    error_count = recent_server_errors()

    if error_count >= max_errors_per_hour():
        problem("-", "errors", f"{error_count} neošetrených chýb servera za poslednú hodinu")

    disk = disk_problem()

    if disk:
        problem("-", "disk", disk)

    scheduler = auto_backup._scheduler

    if auto_backup.backups_enabled() and scheduler is not None and not scheduler.is_alive():
        problem("-", "scheduler", "plánovač záloh nebeží")

    return {
        "status": "degraded" if problems else "ok",
        "accounts": len(slugs),
        "problems": problems,
    }
