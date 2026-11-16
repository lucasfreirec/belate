"""Integration tests run against real PostgreSQL and Redis, started in Docker containers."""

from collections.abc import Iterator

import pytest
from redis import Redis
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
