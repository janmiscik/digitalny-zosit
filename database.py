import os

from collections.abc import Generator

from dotenv import load_dotenv
from fastapi import HTTPException, Request, status
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

import tenancy


# Načítanie premenných zo súboru .env
load_dotenv()


# URL databázy pre SAMOSTATNÉ použitie mimo bežiacej appky (priamy
# CLI príkaz `alembic upgrade head` bez -c alembic_accounts.ini, lokálny
# vývoj) - appka v behu toto NEPOUŽÍVA, každá požiadavka ide cez
# get_db() nižšie, ktorý si databázu vyberie podľa prihláseného konta
# (tenancy.py). Toto zostáva ako pohodlný fallback pre vývojárske
# nástroje, nie ako zdroj pravdy appky.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./digitalny-zosit.db"
)


# SQLAlchemy engine (pozri poznámku vyššie - appka v behu používa
# tenancy.get_account_engine(), nie toto).
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)


# SQLite MÁ podporu pre FOREIGN KEY constrainty (definované v models.py
# cez ForeignKey(...)), ale defaultne ich pri každom novom spojení
# NEVYNUCUJE - treba to explicitne zapnúť pragmou pri KAŽDOM otvorení
# spojenia (nie je to trvalé nastavenie databázového súboru). Bez tohto
# by napr. bolo možné omylom vytvoriť JobPhoto/Invoice/JobCost s
# job_id, ktoré v tabuľke jobs vôbec neexistuje - appka sa síce na
# takéto dáta zvyčajne nedostane (viaže sa cez existujúce ORM vzťahy),
# ale nič by to na úrovni databázy nezastavilo.
#
# Rovnaký listener sa nastavuje aj v tenancy.get_account_engine() pre
# engine jednotlivých kont - tu zostáva len pre DATABASE_URL fallback
# vyššie.
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Databázová session (fallback, pozri poznámku vyššie)
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Základ pre business databázové modely (Company, Customer, Invoice...)
# - ZDIEĽANÝ naprieč všetkými kontami appky (každé konto má vlastný
# súbor s TOU ISTOU schémou, nie vlastnú triedu modelov). Používa ho aj
# alembic/env.py.
Base = declarative_base()


def get_db(request: Request) -> Generator:
    """
    FastAPI dependency pre business dáta appky (faktúry, zákazníci,
    zákazky...). Databázu VYBERÁ podľa prihláseného konta
    (request.session["account_slug"] - nastaví ho auth.login_user() pri
    prihlásení) - každé konto má svoj vlastný súbor (tenancy.py).

    Ak nie je nikto prihlásený, vráti presne tú istú odpoveď ako
    auth.require_login_page() (presmerovanie na /login) - get_db() totiž
    nemá k dispozícii ŽIADNU databázu, kým appka nevie, o čie konto ide.
    V appke sa get_db() vždy používa spolu s require_login_page/api, toto
    je len druhá poistka pre prípad, že by na niektorej trase chýbala.
    """

    account_slug = request.session.get("account_slug")

    if account_slug is None:

        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )

    tenancy.set_current_tenant(account_slug)

    account_engine = tenancy.get_account_engine(account_slug)

    AccountSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=account_engine
    )

    db = AccountSessionLocal()

    try:
        yield db

    finally:
        db.close()
