from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from redis import Redis

from app.prim.models import Api
from app.prim.quota import QuotaExhausted, QuotaGuard, QuotaLimit, TooSoon

# Delete this line once QuotaGuard is implemented.
pytestmark = pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="✍️ to implement")

NOON = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """A clock the test moves by hand, so no test has to wait."""

    def __init__(self, now: datetime = NOON) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def limits(
    daily: int = 10, retry_reserve: int = 0, min_interval: float = 0
) -> dict[Api, QuotaLimit]:
    return {api: QuotaLimit(daily, retry_reserve, min_interval) for api in Api}


def use(guard: QuotaGuard, api: Api, count: int, *, retry: bool = False) -> None:
    for _ in range(count):
        guard.acquire(api, retry=retry)


class TestDailyLimit:
    def test_counts_each_request(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits())

        assert guard.used_today(Api.GLOBAL) == 0
        use(guard, Api.GLOBAL, 3)
        assert guard.used_today(Api.GLOBAL) == 3

    def test_refuses_requests_past_the_limit(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=5))
        use(guard, Api.GLOBAL, 5)

        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.GLOBAL)

    def test_a_refused_request_is_not_counted(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=5))
        use(guard, Api.GLOBAL, 5)

        for _ in range(3):
            with pytest.raises(QuotaExhausted):
                guard.acquire(Api.GLOBAL)

        assert guard.used_today(Api.GLOBAL) == 5

    def test_each_api_has_its_own_counter(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=2))
        use(guard, Api.GLOBAL, 2)

        guard.acquire(Api.UNITARY)

        assert guard.used_today(Api.GLOBAL) == 2
        assert guard.used_today(Api.UNITARY) == 1
        assert guard.used_today(Api.TRAFFIC_INFO) == 0

    def test_each_api_has_its_own_limit(self, redis: Redis) -> None:
        guard = QuotaGuard(
            redis,
            {Api.GLOBAL: QuotaLimit(1, 0, 0), Api.UNITARY: QuotaLimit(3, 0, 0)},
        )
        guard.acquire(Api.GLOBAL)
        use(guard, Api.UNITARY, 3)

        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.GLOBAL)
        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.UNITARY)


class TestRetryReserve:
    def test_first_attempts_cannot_use_the_reserve(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=10, retry_reserve=3))
        use(guard, Api.GLOBAL, 7)

        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.GLOBAL)

    def test_retries_can_use_the_reserve(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=10, retry_reserve=3))
        use(guard, Api.GLOBAL, 7)

        use(guard, Api.GLOBAL, 3, retry=True)

        assert guard.used_today(Api.GLOBAL) == 10

    def test_retries_stop_at_the_daily_limit(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=10, retry_reserve=3))
        use(guard, Api.GLOBAL, 7)
        use(guard, Api.GLOBAL, 3, retry=True)

        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.GLOBAL, retry=True)

        assert guard.used_today(Api.GLOBAL) == 10

    def test_retries_count_against_first_attempts_too(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=10, retry_reserve=3))
        use(guard, Api.GLOBAL, 7, retry=True)

        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.GLOBAL)


class TestDayChange:
    def test_the_count_restarts_at_midnight_utc(self, redis: Redis) -> None:
        clock = FakeClock(datetime(2026, 11, 16, 23, 59, 59, tzinfo=UTC))
        guard = QuotaGuard(redis, limits(daily=2), clock=clock)
        use(guard, Api.GLOBAL, 2)

        clock.advance(2)

        assert guard.used_today(Api.GLOBAL) == 0
        guard.acquire(Api.GLOBAL)
        assert guard.used_today(Api.GLOBAL) == 1

    def test_midnight_in_paris_does_not_restart_the_count(self, redis: Redis) -> None:
        # 22:59 UTC is 23:59 in Paris in winter.
        clock = FakeClock(datetime(2026, 11, 16, 22, 59, 59, tzinfo=UTC))
        guard = QuotaGuard(redis, limits(daily=2), clock=clock)
        use(guard, Api.GLOBAL, 2)

        clock.advance(2)

        assert guard.used_today(Api.GLOBAL) == 2
        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.GLOBAL)

    def test_counters_expire(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits())
        guard.acquire(Api.GLOBAL)

        keys = redis.keys("*")
        assert keys, "the guard stored nothing in Redis"
        for key in keys:
            ttl = redis.ttl(key)
            assert isinstance(ttl, int)
            assert 0 < ttl <= 3 * 24 * 3600, f"{key!r} never expires, or expires too late"


class TestMinimumInterval:
    def test_refuses_a_request_too_close_to_the_previous_one(self, redis: Redis) -> None:
        clock = FakeClock()
        guard = QuotaGuard(redis, limits(min_interval=60), clock=clock)
        guard.acquire(Api.GLOBAL)

        clock.advance(59)

        with pytest.raises(TooSoon):
            guard.acquire(Api.GLOBAL)
        assert guard.used_today(Api.GLOBAL) == 1

    def test_allows_a_request_after_the_interval(self, redis: Redis) -> None:
        clock = FakeClock()
        guard = QuotaGuard(redis, limits(min_interval=60), clock=clock)
        guard.acquire(Api.GLOBAL)

        clock.advance(60)
        guard.acquire(Api.GLOBAL)

        assert guard.used_today(Api.GLOBAL) == 2

    def test_a_refused_request_does_not_restart_the_interval(self, redis: Redis) -> None:
        clock = FakeClock()
        guard = QuotaGuard(redis, limits(min_interval=60), clock=clock)
        guard.acquire(Api.GLOBAL)
        clock.advance(40)
        with pytest.raises(TooSoon):
            guard.acquire(Api.GLOBAL)

        clock.advance(20)

        guard.acquire(Api.GLOBAL)

    def test_retries_ignore_the_interval(self, redis: Redis) -> None:
        clock = FakeClock()
        guard = QuotaGuard(redis, limits(min_interval=60), clock=clock)
        guard.acquire(Api.GLOBAL)

        clock.advance(2)
        guard.acquire(Api.GLOBAL, retry=True)

        assert guard.used_today(Api.GLOBAL) == 2

    def test_a_retry_does_not_restart_the_interval(self, redis: Redis) -> None:
        clock = FakeClock()
        guard = QuotaGuard(redis, limits(min_interval=60), clock=clock)
        guard.acquire(Api.GLOBAL)
        clock.advance(50)
        guard.acquire(Api.GLOBAL, retry=True)

        clock.advance(10)

        guard.acquire(Api.GLOBAL)

    def test_the_interval_is_per_api(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(min_interval=60), clock=FakeClock())
        guard.acquire(Api.GLOBAL)

        guard.acquire(Api.UNITARY)

    def test_the_interval_spans_midnight(self, redis: Redis) -> None:
        clock = FakeClock(datetime(2026, 11, 16, 23, 59, 50, tzinfo=UTC))
        guard = QuotaGuard(redis, limits(min_interval=60), clock=clock)
        guard.acquire(Api.GLOBAL)

        clock.advance(20)

        with pytest.raises(TooSoon):
            guard.acquire(Api.GLOBAL)


class TestSeveralWorkers:
    def test_two_guards_share_the_same_count(self, redis: Redis, redis_url: str) -> None:
        other_connection = Redis.from_url(redis_url, decode_responses=True)
        first = QuotaGuard(redis, limits(daily=3))
        second = QuotaGuard(other_connection, limits(daily=3))

        first.acquire(Api.GLOBAL)
        second.acquire(Api.GLOBAL)
        first.acquire(Api.GLOBAL)

        assert second.used_today(Api.GLOBAL) == 3
        with pytest.raises(QuotaExhausted):
            second.acquire(Api.GLOBAL)
        other_connection.close()

    def test_concurrent_requests_never_pass_the_limit(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=20))

        def attempt(_: int) -> bool:
            try:
                guard.acquire(Api.GLOBAL)
            except QuotaExhausted:
                return False
            return True

        with ThreadPoolExecutor(max_workers=16) as pool:
            allowed = list(pool.map(attempt, range(200)))

        assert allowed.count(True) == 20
        assert guard.used_today(Api.GLOBAL) == 20


class TestReconcile:
    def test_raises_the_count_when_prim_reports_fewer_remaining(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=1000))
        use(guard, Api.GLOBAL, 5)

        # PRIM says 980 remain: 20 were used, 15 of them outside this guard.
        guard.reconcile(Api.GLOBAL, server_remaining=980)

        assert guard.used_today(Api.GLOBAL) == 20

    def test_never_lowers_the_count(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=1000))
        use(guard, Api.GLOBAL, 5)

        guard.reconcile(Api.GLOBAL, server_remaining=999)

        assert guard.used_today(Api.GLOBAL) == 5

    def test_a_reconciled_count_is_enforced(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=1000))

        guard.reconcile(Api.GLOBAL, server_remaining=0)

        with pytest.raises(QuotaExhausted):
            guard.acquire(Api.GLOBAL)

    def test_only_touches_the_given_api(self, redis: Redis) -> None:
        guard = QuotaGuard(redis, limits(daily=1000))

        guard.reconcile(Api.GLOBAL, server_remaining=900)

        assert guard.used_today(Api.UNITARY) == 0
