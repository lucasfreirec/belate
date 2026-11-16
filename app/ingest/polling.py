"""One polling run per API: fetch, parse, store. A failure is logged and never raised.

PRIM being down, a quota being used up or a response being unreadable are expected events:
the next scheduled run simply tries again.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.ingest.service import IngestStats, ingest_disruptions, ingest_snapshot
from app.prim.client import PrimClient, PrimError, PrimResponse
from app.prim.models import Api, FeedSnapshot
from app.prim.parser import (
    ParseError,
    parse_estimated_timetable,
    parse_stop_monitoring,
    parse_traffic_info,
)
from app.prim.quota import QuotaExhausted, QuotaGuard, QuotaLimit, TooSoon

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]
EXPECTED_FAILURES = (PrimError, QuotaExhausted, TooSoon, ParseError)

# PRIM allows 1,000 requests a day on each API (docs/data.md, "Polling budget"). The
# minimum intervals sit just under the schedules, to block a task that fires twice.
QUOTA_LIMITS = {
    Api.GLOBAL: QuotaLimit(daily=1000, retry_reserve=100, min_interval=90),
    Api.UNITARY: QuotaLimit(daily=1000, retry_reserve=100, min_interval=40),
    Api.TRAFFIC_INFO: QuotaLimit(daily=1000, retry_reserve=50, min_interval=240),
}


def poll_global(
    client: PrimClient, guard: QuotaGuard, sessions: SessionFactory
) -> IngestStats | None:
    return _poll_feed(
        Api.GLOBAL, client.estimated_timetable, parse_estimated_timetable, guard, sessions
    )


def poll_unitary(
    client: PrimClient, guard: QuotaGuard, sessions: SessionFactory, station_ref: str
) -> IngestStats | None:
    return _poll_feed(
        Api.UNITARY,
        lambda: client.stop_monitoring(station_ref),
        parse_stop_monitoring,
        guard,
        sessions,
    )


def poll_traffic_info(
    client: PrimClient, guard: QuotaGuard, sessions: SessionFactory
) -> int | None:
    """Store the current RER B disruptions. Return how many there are, None on failure."""
    try:
        response = client.traffic_info()
        _reconcile(guard, Api.TRAFFIC_INFO, response)
        disruptions, errors = parse_traffic_info(response.body)
    except EXPECTED_FAILURES as exc:
        logger.warning("traffic_info poll skipped: %s: %s", type(exc).__name__, exc)
        return None

    with sessions() as session:
        created = ingest_disruptions(session, disruptions, datetime.now(UTC))
        session.commit()
    logger.info(
        "traffic_info poll: %d disruptions, %d new, %d parse errors",
        len(disruptions),
        created,
        len(errors),
    )
    return len(disruptions)


def _poll_feed(
    api: Api,
    fetch: Callable[[], PrimResponse],
    parse: Callable[[object], FeedSnapshot],
    guard: QuotaGuard,
    sessions: SessionFactory,
) -> IngestStats | None:
    try:
        response = fetch()
        _reconcile(guard, api, response)
        snapshot = parse(response.body)
    except EXPECTED_FAILURES as exc:
        logger.warning("%s poll skipped: %s: %s", api, type(exc).__name__, exc)
        return None

    with sessions() as session:
        stats = ingest_snapshot(session, snapshot)
        session.commit()
    logger.info(
        "%s poll: feed of %s, %d departures, %d new, closed %s, %d parse errors",
        api,
        snapshot.produced_at.isoformat(timespec="seconds"),
        stats.observed,
        stats.created,
        dict(stats.closed),
        stats.parse_errors,
    )
    return stats


def _reconcile(guard: QuotaGuard, api: Api, response: PrimResponse) -> None:
    if response.quota_remaining is not None:
        guard.reconcile(api, response.quota_remaining)
