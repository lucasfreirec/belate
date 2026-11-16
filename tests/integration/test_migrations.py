from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db.base import Base


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migrations_apply_and_match_the_models(postgres_url: str) -> None:
    command.upgrade(alembic_config(postgres_url), "head")

    engine = create_engine(postgres_url)
    with engine.connect() as connection:
        assert "departures" in inspect(connection).get_table_names()
        # No difference between the migrated schema and the models: no migration is missing.
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_migrations_can_be_rolled_back(postgres_url: str) -> None:
    config = alembic_config(postgres_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    engine = create_engine(postgres_url)
    with engine.connect() as connection:
        assert "departures" not in inspect(connection).get_table_names()
    command.upgrade(config, "head")
