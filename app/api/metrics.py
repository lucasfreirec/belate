"""`/metrics` for Prometheus: what the collector is doing and how the API is answering.

Collection figures are read from Redis and PostgreSQL at each scrape (the workers write
them, see app/core/telemetry.py). API request figures are counted in this process.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, get_redis
from app.core import telemetry
from app.db.models import Departure, Outcome
from app.ingest.polling import QUOTA_LIMITS
from app.prim.quota import QuotaGuard

logger = logging.getLogger(__name__)
router = APIRouter()

# A registry of our own: the default one would also export unrelated Python internals.
HTTP_REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "belate_http_requests_total",
    "API requests answered.",
    ["method", "path", "status"],
    registry=HTTP_REGISTRY,
)
HTTP_DURATION = Histogram(
    "belate_http_request_duration_seconds",
    "Time taken to answer an API request.",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
    registry=HTTP_REGISTRY,
)


async def observe_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Middleware: count each request and time it."""
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # The route template ("/subscriptions/{subscription_id}"), not the raw path:
        # otherwise every ID and every mistyped address would become its own series.
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if path != "/metrics":
            HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            HTTP_DURATION.labels(request.method, path).observe(time.perf_counter() - started)


class CollectionMetrics:
    """Reads the collector's state from Redis and PostgreSQL when Prometheus asks."""

    def __init__(self, redis: Redis, session: Session) -> None:
        self._redis = redis
        self._session = session

    def collect(self) -> Iterator[Metric]:
        # A source that is down drops its own metrics; the rest are still exported, and the
        # missing series are what the alert rules notice.
        try:
            yield from self._from_redis()
        except RedisError:
            logger.warning("metrics: Redis unavailable")
        try:
            yield from self._from_database()
        except SQLAlchemyError:
            logger.warning("metrics: database unavailable")

    def _from_redis(self) -> Iterator[Metric]:
        yield _counter(
            "belate_prim_requests",
            "HTTP attempts to PRIM, by API and outcome (status code or error).",
            ["api", "outcome"],
            telemetry.read(self._redis, telemetry.PRIM_REQUESTS),
        )
        yield _counter(
            "belate_polls_skipped",
            "Polls that stored nothing, by API and reason.",
            ["api", "reason"],
            telemetry.read(self._redis, telemetry.POLLS_SKIPPED),
        )
        yield _counter(
            "belate_departures_ingested",
            "Departures stored by polls: new rows, and updates of known rows.",
            ["api", "kind"],
            telemetry.read(self._redis, telemetry.DEPARTURES),
        )
        yield _counter(
            "belate_parse_errors",
            "Items of PRIM responses that could not be parsed.",
            ["api"],
            telemetry.read(self._redis, telemetry.PARSE_ERRORS),
        )
        yield _counter(
            "belate_departures_closed",
            "Departures that reached a final outcome.",
            ["outcome"],
            telemetry.read(self._redis, telemetry.CLOSED),
        )

        last_poll = GaugeMetricFamily(
            "belate_last_successful_poll_timestamp_seconds",
            "Unix time of the last poll that stored data, by API.",
            labels=["api"],
        )
        for api, timestamp in telemetry.read(self._redis, telemetry.LAST_POLL).items():
            last_poll.add_metric([api], timestamp)
        yield last_poll

        guard = QuotaGuard(self._redis, QUOTA_LIMITS)
        used = GaugeMetricFamily(
            "belate_quota_used", "PRIM requests counted today (UTC), by API.", labels=["api"]
        )
        limit = GaugeMetricFamily(
            "belate_quota_limit", "PRIM requests allowed per day, by API.", labels=["api"]
        )
        for api, quota in QUOTA_LIMITS.items():
            used.add_metric([api], guard.used_today(api))
            limit.add_metric([api], quota.daily)
        yield used
        yield limit

    def _from_database(self) -> Iterator[Metric]:
        open_departures = self._session.scalar(
            select(func.count()).where(Departure.outcome == Outcome.OPEN)
        )
        yield GaugeMetricFamily(
            "belate_open_departures",
            "Departures still expected: listed by the feed and not yet over.",
            value=open_departures or 0,
        )
        latest: datetime | None = self._session.scalar(select(func.max(Departure.last_seen_at)))
        if latest is not None:
            yield GaugeMetricFamily(
                "belate_latest_departure_data_timestamp_seconds",
                "Unix time at which PRIM produced the newest departure data stored.",
                value=latest.timestamp(),
            )


def _counter(name: str, help_text: str, labels: list[str], values: dict[str, float]) -> Metric:
    """Build a counter from a telemetry hash whose fields are label values joined by "|"."""
    family = CounterMetricFamily(name, help_text, labels=labels)
    for field, value in sorted(values.items()):
        parts = field.split("|")
        if len(parts) == len(labels):
            family.add_metric(parts, value)
    return family


@router.get("/metrics", include_in_schema=False)
def metrics(
    request: Request, session: SessionDep, redis: Annotated[Redis, Depends(get_redis)]
) -> Response:
    # Prometheus scrapes the API directly. A request carrying X-Forwarded-For came through
    # the public proxy, which should already have blocked it: refuse it here as well.
    if "x-forwarded-for" in request.headers:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    collection = CollectorRegistry()
    collection.register(CollectionMetrics(redis, session))
    body = generate_latest(collection) + generate_latest(HTTP_REGISTRY)
    return Response(body, media_type=CONTENT_TYPE_LATEST)
