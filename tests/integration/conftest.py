"""Integration tests run against real PostgreSQL and Redis, started in Docker containers."""

from collections.abc import Callable, Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from app.api.deps import get_redis, get_resolver, get_session
from app.core.config import Settings, get_settings
from app.main import app


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
        connection.execute(
            text("TRUNCATE departures, disruptions, subscriptions, alerts RESTART IDENTITY")
        )
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    opened: list[Session] = []

    def new_session() -> Session:
        opened.append(factory())
        return opened[-1]

    yield new_session
    # A session left open would hold locks and block the next test's TRUNCATE.
    for session in opened:
        session.close()


PUBLIC_HOST = "hooks.example.com"
_FAKE_DNS = {
    PUBLIC_HOST: ["93.184.216.34"],
    "internal.example.com": ["10.0.0.5"],
    "mixed.example.com": ["93.184.216.34", "127.0.0.1"],
}


def fake_resolve(host: str) -> list[str]:
    """Stands in for DNS, so tests never depend on the network."""
    return _FAKE_DNS.get(host, [])


API_TOKEN = "test-token"


@pytest.fixture
def api_settings() -> Settings:
    return Settings(api_token=SecretStr(API_TOKEN), rate_limit_per_minute=1000)


@pytest.fixture
def client(
    sessions: Callable[[], Session], redis: Redis, api_settings: Settings
) -> Iterator[TestClient]:
    """The API, wired to the test database and Redis."""

    def session_for_request() -> Iterator[Session]:
        yield sessions()

    app.dependency_overrides[get_session] = session_for_request
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_settings] = lambda: api_settings
    app.dependency_overrides[get_resolver] = lambda: fake_resolve
    yield TestClient(app)
    app.dependency_overrides.clear()
