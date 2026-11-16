from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers the tables on Base.metadata)
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Any constant works, as long as nothing else uses it as an advisory lock key.
MIGRATION_LOCK = 20261117


def database_url() -> str:
    # Tests pass their own URL; otherwise use the application settings.
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    """Print the SQL instead of running it (`alembic upgrade head --sql`)."""
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            # Every application pod runs the migrations before it starts, so several can
            # run at once. This lock makes them take turns: the first applies what is
            # missing, the others then find the database up to date. It is released when
            # the transaction ends.
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
