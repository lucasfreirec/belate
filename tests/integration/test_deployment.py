"""Behaviour the containers rely on: health checks, the /api prefix, concurrent migrations."""

import os
import subprocess
import sys
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_session
from app.main import app
from tests.integration.test_migrations import alembic_config

Sessions = Callable[[], Session]


class TestHealthChecks:
    def test_health_answers_without_the_database(self, client: TestClient) -> None:
        assert client.get("/health").json() == {"status": "ok"}

    def test_ready_when_the_database_answers(self, client: TestClient) -> None:
        response = client.get("/ready")

        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    def test_not_ready_when_the_database_is_down(self, client: TestClient) -> None:
        unreachable = create_engine(
            "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
            connect_args={"connect_timeout": 2},
        )

        def broken_session() -> Iterator[Session]:
            with sessionmaker(unreachable)() as session:
                yield session

        app.dependency_overrides[get_session] = broken_session

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json() == {"detail": "The database is unavailable."}


class TestBehindTheProxy:
    """uvicorn runs with --root-path /api: it adds the prefix the proxy stripped."""

    def test_routes_answer_under_the_prefix(self, client: TestClient) -> None:
        proxied = TestClient(app, root_path="/api")

        assert proxied.get("/api/health").status_code == 200
        assert proxied.get("/api/stations").status_code == 200

    def test_the_documentation_asks_for_the_schema_under_the_prefix(
        self, client: TestClient
    ) -> None:
        proxied = TestClient(app, root_path="/api")

        page = proxied.get("/api/docs").text

        assert "'/api/openapi.json'" in page
        assert proxied.get("/api/openapi.json").json()["servers"] == [{"url": "/api"}]

    def test_without_a_prefix_nothing_changes(self, client: TestClient) -> None:
        assert "'/openapi.json'" in client.get("/docs").text
        assert "servers" not in client.get("/openapi.json").json()

    def test_metrics_refuse_requests_that_came_through_the_proxy(self, client: TestClient) -> None:
        assert client.get("/metrics").status_code == 200
        assert client.get("/metrics", headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 404


def test_several_processes_can_migrate_at_once(postgres_url: str) -> None:
    """Every application pod migrates before starting; they must not trip over each other."""
    config = alembic_config(postgres_url)
    from alembic import command

    command.downgrade(config, "base")
    environment = {**os.environ, "DATABASE_URL": postgres_url}

    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(4)
    ]
    outputs = [process.communicate(timeout=120)[0] for process in processes]

    failures = [out for process, out in zip(processes, outputs, strict=True) if process.returncode]
    assert failures == [], failures[0][-2000:] if failures else ""
    with create_engine(postgres_url).connect() as connection:
        tables = set(inspect(connection).get_table_names())
    assert {"departures", "disruptions", "stations", "subscriptions", "alerts"} <= tables


@pytest.fixture(autouse=True)
def _restore_overrides() -> Iterator[None]:
    """Tests here replace dependencies; put back what the `client` fixture installed."""
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)
