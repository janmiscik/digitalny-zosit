from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from accounts_db import ACCOUNTS_DATABASE_URL, AccountsBase
from accounts_models import Account  # noqa: F401 - musí byť importované, aby ho AccountsBase.metadata videl


# Alembic Config objekt
config = context.config


# Nastavenie logovania - disable_existing_loggers=False, rovnaký dôvod
# ako v alembic/env.py (táto accounts migrácia síce dnes beží len raz
# pri štarte appky cez CLI, ale pre istotu/konzistenciu rovnako).
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


# SQLAlchemy modely (len Account - táto databáza nemá nič spoločné
# s business dátami appky, viď accounts_db.py)
target_metadata = AccountsBase.metadata


def run_migrations_offline() -> None:
    """
    Spustenie migrácií bez vytvorenia databázového spojenia.
    """

    context.configure(
        url=ACCOUNTS_DATABASE_URL,
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

    configuration["sqlalchemy.url"] = ACCOUNTS_DATABASE_URL

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
