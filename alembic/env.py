from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from database import DATABASE_URL, Base
from models import Customer, Job


# Alembic Config objekt
config = context.config


# Nastavenie logovania
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# SQLAlchemy modely
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Spustenie migrácií bez vytvorenia databázového spojenia.
    """

    context.configure(
        url=DATABASE_URL,
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

    configuration["sqlalchemy.url"] = DATABASE_URL

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