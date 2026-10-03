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


accounts_engine = create_engine(
    ACCOUNTS_DATABASE_URL,
    connect_args={"check_same_thread": False}
)


if accounts_engine.dialect.name == "sqlite":

    @event.listens_for(accounts_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
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
