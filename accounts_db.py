"""
MASTER DATABÁZA KONT

Toto je ÚPLNE SAMOSTATNÁ databáza od business dát appky (faktúry,
zákazníci, zákazky...). Obsahuje len samotné konto (meno, hash hesla) -
nič o konkrétnej firme alebo jej dátach. Tie žijú vo vlastnom SQLite
súbore každého konta (viď tenancy.py), nikdy nie tu.

Prečo samostatná databáza, nie len ďalšia tabuľka v business DB:
appka pri prihlásení ešte NEVIE, ktorého konta databázu má otvoriť -
práve táto (jediná spoločná, vždy rovnaká) databáza je to, čo appka
vie nájsť VŽDY, ešte pred tým, než pozná prihláseného používateľa.

Schéma tejto databázy sa spravuje cez samostatný Alembic reťazec
(alembic_accounts.ini + alembic_accounts/), nie cez ten istý, ktorý
appka používa pre business dáta vo vnútri jednotlivých kont.
"""

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker


ACCOUNTS_DATABASE_URL = os.getenv(
    "ACCOUNTS_DATABASE_URL",
    "sqlite:///./data/accounts.db"
)


# Priečinok pre SQLite súbor (napr. "./data/") git nesleduje (prázdne
# priečinky v ňom nezostávajú) - na čerstvom checkoute by inak SQLite
# zlyhalo s "unable to open database file", lebo "./data/" by vôbec
# neexistoval.
if ACCOUNTS_DATABASE_URL.startswith("sqlite:///"):

    _accounts_db_path = Path(ACCOUNTS_DATABASE_URL.removeprefix("sqlite:///"))
    _accounts_db_path.parent.mkdir(parents=True, exist_ok=True)


# timeout = busy_timeout SQLite: pri súbehu (požiadavka + plánovač záloh)
# zápis chvíľu počká namiesto okamžitého "database is locked".
accounts_engine = create_engine(
    ACCOUNTS_DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 15}
)


if accounts_engine.dialect.name == "sqlite":

    @event.listens_for(accounts_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL: čitatelia neblokujú zapisovateľa (a naopak), pri súbežných
        # požiadavkách tak odpadá väčšina "database is locked". NORMAL je
        # v WAL režime bezpečné voči pádu aplikácie; pri páde celého
        # stroja môže prísť o posledné potvrdené transakcie, nie o súbor.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


AccountsSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=accounts_engine
)


AccountsBase = declarative_base()


def get_accounts_db() -> Generator:
    """FastAPI dependency - session nad master databázou kont."""

    db = AccountsSessionLocal()

    try:
        yield db

    finally:
        db.close()


# =========================================
# MIGRÁCIA accounts.db PRI ŠTARTE
# Na Railway ju robí start.sh pred appkou. Lokálne (`uvicorn main:app`) by
# po aktualizácii kódu ostala databáza kont bez nových stĺpcov
# ("no such column: accounts.is_active"), preto ju appka doplní sama.
# =========================================

_ACCOUNTS_ALEMBIC_INI = Path(__file__).resolve().parent / "alembic_accounts.ini"


def accounts_schema_state() -> str:
    """'ok' | 'outdated' | 'uninitialized' - či je accounts.db na poslednej migrácii."""

    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect

    if not inspect(accounts_engine).has_table("alembic_version"):
        return "uninitialized"

    head = ScriptDirectory.from_config(Config(str(_ACCOUNTS_ALEMBIC_INI))).get_current_head()

    with accounts_engine.connect() as connection:
        current = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()

    return "ok" if current == head else "outdated"


def upgrade_accounts_database(logger=None) -> bool:
    """
    Dotiahne accounts.db na poslednú migráciu (idempotentné). Pred zmenou
    schémy odloží kópiu súboru (len SQLite). Vráti True, ak sa migrovalo.
    Vypnúť sa dá cez AUTO_MIGRATE_ACCOUNTS=false (vtedy appka s chybou skončí).
    """

    if accounts_schema_state() != "outdated":
        return False

    if os.getenv("AUTO_MIGRATE_ACCOUNTS", "true").strip().lower() in ("false", "0", "no"):
        raise RuntimeError(
            "Databáza kont je staršia než kód a AUTO_MIGRATE_ACCOUNTS je vypnuté. "
            "Spusti migrácie:\n\n    alembic -c alembic_accounts.ini upgrade head\n"
        )

    from datetime import datetime

    from alembic import command
    from alembic.config import Config

    if accounts_engine.dialect.name == "sqlite" and _accounts_db_path.exists():

        import shutil

        with accounts_engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

        copy = _accounts_db_path.with_name(
            f"{_accounts_db_path.name}.pre-upgrade-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(_accounts_db_path, copy)

        if logger:
            logger.info("accounts.db: poistná kópia pred migráciou: %s", copy.name)

    command.upgrade(Config(str(_ACCOUNTS_ALEMBIC_INI)), "head")

    if logger:
        logger.info("accounts.db: schéma doplnená na poslednú migráciu.")

    return True
