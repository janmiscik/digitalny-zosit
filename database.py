import os

from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker


# Načítanie premenných zo súboru .env
load_dotenv()


# URL databázy z .env
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./digitalny-zosit.db"
)


# SQLAlchemy engine
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
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):

        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


# Databázová session
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Základ pre databázové modely
Base = declarative_base()

def get_db() -> Generator:
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()