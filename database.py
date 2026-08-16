import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
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


# Databázová session
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


# Základ pre databázové modely
Base = declarative_base()