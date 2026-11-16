"""Counters about collection, kept in Redis so every process shares them.

The workers run as several processes, and the numbers must survive their restarts, so the
usual in-process Prometheus counters would not do. The workers write here; the API reads it
back when Prometheus scrapes `/metrics` (see app/api/metrics.py).

Recording must never break collection: if Redis is unavailable the event is dropped.
"""

import logging
from datetime import datetime

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

PRIM_REQUESTS = "telemetry:prim_requests"  # field "api|outcome" -> count
POLLS_SKIPPED = "telemetry:polls_skipped"  # field "api|reason" -> count
LAST_POLL = "telemetry:last_poll"  # field api -> Unix time of the last successful poll
DEPARTURES = "telemetry:departures"  # field "api|new" or "api|updated" -> count
PARSE_ERRORS = "telemetry:parse_errors"  # field api -> count
CLOSED = "telemetry:closed"  # field outcome -> count


class Telemetry:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def prim_request(self, api: str, outcome: str) -> None:
        """One HTTP attempt to PRIM. `outcome` is the status code, or the error's name."""
        self._add(PRIM_REQUESTS, f"{api}|{outcome}", 1)

    def poll_skipped(self, api: str, reason: str) -> None:
        """A poll that stored nothing: PRIM failed, the quota refused, the body was unreadable."""
        self._add(POLLS_SKIPPED, f"{api}|{reason}", 1)

    def poll_succeeded(
        self, api: str, at: datetime, *, created: int, updated: int, parse_errors: int
    ) -> None:
        self._add(DEPARTURES, f"{api}|new", created)
        self._add(DEPARTURES, f"{api}|updated", updated)
        self._add(PARSE_ERRORS, api, parse_errors)
        try:
            self._redis.hset(LAST_POLL, api, at.timestamp())
        except RedisError:
            logger.debug("telemetry dropped: last poll of %s", api)

    def departures_closed(self, outcome: str, count: int) -> None:
        self._add(CLOSED, outcome, count)

    def _add(self, key: str, field: str, amount: int) -> None:
        if amount == 0:
            return
        try:
            self._redis.hincrby(key, field, amount)
        except RedisError:
            logger.debug("telemetry dropped: %s %s", key, field)


def read(redis: Redis, key: str) -> dict[str, float]:
    """Return one telemetry hash as {field: number}."""
    raw = redis.hgetall(key)
    return (
        {str(field): float(value) for field, value in raw.items()} if isinstance(raw, dict) else {}
    )
