"""
ROZLÍŠENIE DÁT JEDNOTLIVÝCH KONT (multi-tenancy)

Každé konto má svoj vlastný, úplne samostatný SQLite súbor s business
dátami (faktúry, zákazníci, zákazky...), vlastný priečinok s fotkami/
logom a vlastný priečinok so zálohami - presne taký istý "svet", aký
mala appka predtým ako jediný, globálny.

Fyzické oddelenie na úrovni súborov (nie len stĺpec "čí je to záznam"
v jednej spoločnej databáze) je zámer: appka pri obsluhe jedného konta
jednoducho NEOTVORÍ súbor iného konta - nie je to teda otázka toho, či
niekde v nejakom dotaze náhodou chýba filter, čo by pri appke tejto
veľkosti bolo zbytočné riziko navyše (presne tej triedy chýb, akú appka
mala napr. pri ceste k fotke zákazky).

Priečinková štruktúra (ACCOUNTS_DATA_DIR, predvolene ./data/accounts):

    data/accounts/<slug>/database.db
    data/accounts/<slug>/uploads/
    data/accounts/<slug>/backups/
"""

import re
from contextvars import ContextVar
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


# =========================================
# CESTY
# =========================================

import os

ACCOUNTS_DATA_DIR = Path(os.getenv("ACCOUNTS_DATA_DIR", "./data/accounts"))

PROJECT_ROOT = Path(__file__).resolve().parent


def account_dir(slug: str) -> Path:
    return ACCOUNTS_DATA_DIR / slug


def account_database_path(slug: str) -> Path:
    return account_dir(slug) / "database.db"


def account_database_url(slug: str) -> str:
    return f"sqlite:///{account_database_path(slug)}"


def account_uploads_dir(slug: str) -> Path:
    return account_dir(slug) / "uploads"


def account_backups_dir(slug: str) -> Path:
    return account_dir(slug) / "backups"


# =========================================
# SLUG (priečinok konta na disku)
#
# Odvodený z username pri registrácii, ale zámerne SAMOSTATNÉ pole
# (viď accounts_models.Account) - raz pridelený slug sa už nemení, aj
# keby si používateľ zmenil username.
# =========================================

def slugify_username(username: str) -> str:
    """
    Zmení username na bezpečný názov priečinka: len malé písmená,
    číslice a pomlčky. Keby po tomto čistení ostal prázdny reťazec
    (napr. username zložený len z emoji), použije sa "ucet" ako
    základ - `ensure_unique_slug` k nemu pripojí číslo, aby bol
    jedinečný.
    """

    normalized = re.sub(r"[^a-z0-9]+", "-", username.strip().lower())
    normalized = normalized.strip("-")

    return normalized or "ucet"


def ensure_unique_slug(base_slug: str, slug_exists) -> str:
    """
    `slug_exists` je funkcia slug -> bool (typicky dotaz do accounts
    DB). Pri kolízii pripojí -2, -3... - rovnaký princíp ako appka už
    používa pri kolízii mena bezpečnostnej zálohy (backup_utils.py).
    """

    if not slug_exists(base_slug):
        return base_slug

    suffix = 2

    while slug_exists(f"{base_slug}-{suffix}"):
        suffix += 1

    return f"{base_slug}-{suffix}"


# =========================================
# PROVISIONING (nové konto)
# =========================================

def provision_account(slug: str) -> None:
    """
    Vytvorí priečinkovú štruktúru nového konta a spustí naň business
    migrácie appky (ten istý reťazec ako `alembic upgrade head` pre
    pôvodnú appku, len namierený na súbor TOHTO konta namiesto na
    jeden globálny DATABASE_URL).
    """

    account_dir(slug).mkdir(parents=True, exist_ok=True)
    account_uploads_dir(slug).mkdir(parents=True, exist_ok=True)
    account_backups_dir(slug).mkdir(parents=True, exist_ok=True)

    upgrade_account_database(slug)


def upgrade_account_database(slug: str) -> None:
    """
    Spustí business-migrácie appky (alembic.ini/alembic/) nad súborom
    KONKRÉTNEHO konta. Používa sa pri registrácii (nové, prázdne
    konto) aj pri nasadení appky s novými migráciami (spustí sa pre
    VŠETKY existujúce kontá - viď manage_accounts.py).
    """

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", account_database_url(slug))

    command.upgrade(cfg, "head")


# =========================================
# ENGINE CACHE
#
# Vytvoriť SQLAlchemy engine nie je zadarmo (otvorenie spojenia,
# nastavenie poolu) - keby appka vytvárala nový engine pri KAŽDEJ
# jednej požiadavke, zbytočne by to spomaľovalo každú stránku. Namiesto
# toho sa engine pre dané konto vytvorí raz a nechá v pamäti pre
# ďalšie požiadavky, kým appka beží.
# =========================================

_engine_cache: dict[str, Engine] = {}
_engine_cache_lock = Lock()


def get_account_engine(slug: str) -> Engine:

    existing = _engine_cache.get(slug)

    if existing is not None:
        return existing

    with _engine_cache_lock:

        # Dvojitá kontrola - iné vlákno mohlo engine vytvoriť, kým
        # sme čakali na zámok.
        existing = _engine_cache.get(slug)

        if existing is not None:
            return existing

        engine = create_engine(
            account_database_url(slug),
            connect_args={"check_same_thread": False}
        )

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):

            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        _engine_cache[slug] = engine

        return engine


# =========================================
# AKTUÁLNE OBSLUHOVANÉ KONTO (per-request)
#
# ContextVar namiesto parametra v každej funkcii - veci ako
# uploads_utils.py alebo backup_utils.py majú veľa funkcií a volaní
# naprieč viacerými routermi; prerábať signatúru úplne všetkých by
# bola obrovská a krehká zmena. ContextVar je pre toto bežný a bezpečný
# nástroj - asyncio (a teda aj FastAPI) ho izoluje PER REQUEST/task,
# takže sa nemôže "preliať" z jednej súbežnej požiadavky do druhej.
#
# Nastavuje sa NEZÁVISLE na dvoch miestach (auth.require_login_page/api
# aj database.get_db) - obe čerpajú z toho istého zdroja pravdy
# (request.session["account_slug"]), takže nezáleží na tom, v akom
# poradí FastAPI dependencies vyrieši (čo nie je nikde garantované).
# =========================================

_current_tenant: ContextVar[str | None] = ContextVar(
    "current_tenant",
    default=None
)


def set_current_tenant(slug: str) -> None:
    _current_tenant.set(slug)


def get_current_tenant() -> str:
    """
    Vráti slug aktuálne obsluhovaného konta. Ak nie je nastavený
    (programátorská chyba - táto funkcia sa volá mimo požiadavky
    prihláseného používateľa), vyhodí jasnú chybu namiesto toho, aby
    ticho spadla na None a appka skúsila pracovať so zlým priečinkom.
    """

    slug = _current_tenant.get()

    if slug is None:

        raise RuntimeError(
            "get_current_tenant() zavolané mimo požiadavky prihláseného "
            "konta - aktuálne konto nie je nastavené."
        )

    return slug


def discard_account_engine(slug: str) -> None:
    """
    Zahodí (a zavrie) cachovaný engine pre dané konto - používa sa v
    testoch, aby sa medzi testami nezdieľalo spojenie na súbor z
    predošlého testu. V bežnej prevádzke appky sa nevolá.
    """

    with _engine_cache_lock:

        engine = _engine_cache.pop(slug, None)

    if engine is not None:
        engine.dispose()
