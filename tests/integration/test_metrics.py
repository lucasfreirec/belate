import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy.orm import Session

from app.api.deps import get_redis
from app.core import telemetry
from app.core.telemetry import Telemetry
from app.db.models import Outcome
from app.ingest.polling import poll_global, poll_traffic_info
from app.main import app
from app.prim.client import PrimClient
from app.prim.models import Api
from app.prim.quota import QuotaGuard, QuotaLimit
from tests.factories import departure

FIXTURES = Path(__file__).parent.parent / "fixtures" / "prim"
Sessions = Callable[[], Session]
Samples = dict[tuple[str, tuple[tuple[str, str], ...]], float]


def scrape(client: TestClient) -> Samples:
    """Read /metrics into {(sample name, sorted labels): value}."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(response.text)
        for sample in family.samples
    }


def value(samples: Samples, name: str, **labels: str) -> float | None:
    return samples.get((name, tuple(sorted(labels.items()))))


def collector(
    redis: Redis, reply: httpx.Response, daily: int = 1000
) -> tuple[PrimClient, QuotaGuard, Telemetry]:
    """A PRIM client that always gets `reply`, wired to the shared counters."""
    recorder = Telemetry(redis)
    guard = QuotaGuard(redis, {api: QuotaLimit(daily, 0, 0) for api in Api})
    http = httpx.Client(
        base_url="https://prim.test", transport=httpx.MockTransport(lambda request: reply)
    )
    client = PrimClient(http, guard, sleep=lambda _: None, on_attempt=recorder.prim_request)
    return client, guard, recorder


def fixture(name: str) -> httpx.Response:
    body: Any = json.loads((FIXTURES / name).read_text())
    return httpx.Response(200, json=body, headers={"x-ratelimit-remaining-day": "999"})


def unreachable_redis() -> Redis:
    """A client for a Redis that is not there. It fails at once, without retrying."""
    return Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, retry=Retry(NoBackoff(), 0))


class TestTelemetry:
    def test_a_successful_poll_is_counted(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, recorder = collector(redis, fixture("estimated_timetable.json"))
        before = datetime.now(UTC).timestamp()

        poll_global(client, guard, sessions, recorder)
        poll_global(client, guard, sessions, recorder)

        assert telemetry.read(redis, telemetry.PRIM_REQUESTS) == {"global|200": 2}
        # 161 new rows on the first poll; the second sees the same feed and updates them.
        assert telemetry.read(redis, telemetry.DEPARTURES) == {
            "global|new": 161,
            "global|updated": 161,
        }
        assert telemetry.read(redis, telemetry.LAST_POLL)["global"] >= before
        assert telemetry.read(redis, telemetry.POLLS_SKIPPED) == {}
        assert telemetry.read(redis, telemetry.PARSE_ERRORS) == {}

    def test_a_prim_outage_is_counted(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, recorder = collector(redis, httpx.Response(503))

        poll_global(client, guard, sessions, recorder)

        assert telemetry.read(redis, telemetry.PRIM_REQUESTS) == {"global|503": 3}
        assert telemetry.read(redis, telemetry.POLLS_SKIPPED) == {"global|PrimUnavailable": 1}
        assert telemetry.read(redis, telemetry.LAST_POLL) == {}

    def test_a_refusal_by_the_quota_guard_is_counted(
        self, sessions: Sessions, redis: Redis
    ) -> None:
        client, guard, recorder = collector(redis, fixture("estimated_timetable.json"), daily=0)

        poll_global(client, guard, sessions, recorder)

        assert telemetry.read(redis, telemetry.PRIM_REQUESTS) == {}
        assert telemetry.read(redis, telemetry.POLLS_SKIPPED) == {"global|QuotaExhausted": 1}

    def test_an_unreadable_response_is_counted(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, recorder = collector(redis, httpx.Response(200, json={"unexpected": 1}))

        poll_global(client, guard, sessions, recorder)

        assert telemetry.read(redis, telemetry.PRIM_REQUESTS) == {"global|200": 1}
        assert telemetry.read(redis, telemetry.POLLS_SKIPPED) == {"global|ParseError": 1}

    def test_each_api_has_its_own_figures(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, recorder = collector(redis, fixture("traffic_info.json"))

        poll_traffic_info(client, guard, sessions, recorder)

        assert telemetry.read(redis, telemetry.PRIM_REQUESTS) == {"traffic_info|200": 1}
        assert set(telemetry.read(redis, telemetry.LAST_POLL)) == {"traffic_info"}

    def test_recording_never_fails_when_redis_is_down(self) -> None:
        recorder = Telemetry(unreachable_redis())

        recorder.prim_request("global", "200")
        recorder.poll_skipped("global", "PrimUnavailable")
        recorder.poll_succeeded("global", datetime.now(UTC), created=1, updated=2, parse_errors=3)
        recorder.departures_closed("departed", 4)


class TestMetricsEndpoint:
    def test_exports_what_the_collector_recorded(
        self, client: TestClient, sessions: Sessions, redis: Redis
    ) -> None:
        prim, guard, recorder = collector(redis, fixture("estimated_timetable.json"))
        poll_global(prim, guard, sessions, recorder)
        recorder.poll_skipped("unitary", "TooSoon")
        recorder.departures_closed("cancelled", 3)

        samples = scrape(client)

        assert value(samples, "belate_prim_requests_total", api="global", outcome="200") == 1
        assert value(samples, "belate_polls_skipped_total", api="unitary", reason="TooSoon") == 1
        assert value(samples, "belate_departures_ingested_total", api="global", kind="new") == 161
        assert value(samples, "belate_departures_closed_total", outcome="cancelled") == 3
        last_poll = value(samples, "belate_last_successful_poll_timestamp_seconds", api="global")
        assert last_poll is not None
        assert abs(last_poll - datetime.now(UTC).timestamp()) < 60

    def test_exports_quota_use_and_limit_for_every_api(
        self, client: TestClient, redis: Redis
    ) -> None:
        QuotaGuard(redis, {Api.GLOBAL: QuotaLimit(1000, 0, 0)}).reconcile(
            Api.GLOBAL, server_remaining=580
        )

        samples = scrape(client)

        assert value(samples, "belate_quota_used", api="global") == 420
        assert value(samples, "belate_quota_used", api="unitary") == 0
        assert value(samples, "belate_quota_limit", api="global") == 1000
        assert value(samples, "belate_quota_limit", api="traffic_info") == 1000

    def test_exports_the_state_of_the_database(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        seen = datetime(2026, 11, 16, 7, 30, tzinfo=UTC)
        with sessions() as session:
            session.add_all(
                [
                    departure(outcome=Outcome.OPEN, seen_until=seen),
                    departure(outcome=Outcome.OPEN, seen_until=seen),
                    departure(seen_until=seen),
                ]
            )
            session.commit()

        samples = scrape(client)

        assert value(samples, "belate_open_departures") == 2
        assert value(samples, "belate_latest_departure_data_timestamp_seconds") == seen.timestamp()

    def test_an_empty_system_still_answers(self, client: TestClient) -> None:
        samples = scrape(client)

        assert value(samples, "belate_open_departures") == 0
        assert value(samples, "belate_latest_departure_data_timestamp_seconds") is None
        assert value(samples, "belate_quota_used", api="global") == 0

    def test_counts_requests_by_route_not_by_address(self, client: TestClient) -> None:
        def count(samples: Samples, path: str, status: str) -> float:
            name = "belate_http_requests_total"
            return value(samples, name, method="GET", path=path, status=status) or 0

        before = scrape(client)

        client.get("/stations")
        client.get("/stations")
        client.get("/live?station=nowhere")
        client.delete("/subscriptions/123")
        client.delete("/subscriptions/456")
        client.get("/no/such/page")
        after = scrape(client)

        assert count(after, "/stations", "200") - count(before, "/stations", "200") == 2
        assert count(after, "/live", "404") - count(before, "/live", "404") == 1
        # Every unknown address lands in one series, whatever was typed.
        assert count(after, "unmatched", "404") - count(before, "unmatched", "404") == 1
        template = ("method", "DELETE"), ("path", "/subscriptions/{subscription_id}")
        by_id = [
            key for key in after if key[0] == "belate_http_requests_total" and "123" in str(key)
        ]
        assert by_id == []
        assert after[("belate_http_requests_total", (*template, ("status", "401")))] >= 2

    def test_scrapes_are_not_counted_as_requests(self, client: TestClient) -> None:
        scrape(client)
        samples = scrape(client)

        metrics_paths = [key for key in samples if ("path", "/metrics") in key[1]]
        assert metrics_paths == []

    def test_times_requests(self, client: TestClient) -> None:
        client.get("/stations")

        samples = scrape(client)

        name = "belate_http_request_duration_seconds_count"
        assert (value(samples, name, method="GET", path="/stations") or 0) >= 1

    def test_is_not_rate_limited(self, client: TestClient) -> None:
        assert all(client.get("/metrics").status_code == 200 for _ in range(5))

    def test_database_figures_survive_redis_being_down(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        with sessions() as session:
            session.add(departure(outcome=Outcome.OPEN))
            session.commit()
        app.dependency_overrides[get_redis] = unreachable_redis

        samples = scrape(client)

        assert value(samples, "belate_open_departures") == 1
        assert value(samples, "belate_quota_used", api="global") is None


@pytest.mark.parametrize("path", ["/metrics"])
def test_metrics_are_left_out_of_the_api_documentation(client: TestClient, path: str) -> None:
    assert path not in client.get("/openapi.json").json()["paths"]
