"""
Kontrola produkčnej konfigurácie PRED spustením (alebo odovzdaním) appky.

    python tools/check_config.py --profile managed            # premenné z prostredia
    python tools/check_config.py --profile managed --env-file .env

Profily podľa spôsobu používania:
    managed        kontá zakladá majiteľ servera (admin stránka alebo CLI); verejná registrácia vypnutá
    self-service   používatelia sa registrujú sami; treba strop počtu kont
    single         jedna firma, jedno konto; registrácia vypnutá

Výstup: OK / VAROVANIE / CHYBA pri každej kontrole. Návratový kód 1, ak je aspoň jedna CHYBA.
Hodnoty tajných premenných sa nikdy nevypisujú. Nič sa nemení ani nespúšťa.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backup_crypto  # noqa: E402
import security_config  # noqa: E402

PROFILES = ("managed", "self-service", "single")
TRUE_VALUES = {"true", "1", "yes", "on"}
FALSE_VALUES = {"false", "0", "no", "off"}
OFFSITE_REQUIRED = ("OFFSITE_BACKUP_BUCKET", "OFFSITE_BACKUP_ACCESS_KEY_ID", "OFFSITE_BACKUP_SECRET_ACCESS_KEY")


@dataclass
class Finding:

    level: str       # OK | WARN | ERROR
    name: str
    message: str


def _get(env, name: str) -> str:

    return (env.get(name) or "").strip()


def _flag(env, name: str, default: bool) -> bool | None:
    """True/False, alebo None pri neplatnej hodnote."""

    raw = _get(env, name).lower()

    if raw == "":
        return default

    if raw in TRUE_VALUES:
        return True

    if raw in FALSE_VALUES:
        return False

    return None


def check_config(env, profile: str) -> list[Finding]:

    if profile not in PROFILES:
        raise ValueError(f"Neznámy profil: {profile}")

    findings: list[Finding] = []

    def add(level: str, name: str, message: str) -> None:
        findings.append(Finding(level, name, message))

    # ---- režim spustenia ----
    try:
        environment = security_config.resolve_environment(env.get("APP_ENV"))

    except RuntimeError:

        add("ERROR", "APP_ENV", "neplatná hodnota (použi production alebo development)")
        environment = "production"

    else:

        if environment == "production":
            add("OK", "APP_ENV", "production")
        else:
            add("ERROR", "APP_ENV", "development: na ostrú prevádzku musí byť production (alebo nenastavené)")

    # ---- tajné kľúče ----
    problems = security_config.secret_key_problems(env.get("SECRET_KEY"))

    if problems:
        add("ERROR", "SECRET_KEY", "; ".join(problems))
    else:
        add("OK", "SECRET_KEY", "nastavený a dosť silný")

    https_only = _flag(env, "SESSION_HTTPS_ONLY", True)

    if https_only is None:
        add("ERROR", "SESSION_HTTPS_ONLY", "neplatná hodnota (true alebo false)")
    elif not https_only:
        add("ERROR", "SESSION_HTTPS_ONLY", "false: prihlasovacia cookie by išla aj cez nezabezpečené spojenie")
    else:
        add("OK", "SESSION_HTTPS_ONLY", "true")

    if _flag(env, "ENABLE_API_DOCS", False):
        add("WARN", "ENABLE_API_DOCS", "zapnuté: /docs a /redoc vypisujú všetky trasy, v produkcii vypni")
    else:
        add("OK", "ENABLE_API_DOCS", "vypnuté")

    # ---- registrácia a správa kont podľa profilu ----
    registration = _flag(env, "REGISTRATION_ENABLED", True)
    cap_raw = _get(env, "MAX_ACCOUNTS")
    admins = [name.strip() for name in _get(env, "SERVER_ADMINS").split(",") if name.strip()]

    try:
        cap = int(cap_raw) if cap_raw else 0

        if cap < 0:
            raise ValueError

    except ValueError:

        add("ERROR", "MAX_ACCOUNTS", "neplatná hodnota (celé číslo 0 alebo viac)")
        cap = 0

    if registration is None:
        add("ERROR", "REGISTRATION_ENABLED", "neplatná hodnota (true alebo false)")

    elif profile in ("managed", "single"):

        if registration:
            add("ERROR", "REGISTRATION_ENABLED", f"profil {profile}: verejná registrácia musí byť false")
        else:
            add("OK", "REGISTRATION_ENABLED", "false")

    else:

        if registration:
            add("OK", "REGISTRATION_ENABLED", "true (samoobslužná registrácia)")
        else:
            add("WARN", "REGISTRATION_ENABLED", "false: samoobslužný profil očakáva otvorenú registráciu")

        if registration and cap == 0:
            add("WARN", "MAX_ACCOUNTS", "bez stropu: kto chce, môže zaplniť disk a zálohy; nastav strop")
        elif registration:
            add("OK", "MAX_ACCOUNTS", f"strop {cap} kont")

    if profile == "managed":

        if admins:
            add("OK", "SERVER_ADMINS", f"{len(admins)} správca(ov)")
        else:
            add("ERROR", "SERVER_ADMINS", "nenastavené: nikto nemôže zakladať kontá cez admin stránku")

    elif profile == "self-service":

        if not admins:
            add("WARN", "SERVER_ADMINS", "nenastavené: nikto nemôže vypnúť konto ani nastaviť heslo cez admin stránku")

    elif admins:
        add("OK", "SERVER_ADMINS", "nastavené")

    # ---- monitoring ----
    token = _get(env, "MONITOR_TOKEN")

    if not token:
        add("WARN", "MONITOR_TOKEN", "nenastavený: /health/details neexistuje, nikto nehlási poškodené zálohy")
    elif len(token) < 32:
        add("WARN", "MONITOR_TOKEN", "kratší než 32 znakov")
    else:
        add("OK", "MONITOR_TOKEN", "nastavený")

    # ---- zálohy ----
    if _flag(env, "BACKUP_ENABLED", True) is False:
        add("ERROR", "BACKUP_ENABLED", "false: automatické zálohy sú vypnuté")
    else:
        add("OK", "BACKUP_ENABLED", "zapnuté")

    offsite_set = [name for name in OFFSITE_REQUIRED if _get(env, name)]

    if not offsite_set:
        add("WARN", "OFFSITE_BACKUP_*", "nenastavené: zálohy ostávajú len na tom istom disku ako dáta")

    elif len(offsite_set) < len(OFFSITE_REQUIRED):

        missing = ", ".join(name for name in OFFSITE_REQUIRED if name not in offsite_set)
        add("ERROR", "OFFSITE_BACKUP_*", f"nekompletné, chýba: {missing} (nahrávanie mimo servera ostane vypnuté)")

    else:
        add("OK", "OFFSITE_BACKUP_*", "kompletné")

    passphrase = _get(env, "BACKUP_ENCRYPTION_KEY")

    if passphrase:

        bad = backup_crypto.passphrase_problems(passphrase)

        if bad:
            add("ERROR", "BACKUP_ENCRYPTION_KEY", "; ".join(bad))
        else:
            add("OK", "BACKUP_ENCRYPTION_KEY", "nastavený")

    elif len(offsite_set) == len(OFFSITE_REQUIRED):
        add("WARN", "BACKUP_ENCRYPTION_KEY", "nenastavený: zálohy v bucketi sú nešifrované a obsahujú dáta zákazníkov")

    # ---- e-mail (obnova hesla) ----
    smtp = [name for name in ("SMTP_HOST", "SMTP_FROM", "APP_BASE_URL") if _get(env, name)]

    if smtp and len(smtp) < 3:
        add("WARN", "SMTP_*", "nekompletné (SMTP_HOST, SMTP_FROM a APP_BASE_URL treba spolu): obnova hesla cez e-mail je vypnutá")

    elif smtp:

        parsed = urlsplit(_get(env, "APP_BASE_URL"))

        if parsed.scheme.lower() != "https" or not parsed.hostname:
            add("ERROR", "APP_BASE_URL", "musí začínať https:// (odkaz na obnovu hesla nesie token)")
        else:
            add("OK", "SMTP_*", "obnova hesla cez e-mail je zapnutá")

    else:
        add("OK", "SMTP_*", "nenastavené (obnova hesla cez e-mail vypnutá, kontá obnovuje správca)")

    # ---- proxy ----
    on_railway = any(_get(env, name) for name in ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID"))

    if _get(env, "FORWARDED_ALLOW_IPS") == "*" and not on_railway:
        add("WARN", "FORWARDED_ALLOW_IPS", "* dôveruje hlavičkám proxy zo všetkých zdrojov; nastav IP/CIDR svojej proxy")

    return findings


def load_env(env_file: str | None):

    if not env_file:
        return dict(os.environ)

    from dotenv import dotenv_values

    values = {key: (value or "") for key, value in dotenv_values(env_file).items()}

    return values


LABELS = {"OK": "OK       ", "WARN": "VAROVANIE", "ERROR": "CHYBA    "}


def main(argv: list[str]) -> int:

    parser = argparse.ArgumentParser(description="Kontrola produkčnej konfigurácie")
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--env-file", help="súbor .env namiesto premenných prostredia")
    args = parser.parse_args(argv[1:])

    if args.env_file and not Path(args.env_file).exists():
        print(f"CHYBA: súbor {args.env_file} neexistuje.")
        return 2

    findings = check_config(load_env(args.env_file), args.profile)

    for finding in findings:
        print(f"{LABELS[finding.level]} {finding.name}: {finding.message}")

    errors = sum(1 for f in findings if f.level == "ERROR")
    warnings = sum(1 for f in findings if f.level == "WARN")

    print(f"\nChýb: {errors}, varovaní: {warnings}")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
