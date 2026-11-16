"""Integration tests run against real PostgreSQL and Redis, started in Docker containers."""

from collections.abc import Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16", driver="psycopg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer("redis:7") as container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
def redis(redis_url: str) -> Iterator[Redis]:
    """A connection to an empty Redis."""
    client = Redis.from_url(redis_url, decode_responses=True)
    client.flushdb()
    yield client
    client.close()


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str) -> Iterator[Engine]:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(migrated_engine: Engine) -> Iterator[Callable[[], Session]]:
    """A session factory on an empty, migrated database."""
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE departures, disruptions"))
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    opened: list[Session] = []

    def new_session() -> Session:
        opened.append(factory())
        return opened[-1]

    yield new_session
    # A session left open would hold locks and block the next test's TRUNCATE.
    for session in opened:
        session.close()
