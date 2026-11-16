from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics.derive import DERIVE_SQL
from app.analytics.queries import (
    Dimension,
    measurement_error,
    punctuality,
    segments,
    waiting_time,
)
from app.db.models import Departure, Outcome
from app.prim.models import Direction
from tests.factories import (
    CDG_2,
    CITE_UNIVERSITAIRE,
    DENFERT,
    GARE_DU_NORD,
    GENTILLY,
    MONDAY_8AM,
    ROBINSON,
    SAINT_REMY,
    departure,
)

Sessions = Callable[[], Session]
DAY = (datetime(2026, 11, 16, tzinfo=UTC), datetime(2026, 11, 17, tzinfo=UTC))


def store(sessions: Sessions, *rows: Departure) -> Session:
    session = sessions()
    session.add_all(rows)
    session.commit()
    return session


class TestPunctuality:
    def test_overall_figures(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(delay=0),
            departure(delay=60),
            departure(delay=120),
            departure(delay=600),
            departure(outcome=Outcome.CANCELLED),
        )

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.group == {}
        assert row.trains == 4
        assert row.cancelled == 1
        assert row.cancellation_rate == pytest.approx(0.2)
        assert row.avg_delay_seconds == pytest.approx(195)
        # 90th percentile of 0, 60, 120, 600 with linear interpolation.
        assert row.p90_delay_seconds == pytest.approx(456)
        # Only the train 600 seconds late is more than 5 minutes late.
        assert row.late_share == pytest.approx(0.25)

    def test_exactly_five_minutes_late_is_not_late(self, sessions: Sessions) -> None:
        session = store(sessions, departure(delay=300), departure(delay=301))

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.late_share == pytest.approx(0.5)

    def test_early_trains_count_with_a_negative_delay(self, sessions: Sessions) -> None:
        session = store(sessions, departure(delay=-60), departure(delay=120))

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.avg_delay_seconds == pytest.approx(30)

    def test_departures_that_are_not_over_are_ignored(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(delay=60),
            departure(delay=900, outcome=Outcome.OPEN),
            departure(delay=900, outcome=Outcome.UNKNOWN),
        )

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.trains == 1
        assert row.cancelled == 0
        assert row.avg_delay_seconds == pytest.approx(60)

    def test_a_stale_prediction_is_not_an_actual_time(self, sessions: Sessions) -> None:
        # Collection stopped 30 minutes before this train was due.
        stale = departure(delay=0, seen_until=MONDAY_8AM - timedelta(minutes=30))
        session = store(sessions, departure(delay=120), stale)

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.trains == 1
        assert row.avg_delay_seconds == pytest.approx(120)

    def test_a_cancelled_train_counts_even_if_it_left_the_feed_early(
        self, sessions: Sessions
    ) -> None:
        cancelled = departure(
            outcome=Outcome.CANCELLED, seen_until=MONDAY_8AM - timedelta(minutes=30)
        )
        session = store(sessions, cancelled)

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.cancelled == 1
        assert row.cancellation_rate == pytest.approx(1.0)
        assert row.avg_delay_seconds is None

    def test_the_window_includes_its_start_and_excludes_its_end(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(aimed=DAY[0], delay=10),
            departure(aimed=DAY[1] - timedelta(seconds=1), delay=20),
            departure(aimed=DAY[1], delay=1000),
            departure(aimed=DAY[0] - timedelta(seconds=1), delay=1000),
        )

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.trains == 2
        assert row.avg_delay_seconds == pytest.approx(15)

    def test_an_empty_window_gives_one_empty_row(self, sessions: Sessions) -> None:
        (row,) = punctuality(sessions(), since=DAY[0], until=DAY[1])

        assert row.trains == 0
        assert row.cancellation_rate is None
        assert row.avg_delay_seconds is None

    def test_a_terminus_uses_its_arrival_time(self, sessions: Sessions) -> None:
        session = store(sessions, departure(delay=240, terminus=True, station=SAINT_REMY))

        (row,) = punctuality(session, since=DAY[0], until=DAY[1])

        assert row.trains == 1
        assert row.avg_delay_seconds == pytest.approx(240)

    def test_by_direction(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(delay=60),
            departure(delay=180),
            departure(delay=600, direction=Direction.NORTHBOUND, destination=CDG_2),
        )

        rows = punctuality(session, since=DAY[0], until=DAY[1], group_by=[Dimension.DIRECTION])

        by_direction: Any = [(r.group, r.trains, r.avg_delay_seconds) for r in rows]
        assert by_direction == [
            ({"direction": "northbound"}, 1, pytest.approx(600)),
            ({"direction": "southbound"}, 2, pytest.approx(120)),
        ]

    def test_by_branch_uses_where_the_train_is_heading(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(delay=60, destination=SAINT_REMY),
            departure(delay=120, destination=ROBINSON),
            departure(delay=300, destination=ROBINSON),
            departure(delay=30, direction=Direction.NORTHBOUND, destination=CDG_2),
            # A train that ends at Gare du Nord is heading to the trunk.
            departure(delay=0, direction=Direction.NORTHBOUND, destination=GARE_DU_NORD),
            departure(delay=0, destination=None),
        )

        rows = punctuality(session, since=DAY[0], until=DAY[1], group_by=[Dimension.BRANCH])

        by_branch: Any = {r.group["branch"]: (r.trains, r.avg_delay_seconds) for r in rows}
        assert by_branch == {
            "cdg": (1, pytest.approx(30)),
            "robinson": (2, pytest.approx(210)),
            "saint_remy": (1, pytest.approx(60)),
            "trunk": (1, pytest.approx(0)),
            None: (1, pytest.approx(0)),
        }

    def test_by_station(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(delay=60, station=DENFERT),
            departure(delay=120, station=CITE_UNIVERSITAIRE),
            departure(outcome=Outcome.CANCELLED, station=CITE_UNIVERSITAIRE),
        )

        rows = punctuality(session, since=DAY[0], until=DAY[1], group_by=[Dimension.STATION])

        assert {r.group["station"]: (r.trains, r.cancelled) for r in rows} == {
            DENFERT: (1, 0),
            CITE_UNIVERSITAIRE: (1, 1),
        }

    def test_hours_are_paris_time_in_winter_and_summer(self, sessions: Sessions) -> None:
        winter = datetime(2026, 11, 16, 7, 30, tzinfo=UTC)  # 08:30 in Paris
        summer = datetime(2026, 7, 1, 7, 30, tzinfo=UTC)  # 09:30 in Paris
        session = store(sessions, departure(aimed=winter), departure(aimed=summer))

        rows = punctuality(
            session,
            since=datetime(2026, 1, 1, tzinfo=UTC),
            until=datetime(2027, 1, 1, tzinfo=UTC),
            group_by=[Dimension.SERVICE_DATE, Dimension.HOUR],
        )

        assert [r.group for r in rows] == [
            {"service_date": "2026-07-01", "hour": 9},
            {"service_date": "2026-11-16", "hour": 8},
        ]

    def test_a_train_after_midnight_belongs_to_the_previous_service_day(
        self, sessions: Sessions
    ) -> None:
        monday_1am = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)  # 01:00 Monday in Paris
        monday_3am = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)  # 03:00 Monday in Paris
        session = store(sessions, departure(aimed=monday_1am), departure(aimed=monday_3am))

        rows = punctuality(
            session,
            since=DAY[0] - timedelta(days=1),
            until=DAY[1],
            group_by=[Dimension.SERVICE_DATE, Dimension.DAY_TYPE, Dimension.HOUR],
        )

        assert [r.group for r in rows] == [
            # 01:00 on Monday is still Sunday's service: a weekend train.
            {"service_date": "2026-11-15", "day_type": "weekend", "hour": 1},
            {"service_date": "2026-11-16", "day_type": "weekday", "hour": 3},
        ]

    def test_weekdays_and_weekends(self, sessions: Sessions) -> None:
        saturday = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
        sunday = datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
        session = store(
            sessions,
            departure(delay=60),
            departure(aimed=saturday, delay=120),
            departure(aimed=sunday, delay=240),
        )

        rows = punctuality(
            session,
            since=DAY[0],
            until=DAY[0] + timedelta(days=7),
            group_by=[Dimension.DAY_TYPE],
        )

        by_day_type: Any = [(r.group["day_type"], r.trains, r.avg_delay_seconds) for r in rows]
        assert by_day_type == [
            ("weekday", 1, pytest.approx(60)),
            ("weekend", 2, pytest.approx(180)),
        ]

    def test_filters(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(delay=10, station=DENFERT, destination=ROBINSON),
            departure(delay=20, station=CITE_UNIVERSITAIRE, destination=ROBINSON),
            departure(delay=40, station=CITE_UNIVERSITAIRE, destination=SAINT_REMY),
            departure(
                delay=80,
                station=CITE_UNIVERSITAIRE,
                direction=Direction.NORTHBOUND,
                destination=CDG_2,
            ),
        )

        def average(**filters: object) -> float | None:
            (row,) = punctuality(session, since=DAY[0], until=DAY[1], **filters)  # type: ignore[arg-type]
            return row.avg_delay_seconds

        assert average(station_ref=DENFERT) == pytest.approx(10)
        assert average(direction=Direction.NORTHBOUND) == pytest.approx(80)
        assert average(branch="robinson") == pytest.approx(15)
        assert average(station_ref=CITE_UNIVERSITAIRE, branch="saint_remy") == pytest.approx(40)


class TestSegments:
    def train(self, journey: str, *delays: float, direction: Direction) -> list[Departure]:
        """A train calling at Denfert, Cité Universitaire and Gentilly, 3 minutes apart."""
        stations = [DENFERT, CITE_UNIVERSITAIRE, GENTILLY]
        if direction is Direction.NORTHBOUND:
            stations.reverse()
        return [
            departure(
                journey=journey,
                station=station,
                aimed=MONDAY_8AM + timedelta(minutes=3 * index),
                delay=delay,
                direction=direction,
            )
            for index, (station, delay) in enumerate(zip(stations, delays, strict=True))
        ]

    def test_average_delay_gained_between_consecutive_stops(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            *self.train("a", 0, 60, 60, direction=Direction.SOUTHBOUND),
            *self.train("b", 0, 120, 60, direction=Direction.SOUTHBOUND),
        )

        rows = segments(session, since=DAY[0], until=DAY[1], direction=Direction.SOUTHBOUND)

        # Denfert to Cité Universitaire: +60 and +120. Then to Gentilly: 0 and -60.
        found: Any = [
            (r.from_station, r.to_station, r.trains, r.avg_delay_gained_seconds) for r in rows
        ]
        assert found == [
            (DENFERT, CITE_UNIVERSITAIRE, 2, pytest.approx(90)),
            (CITE_UNIVERSITAIRE, GENTILLY, 2, pytest.approx(-30)),
        ]

    def test_only_the_requested_direction(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            *self.train("a", 0, 60, 60, direction=Direction.SOUTHBOUND),
            *self.train("b", 0, 300, 300, direction=Direction.NORTHBOUND),
        )

        rows = segments(session, since=DAY[0], until=DAY[1], direction=Direction.NORTHBOUND)

        assert [(r.from_station, r.to_station) for r in rows] == [
            (GENTILLY, CITE_UNIVERSITAIRE),
            (CITE_UNIVERSITAIRE, DENFERT),
        ]

    def test_a_skipped_stop_joins_its_neighbours(self, sessions: Sessions) -> None:
        calls = self.train("a", 0, 0, 90, direction=Direction.SOUTHBOUND)
        calls[1].outcome = Outcome.CANCELLED
        session = store(sessions, *calls)

        rows = segments(session, since=DAY[0], until=DAY[1], direction=Direction.SOUTHBOUND)

        found: Any = [(r.from_station, r.to_station, r.avg_delay_gained_seconds) for r in rows]
        assert found == [(DENFERT, GENTILLY, pytest.approx(90))]

    def test_a_single_stop_gives_no_segment(self, sessions: Sessions) -> None:
        session = store(sessions, departure(journey="a"))

        assert segments(session, since=DAY[0], until=DAY[1], direction=Direction.SOUTHBOUND) == []


class TestWaitingTime:
    def at(self, minute: float, **changes: object) -> Departure:
        return departure(aimed=MONDAY_8AM + timedelta(minutes=minute), **changes)  # type: ignore[arg-type]

    def wait(self, session: Session, direction: Direction = Direction.SOUTHBOUND) -> list[object]:
        return [
            (r.hour, r.trains, r.avg_wait_seconds, r.avg_gap_seconds)
            for r in waiting_time(
                session,
                since=DAY[0],
                until=DAY[1],
                station_ref=CITE_UNIVERSITAIRE,
                direction=direction,
            )
        ]

    def test_long_gaps_weigh_more_than_their_share(self, sessions: Sessions) -> None:
        # Trains at 08:00, 08:10 and 08:30: gaps of 10 and 20 minutes.
        session = store(sessions, self.at(0), self.at(10), self.at(30))

        # (600² + 1200²) / (2 × 1800) = 500 seconds, more than half the average gap (450).
        assert self.wait(session) == [(8, 2, pytest.approx(500), pytest.approx(900))]

    def test_regular_trains_give_half_the_gap(self, sessions: Sessions) -> None:
        session = store(sessions, self.at(0), self.at(10), self.at(20), self.at(30))

        assert self.wait(session) == [(8, 3, pytest.approx(300), pytest.approx(600))]

    def test_uses_actual_times_not_the_schedule(self, sessions: Sessions) -> None:
        # Scheduled every 10 minutes; the second train runs 5 minutes late.
        session = store(sessions, self.at(0), self.at(10, delay=300), self.at(20))

        # Gaps of 15 and 5 minutes: (900² + 300²) / (2 × 1200) = 375 seconds.
        assert self.wait(session) == [(8, 2, pytest.approx(375), pytest.approx(600))]

    def test_a_gap_belongs_to_the_hour_it_starts_in(self, sessions: Sessions) -> None:
        session = store(sessions, self.at(50), self.at(70), self.at(80))

        assert self.wait(session) == [
            (8, 1, pytest.approx(600), pytest.approx(1200)),
            (9, 1, pytest.approx(300), pytest.approx(600)),
        ]

    def test_a_gap_of_more_than_an_hour_is_ignored(self, sessions: Sessions) -> None:
        session = store(sessions, self.at(0), self.at(10), self.at(200), self.at(210))

        assert self.wait(session) == [
            (8, 1, pytest.approx(300), pytest.approx(600)),
            (11, 1, pytest.approx(300), pytest.approx(600)),
        ]

    def test_other_stations_directions_and_cancelled_trains_are_ignored(
        self, sessions: Sessions
    ) -> None:
        session = store(
            sessions,
            self.at(0),
            self.at(20),
            self.at(5, station=DENFERT),
            self.at(10, direction=Direction.NORTHBOUND, destination=CDG_2),
            self.at(15, outcome=Outcome.CANCELLED),
        )

        assert self.wait(session) == [(8, 1, pytest.approx(600), pytest.approx(1200))]
        assert self.wait(session, Direction.NORTHBOUND) == []

    def test_the_last_train_of_a_day_is_not_linked_to_the_first_of_the_next(
        self, sessions: Sessions
    ) -> None:
        # 02:50 and 03:10 in Paris: 20 minutes apart, but on two service days.
        before = datetime(2026, 11, 16, 1, 50, tzinfo=UTC)
        after = datetime(2026, 11, 16, 2, 10, tzinfo=UTC)
        session = store(sessions, departure(aimed=before), departure(aimed=after))

        assert self.wait(session) == []


class TestMeasurementError:
    def test_compares_the_two_feeds_at_one_station(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(global_delay=60, unitary_delay=60),
            departure(global_delay=60, unitary_delay=90),
            departure(global_delay=120, unitary_delay=30),
        )

        error = measurement_error(
            session, since=DAY[0], until=DAY[1], station_ref=CITE_UNIVERSITAIRE
        )

        assert error.departures == 3
        # Differences of 0, 30 and 90 seconds.
        assert error.mean_abs_seconds == pytest.approx(40)
        assert error.p90_abs_seconds == pytest.approx(78)
        assert error.identical_share == pytest.approx(1 / 3)

    def test_needs_both_feeds_and_a_train_that_ran(self, sessions: Sessions) -> None:
        session = store(
            sessions,
            departure(global_delay=60, unitary_delay=None),
            departure(global_delay=None, unitary_delay=60),
            departure(global_delay=0, unitary_delay=600, outcome=Outcome.CANCELLED),
            departure(global_delay=0, unitary_delay=600, station=DENFERT),
        )

        error = measurement_error(
            session, since=DAY[0], until=DAY[1], station_ref=CITE_UNIVERSITAIRE
        )

        assert error.departures == 0
        assert error.mean_abs_seconds is None


class TestDerivedFields:
    def test_the_sql_backfill_agrees_with_the_python_computation(self, sessions: Sessions) -> None:
        cases = [
            departure(delay=0),
            departure(delay=-64),
            departure(delay=923, station=DENFERT),
            departure(delay=240, terminus=True, station=SAINT_REMY),
            departure(outcome=Outcome.CANCELLED),
            # Summer time, and a train after midnight.
            departure(aimed=datetime(2026, 7, 1, 7, 30, tzinfo=UTC), delay=30),
            departure(aimed=datetime(2026, 11, 16, 0, 0, tzinfo=UTC), delay=30),
            # The night the clocks go back: 02:30 happens twice in Paris.
            departure(aimed=datetime(2026, 10, 25, 0, 30, tzinfo=UTC), delay=30),
            departure(aimed=datetime(2026, 10, 25, 1, 30, tzinfo=UTC), delay=30),
            departure(delay=0, seen_until=MONDAY_8AM - timedelta(minutes=30)),
        ]
        fields = ("delay_seconds", "service_date", "local_hour", "reliable")
        expected = {row.journey_ref: [getattr(row, name) for name in fields] for row in cases}
        for row in cases:
            for name in fields:
                setattr(row, name, None)
        session = store(sessions, *cases)

        session.execute(text(DERIVE_SQL))
        session.commit()

        session.expire_all()
        stored = {
            row.journey_ref: [getattr(row, name) for name in fields]
            for row in session.scalars(select(Departure))
        }
        assert stored == expected
