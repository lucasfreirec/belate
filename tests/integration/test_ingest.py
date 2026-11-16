import json
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Departure, DisruptionRecord, Outcome
from app.ingest.polling import poll_global, poll_traffic_info, poll_unitary
from app.ingest.service import ingest_snapshot
from app.prim.client import PrimClient
from app.prim.models import Api, CallStatus, FeedSnapshot
from app.prim.parser import parse_estimated_timetable, parse_stop_monitoring
from app.prim.quota import QuotaGuard, QuotaLimit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "prim"
CITE_UNIVERSITAIRE = "STIF:StopArea:SP:473843:"
Sessions = Callable[[], Session]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def global_snapshot() -> FeedSnapshot:
    return parse_estimated_timetable(load("estimated_timetable.json"))


def unitary_snapshot() -> FeedSnapshot:
    return parse_stop_monitoring(load("stop_monitoring.json"))


def ingest(sessions: Sessions, snapshot: FeedSnapshot) -> Any:
    with sessions() as session:
        stats = ingest_snapshot(session, snapshot)
        session.commit()
    return stats


def count(sessions: Sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(Departure)) or 0


def outcomes(sessions: Sessions) -> Counter[str]:
    with sessions() as session:
        return Counter(session.scalars(select(Departure.outcome)))


class TestIngestSnapshot:
    def test_stores_one_row_per_train_and_station(self, sessions: Sessions) -> None:
        stats = ingest(sessions, global_snapshot())

        assert stats.observed == 161
        assert stats.created == 161
        assert count(sessions) == 161

    def test_the_same_feed_twice_adds_nothing(self, sessions: Sessions) -> None:
        ingest(sessions, global_snapshot())

        stats = ingest(sessions, global_snapshot())

        assert stats.created == 0
        assert count(sessions) == 161

    def test_a_newer_feed_updates_the_rows(self, sessions: Sessions) -> None:
        first = global_snapshot()
        ingest(sessions, first)
        later = replace(
            first,
            produced_at=first.produced_at + timedelta(minutes=2),
            observations=[
                replace(o, expected_departure=o.expected_departure + timedelta(minutes=3))
                if o.expected_departure
                else o
                for o in first.observations
            ],
        )

        stats = ingest(sessions, later)

        assert stats.created == 0
        assert count(sessions) == 161
        with sessions() as session:
            row = session.scalars(
                select(Departure).where(Departure.first_expected_departure.is_not(None)).limit(1)
            ).one()
        assert row.last_expected_departure is not None
        assert row.first_expected_departure is not None
        assert row.last_expected_departure - row.first_expected_departure == timedelta(minutes=3)
        assert row.last_seen_at == later.produced_at

    def test_departures_missing_from_a_newer_feed_are_closed(self, sessions: Sessions) -> None:
        first = global_snapshot()
        ingest(sessions, first)
        gone = first.observations[0].journey_ref
        expected = Counter[str]()
        for o in first.observations:
            if o.journey_ref != gone:
                continue
            its_time = o.expected_departure or o.expected_arrival or o.aimed_departure
            assert its_time is not None
            if o.departure_status is CallStatus.CANCELLED:
                expected[Outcome.CANCELLED] += 1
            elif its_time - (first.produced_at + timedelta(minutes=2)) > timedelta(minutes=5):
                expected[Outcome.UNKNOWN] += 1
            else:
                expected[Outcome.DEPARTED] += 1
        later = replace(
            first,
            produced_at=first.produced_at + timedelta(minutes=2),
            observations=[o for o in first.observations if o.journey_ref != gone],
        )

        stats = ingest(sessions, later)

        assert sum(expected.values()) == 23
        assert stats.closed == expected
        with sessions() as session:
            closed = session.scalars(select(Departure).where(Departure.journey_ref == gone)).all()
        assert Counter(row.outcome for row in closed) == expected
        assert {row.closed_at for row in closed} == {later.produced_at}
        # Closing fills the analytics fields.
        assert all(row.service_date is not None and row.local_hour is not None for row in closed)
        assert all(row.delay_seconds is not None and row.reliable is not None for row in closed)

    def test_an_empty_feed_does_not_close_everything(self, sessions: Sessions) -> None:
        first = global_snapshot()
        ingest(sessions, first)
        open_before = outcomes(sessions)[Outcome.OPEN]
        empty = replace(
            first, produced_at=first.produced_at + timedelta(minutes=2), observations=[]
        )

        stats = ingest(sessions, empty)

        assert open_before == 161
        assert sum(stats.closed.values()) < 10
        assert outcomes(sessions)[Outcome.UNKNOWN] == 0

    def test_both_feeds_update_the_same_rows(self, sessions: Sessions) -> None:
        in_global = global_snapshot()
        unitary = unitary_snapshot()
        ingest(sessions, in_global)
        shared = {(o.journey_ref, o.station_ref) for o in in_global.observations} & {
            (o.journey_ref, o.station_ref) for o in unitary.observations
        }

        stats = ingest(sessions, unitary)

        assert len(shared) == 6
        assert stats.observed == 12
        assert stats.created == 6
        assert count(sessions) == 167

    def test_the_unitary_feed_never_closes_departures(self, sessions: Sessions) -> None:
        ingest(sessions, global_snapshot())
        unitary = unitary_snapshot()
        much_later = replace(unitary, produced_at=unitary.produced_at + timedelta(hours=6))

        stats = ingest(sessions, much_later)

        assert stats.closed == Counter()
        assert outcomes(sessions) == Counter({Outcome.OPEN: 167})


def prim(
    responses: dict[str, httpx.Response], redis: Redis
) -> tuple[PrimClient, QuotaGuard, list[str]]:
    """A PRIM client whose HTTP layer answers from `responses`, keyed by URL path."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return responses[request.url.path]

    http = httpx.Client(base_url="https://prim.test", transport=httpx.MockTransport(handler))
    guard = QuotaGuard(redis, {api: QuotaLimit(1000, 100, 0) for api in Api})
    return PrimClient(http, guard, sleep=lambda _: None), guard, requested


def ok(fixture: str, remaining: int = 999) -> httpx.Response:
    return httpx.Response(
        200, json=load(fixture), headers={"x-ratelimit-remaining-day": str(remaining)}
    )


class TestPolling:
    def test_a_global_poll_stores_departures_and_counts_the_request(
        self, sessions: Sessions, redis: Redis
    ) -> None:
        client, guard, _ = prim({"/estimated-timetable": ok("estimated_timetable.json")}, redis)

        stats = poll_global(client, guard, sessions)

        assert stats is not None
        assert stats.created == 161
        assert count(sessions) == 161
        assert guard.used_today(Api.GLOBAL) == 1

    def test_a_unitary_poll_stores_departures(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, requested = prim({"/stop-monitoring": ok("stop_monitoring.json")}, redis)

        stats = poll_unitary(client, guard, sessions, CITE_UNIVERSITAIRE)

        assert stats is not None
        assert stats.created == 12
        assert requested == ["/stop-monitoring"]
        assert guard.used_today(Api.UNITARY) == 1

    def test_the_count_follows_what_prim_reports(self, sessions: Sessions, redis: Redis) -> None:
        # PRIM says 950 remain although this guard has only seen one request.
        client, guard, _ = prim(
            {"/estimated-timetable": ok("estimated_timetable.json", remaining=950)}, redis
        )

        poll_global(client, guard, sessions)

        assert guard.used_today(Api.GLOBAL) == 50

    def test_a_prim_outage_is_survived(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, requested = prim({"/estimated-timetable": httpx.Response(503)}, redis)

        stats = poll_global(client, guard, sessions)

        assert stats is None
        assert count(sessions) == 0
        # Three attempts were made, and each one used quota.
        assert len(requested) == 3
        assert guard.used_today(Api.GLOBAL) == 3

    def test_an_unreadable_response_is_survived(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, _ = prim(
            {"/estimated-timetable": httpx.Response(200, json={"unexpected": True})}, redis
        )

        assert poll_global(client, guard, sessions) is None
        assert count(sessions) == 0

    def test_nothing_is_sent_once_the_quota_is_used(self, sessions: Sessions, redis: Redis) -> None:
        client, guard, requested = prim(
            {"/estimated-timetable": ok("estimated_timetable.json")}, redis
        )
        guard.reconcile(Api.GLOBAL, server_remaining=0)

        assert poll_global(client, guard, sessions) is None
        assert requested == []

    def test_a_traffic_info_poll_stores_rer_b_disruptions(
        self, sessions: Sessions, redis: Redis
    ) -> None:
        client, guard, _ = prim(
            {"/disruptions_bulk/disruptions/v2": ok("traffic_info.json")}, redis
        )

        assert poll_traffic_info(client, guard, sessions) == 4
        assert poll_traffic_info(client, guard, sessions) == 4

        with sessions() as session:
            rows = session.scalars(select(DisruptionRecord)).all()
        assert len(rows) == 4
        assert {row.cause for row in rows} == {"TRAVAUX"}
        works = next(row for row in rows if row.id.startswith("89063746"))
        assert works.periods[0] == ["2026-12-14T21:45:00+00:00", "2026-12-15T02:00:00+00:00"]
        assert len(works.station_refs) == 6
        assert works.last_seen_at > works.first_seen_at
        assert guard.used_today(Api.TRAFFIC_INFO) == 2
