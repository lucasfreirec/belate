"""Quota guard: makes sure the project never exceeds a PRIM daily quota.

Every request to PRIM, retries included, first calls `QuotaGuard.acquire`. The guard counts
the requests of the day in Redis, so that the count is shared by every worker process and
survives restarts. Measured facts about the quotas are in docs/data.md.

Rules:

1. Each API has its own counter.
2. The day changes at midnight UTC, because that is when PRIM resets its quota.
3. A first attempt is refused once `daily - retry_reserve` requests have been used. A retry
   may use the reserve, up to `daily`.
4. A first attempt is refused if the previous first attempt on the same API was less than
   `min_interval` seconds ago. This protects against a task scheduled twice. Retries ignore
   this rule, and a `min_interval` of 0 turns it off.
5. A refused request is not counted.
6. Check-and-count is atomic: it runs as one Lua script, which Redis executes without
   interleaving any other command. Two workers calling at the same instant can therefore
   never both take the last request.
7. Counters expire by themselves after two days.
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


# Both scripts return 0 when the request is counted.
_EXHAUSTED = 1
_TOO_SOON = 2

# KEYS: today's counter, timestamp of the last first attempt.
# ARGV: cap for this request, minimum interval ('' to skip the check), now, key lifetime.
_ACQUIRE = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used >= tonumber(ARGV[1]) then
  return 1
end
if ARGV[2] ~= '' then
  local last = redis.call('GET', KEYS[2])
  if last and tonumber(ARGV[3]) - tonumber(last) < tonumber(ARGV[2]) then
    return 2
  end
  redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[4])
end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 0
"""

# KEYS: today's counter. ARGV: the count PRIM implies, key lifetime.
_RAISE_TO = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if tonumber(ARGV[1]) > used then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
end
return 0
"""

KEY_LIFETIME_SECONDS = 2 * 24 * 3600


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
        # The time is always read through the clock, so tests can replace it.
        self._clock = clock
        self._acquire = redis.register_script(_ACQUIRE)
        self._raise_to = redis.register_script(_RAISE_TO)

    def acquire(self, api: Api, *, retry: bool = False) -> None:
        """Count one request against today's quota of `api`.

        Raise `QuotaExhausted` or `TooSoon` if the request must not be sent. In that case
        nothing is counted.
        """
        limit = self._limits[api]
        now = self._clock()
        cap = limit.daily if retry else limit.daily - limit.retry_reserve
        check_interval = not retry and limit.min_interval > 0
        result = self._acquire(
            keys=[self._counter_key(api, now), f"quota:{api}:last"],
            args=[
                cap,
                limit.min_interval if check_interval else "",
                now.timestamp(),
                KEY_LIFETIME_SECONDS,
            ],
        )
        if result == _EXHAUSTED:
            raise QuotaExhausted(f"{api}: daily budget of {cap} requests used")
        if result == _TOO_SOON:
            raise TooSoon(f"{api}: previous request less than {limit.min_interval} s ago")

    def used_today(self, api: Api) -> int:
        """Return how many requests were counted for `api` today (UTC). 0 if none."""
        raw = self._redis.get(self._counter_key(api, self._clock()))
        return int(raw) if isinstance(raw, str | bytes) else 0

    def reconcile(self, api: Api, server_remaining: int) -> None:
        """Correct today's count with what PRIM reports in `x-ratelimit-remaining-day`.

        If PRIM says fewer requests remain than the guard thinks (for example because the
        exploration script used some), raise the count to `daily - server_remaining`. The
        count is never lowered: the header can be a few seconds behind.
        """
        implied = self._limits[api].daily - server_remaining
        self._raise_to(
            keys=[self._counter_key(api, self._clock())], args=[implied, KEY_LIFETIME_SECONDS]
        )

    @staticmethod
    def _counter_key(api: Api, now: datetime) -> str:
        return f"quota:{api}:{now.astimezone(UTC).date().isoformat()}"
