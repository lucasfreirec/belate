"""Quota guard: makes sure the project never exceeds a PRIM daily quota.  ✍️ Lucas

Every request to PRIM, retries included, first calls `QuotaGuard.acquire`. The guard counts
the requests of the day in Redis, so that the count is shared by every worker process and
survives restarts. The tests are in tests/integration/test_quota.py; they run against a real
Redis and describe the expected behaviour precisely.

Rules to implement (measured facts are in docs/data.md, "Quotas"):

1. Each API has its own counter.
2. The day changes at midnight UTC, because that is when PRIM resets its quota.
3. A first attempt is refused once `daily - retry_reserve` requests have been used. A retry
   may use the reserve, up to `daily`.
4. A first attempt is refused if the previous first attempt on the same API was less than
   `min_interval` seconds ago. This protects against a task scheduled twice. Retries ignore
   this rule, and a `min_interval` of 0 turns it off. Do not assume requests reach Redis in
   clock order: two workers can call with timestamps a few milliseconds apart, either way
   round.
5. A refused request is not counted.
6. Check-and-count must be atomic: with two workers calling at the same instant, the count
   must never go past the limit.
7. Counters must expire by themselves, so Redis does not fill up with old days.

Design options for rule 6:

- A Lua script run with `redis.register_script(...)`. Redis runs a script atomically, so the
  script can read the counter, compare it and increment it in one step. This is the usual
  answer and the one I recommend. It also sidesteps typing friction: a script call returns
  `Any`, while `redis.incr()` is typed as "int or awaitable" and needs a cast under mypy.
- `INCR` first, then compare, and `DECR` if over the limit. Simple, atomic per command, but
  the counter briefly overshoots and a crash between the two commands leaves it wrong.
- `WATCH` / `MULTI` / `EXEC` (optimistic locking). Correct, but needs a retry loop.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from redis import Redis

from app.prim.models import Api


@dataclass(frozen=True, slots=True)
class QuotaLimit:
    daily: int  # requests allowed per UTC day
    retry_reserve: int  # part of `daily` that only retries may use
    min_interval: float  # minimum seconds between two first attempts


class QuotaExhausted(Exception):
    """The daily budget of this API is used up. Nothing may be sent until the reset."""


class TooSoon(Exception):
    """The previous request on this API is too recent."""


def utc_now() -> datetime:
    return datetime.now(UTC)


class QuotaGuard:
    def __init__(
        self,
        redis: Redis,
        limits: Mapping[Api, QuotaLimit],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._redis = redis
        self._limits = limits
        # Always read the time through the clock, never datetime.now(): tests replace it.
        self._clock = clock

    def acquire(self, api: Api, *, retry: bool = False) -> None:
        """Count one request against today's quota of `api`.

        Raise `QuotaExhausted` or `TooSoon` if the request must not be sent. In that case
        nothing is counted.
        """
        raise NotImplementedError

    def used_today(self, api: Api) -> int:
        """Return how many requests were counted for `api` today (UTC). 0 if none."""
        raise NotImplementedError

    def reconcile(self, api: Api, server_remaining: int) -> None:
        """Correct today's count with what PRIM reports in `x-ratelimit-remaining-day`.

        If PRIM says fewer requests remain than the guard thinks (for example because the
        exploration script used some), raise the count to `daily - server_remaining`. Never
        lower the count: the header can be a few seconds behind.
        """
        raise NotImplementedError
