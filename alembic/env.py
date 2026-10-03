from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from database import DATABASE_URL, Base
from models import Company, Customer, Invoice, InvoiceItem, Job


# Alembic Config objekt
config = context.config


# Nastavenie logovania.
#
# disable_existing_loggers=False je KRITICKÉ - migrácie teraz bežia aj
# PROGRAMATICKY, vnútri bežiacej appky (tenancy.upgrade_account_database,
# volané pri KAŽDEJ registrácii nového konta), nielen ako jednorazový
# CLI príkaz. fileConfig() defaultne (disable_existing_loggers=True)
# VYPNE každý logger, ktorý už existuje a nie je vymenovaný v
# alembic.ini (napr. "uploads_utils", "main", "backup_utils") - takže
# by appka po prvej registrácii potichu stratila vlastné logovanie na
# zvyšok behu procesu.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


# SQLAlchemy modely
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Spustenie migrácií bez vytvorenia databázového spojenia.
    """

    # config.get_main_option(...) MÁ PREDNOSŤ pred DATABASE_URL z
    # database.py - tenancy.upgrade_account_database() nastavuje
    # sqlalchemy.url programaticky (per-konto súbor), nie cez .env.
    # Bez tejto priority by appka pri provisioningu nového konta vždy
    # migrovala ten istý (pôvodný, globálny) DATABASE_URL namiesto
    # súboru konkrétneho konta.
    db_url = config.get_main_option("sqlalchemy.url") or DATABASE_URL

    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named"
        },
        compare_type=True,
    )

    with context.begin_transaction():

        context.run_migrations()


def run_migrations_online() -> None:
    """
    Spustenie migrácií s databázovým spojením.
    """

    configuration = config.get_section(
        config.config_ini_section,
        {}
    )

    # Rovnaká priorita ako v run_migrations_offline() vyššie.
    configuration["sqlalchemy.url"] = (
        config.get_main_option("sqlalchemy.url") or DATABASE_URL
    )

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():

            context.run_migrations()


if context.is_offline_mode():

    run_migrations_offline()

else:

    run_migrations_online()