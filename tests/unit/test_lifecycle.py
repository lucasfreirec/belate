from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import Departure, Outcome
from app.ingest.lifecycle import decide_outcome, merge
from app.prim.models import CallStatus, DepartureObservation, Direction

AIMED = datetime(2026, 11, 16, 8, 0, 0, tzinfo=UTC)
T0 = datetime(2026, 11, 16, 7, 30, 0, tzinfo=UTC)


def minutes(count: float) -> timedelta:
    return timedelta(minutes=count)


def observation(**changes: object) -> DepartureObservation:
    """A train due at 08:00 at Cité Universitaire, predicted on time."""
    base = DepartureObservation(
        journey_ref="train-1",
        station_ref="STIF:StopArea:SP:473843:",
        direction=Direction.SOUTHBOUND,
        mission="KALI",
        train_number="KALI48",
        origin_ref="STIF:StopArea:SP:473364:",
        destination_ref="STIF:StopArea:SP:58774:",
        aimed_departure=AIMED,
        expected_departure=AIMED,
        aimed_arrival=AIMED - minutes(1),
        expected_arrival=AIMED - minutes(1),
        departure_status=CallStatus.ON_TIME,
        arrival_status=CallStatus.ON_TIME,
        platform="1",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def seen(*steps: tuple[datetime, DepartureObservation]) -> Departure:
    """Merge a sequence of (seen_at, observation) and return the resulting row."""
    departure: Departure | None = None
    for seen_at, item in steps:
        departure = merge(departure, item, seen_at)
    assert departure is not None
    return departure


class TestMerge:
    def test_first_observation_creates_an_open_departure(self) -> None:
        departure = merge(None, observation(expected_departure=AIMED + minutes(2)), T0)

        assert departure.journey_ref == "train-1"
        assert departure.station_ref == "STIF:StopArea:SP:473843:"
        assert departure.direction == Direction.SOUTHBOUND
        assert departure.mission == "KALI"
        assert departure.train_number == "KALI48"
        assert departure.origin_ref == "STIF:StopArea:SP:473364:"
        assert departure.destination_ref == "STIF:StopArea:SP:58774:"
        assert departure.platform == "1"
        assert departure.aimed_departure == AIMED
        assert departure.aimed_arrival == AIMED - minutes(1)
        assert departure.first_expected_departure == AIMED + minutes(2)
        assert departure.last_expected_departure == AIMED + minutes(2)
        assert departure.last_expected_arrival == AIMED - minutes(1)
        assert departure.last_status == CallStatus.ON_TIME
        assert departure.first_seen_at == T0
        assert departure.last_seen_at == T0
        assert departure.outcome == Outcome.OPEN
        assert departure.closed_at is None

    def test_a_later_observation_updates_the_same_row(self) -> None:
        first = merge(None, observation(), T0)

        second = merge(first, observation(expected_departure=AIMED + minutes(4)), T0 + minutes(2))

        assert second is first

    def test_keeps_the_first_prediction_and_updates_the_last(self) -> None:
        departure = seen(
            (T0, observation(expected_departure=AIMED + minutes(1))),
            (T0 + minutes(2), observation(expected_departure=AIMED + minutes(3))),
            (T0 + minutes(4), observation(expected_departure=AIMED + minutes(6))),
        )

        assert departure.first_expected_departure == AIMED + minutes(1)
        assert departure.last_expected_departure == AIMED + minutes(6)
        assert departure.first_seen_at == T0
        assert departure.last_seen_at == T0 + minutes(4)
        assert departure.delay_seconds == 360

    def test_updates_status_platform_and_arrival(self) -> None:
        departure = seen(
            (T0, observation()),
            (
                T0 + minutes(2),
                observation(
                    departure_status=CallStatus.DELAYED,
                    platform="2",
                    expected_arrival=AIMED + minutes(2),
                ),
            ),
        )

        assert departure.last_status == CallStatus.DELAYED
        assert departure.platform == "2"
        assert departure.last_expected_arrival == AIMED + minutes(2)

    def test_a_missing_expected_time_keeps_the_last_known_one(self) -> None:
        # Cancelled calls often come without an expected time.
        departure = seen(
            (T0, observation(expected_departure=AIMED + minutes(3))),
            (
                T0 + minutes(2),
                observation(
                    expected_departure=None,
                    expected_arrival=None,
                    departure_status=CallStatus.CANCELLED,
                ),
            ),
        )

        assert departure.last_expected_departure == AIMED + minutes(3)
        assert departure.last_expected_arrival == AIMED - minutes(1)
        assert departure.last_status == CallStatus.CANCELLED

    def test_a_missing_platform_keeps_the_last_known_one(self) -> None:
        departure = seen((T0, observation()), (T0 + minutes(2), observation(platform=None)))

        assert departure.platform == "1"

    def test_an_older_observation_changes_nothing(self) -> None:
        # A unitary response from 08:02, then a global response produced at 08:01.
        departure = seen(
            (T0 + minutes(2), observation(expected_departure=AIMED + minutes(5))),
            (
                T0 + minutes(1),
                observation(expected_departure=AIMED, departure_status=CallStatus.CANCELLED),
            ),
        )

        assert departure.last_expected_departure == AIMED + minutes(5)
        assert departure.last_status == CallStatus.ON_TIME
        assert departure.last_seen_at == T0 + minutes(2)

    def test_the_same_response_seen_twice_changes_nothing(self) -> None:
        departure = seen((T0, observation()), (T0, observation()))

        assert departure.first_seen_at == T0
        assert departure.last_seen_at == T0

    def test_a_prediction_appearing_later_becomes_the_first_one(self) -> None:
        departure = seen(
            (T0, observation(expected_departure=None)),
            (T0 + minutes(2), observation(expected_departure=AIMED + minutes(1))),
        )

        assert departure.first_expected_departure == AIMED + minutes(1)
        assert departure.last_expected_departure == AIMED + minutes(1)

    def test_a_terminus_has_no_departure_times(self) -> None:
        departure = merge(None, observation(aimed_departure=None, expected_departure=None), T0)

        assert departure.aimed_departure is None
        assert departure.first_expected_departure is None
        assert departure.last_expected_departure is None
        assert departure.aimed_time == AIMED - minutes(1)
        assert departure.actual_time == AIMED - minutes(1)

    def test_a_cancelled_train_can_be_reinstated(self) -> None:
        departure = seen(
            (T0, observation(departure_status=CallStatus.CANCELLED)),
            (T0 + minutes(2), observation(departure_status=CallStatus.ON_TIME)),
        )

        assert departure.last_status == CallStatus.ON_TIME

    def test_an_unknown_departure_seen_again_is_reopened(self) -> None:
        departure = merge(None, observation(), T0)
        departure.outcome = Outcome.UNKNOWN
        departure.closed_at = T0 + minutes(2)

        merge(departure, observation(), T0 + minutes(4))

        assert departure.outcome == Outcome.OPEN
        assert departure.closed_at is None

    @pytest.mark.parametrize("outcome", [Outcome.DEPARTED, Outcome.CANCELLED])
    def test_a_finished_departure_seen_again_stays_closed(self, outcome: Outcome) -> None:
        departure = merge(None, observation(), T0)
        departure.outcome = outcome
        departure.closed_at = T0 + minutes(2)

        merge(departure, observation(), T0 + minutes(4))

        assert departure.outcome == outcome
        assert departure.closed_at == T0 + minutes(2)


class TestDecideOutcome:
    """The train is due at 08:00; each test picks a "now" and whether it is still listed."""

    def departure(self, **changes: object) -> Departure:
        return merge(None, observation(**changes), T0)

    def test_listed_and_still_to_come_is_open(self) -> None:
        outcome = decide_outcome(
            self.departure(), now=AIMED - minutes(10), in_latest_global_feed=True
        )

        assert outcome is Outcome.OPEN

    def test_listed_shortly_after_its_time_is_open(self) -> None:
        # Calls stay in the feed about 100 seconds after their expected time.
        outcome = decide_outcome(
            self.departure(), now=AIMED + minutes(2), in_latest_global_feed=True
        )

        assert outcome is Outcome.OPEN

    def test_gone_after_its_time_has_departed(self) -> None:
        outcome = decide_outcome(
            self.departure(), now=AIMED + minutes(2), in_latest_global_feed=False
        )

        assert outcome is Outcome.DEPARTED

    def test_gone_just_before_its_time_has_departed(self) -> None:
        # Some calls leave the feed about 2 minutes early.
        outcome = decide_outcome(
            self.departure(), now=AIMED - minutes(2), in_latest_global_feed=False
        )

        assert outcome is Outcome.DEPARTED

    def test_gone_exactly_five_minutes_early_has_departed(self) -> None:
        outcome = decide_outcome(
            self.departure(), now=AIMED - minutes(5), in_latest_global_feed=False
        )

        assert outcome is Outcome.DEPARTED

    def test_gone_long_before_its_time_is_unknown(self) -> None:
        outcome = decide_outcome(
            self.departure(), now=AIMED - minutes(30), in_latest_global_feed=False
        )

        assert outcome is Outcome.UNKNOWN

    def test_lingering_twenty_minutes_after_its_time_has_departed(self) -> None:
        outcome = decide_outcome(
            self.departure(), now=AIMED + minutes(21), in_latest_global_feed=True
        )

        assert outcome is Outcome.DEPARTED

    def test_lingering_exactly_twenty_minutes_is_still_open(self) -> None:
        outcome = decide_outcome(
            self.departure(), now=AIMED + minutes(20), in_latest_global_feed=True
        )

        assert outcome is Outcome.OPEN

    def test_the_last_prediction_counts_not_the_schedule(self) -> None:
        # Due at 08:00, predicted at 08:30. At 08:10 it has not left: gone means unknown.
        late = self.departure(expected_departure=AIMED + minutes(30))

        outcome = decide_outcome(late, now=AIMED + minutes(10), in_latest_global_feed=False)

        assert outcome is Outcome.UNKNOWN

    def test_cancelled_and_gone_is_cancelled(self) -> None:
        cancelled = self.departure(departure_status=CallStatus.CANCELLED)

        outcome = decide_outcome(cancelled, now=AIMED + minutes(2), in_latest_global_feed=False)

        assert outcome is Outcome.CANCELLED

    def test_cancelled_and_gone_early_is_cancelled_not_unknown(self) -> None:
        cancelled = self.departure(departure_status=CallStatus.CANCELLED)

        outcome = decide_outcome(cancelled, now=AIMED - minutes(30), in_latest_global_feed=False)

        assert outcome is Outcome.CANCELLED

    def test_cancelled_but_still_listed_and_to_come_is_open(self) -> None:
        # It may be reinstated while it is still in the feed.
        cancelled = self.departure(departure_status=CallStatus.CANCELLED)

        outcome = decide_outcome(cancelled, now=AIMED - minutes(10), in_latest_global_feed=True)

        assert outcome is Outcome.OPEN

    def test_cancelled_and_lingering_is_cancelled(self) -> None:
        cancelled = self.departure(departure_status=CallStatus.CANCELLED)

        outcome = decide_outcome(cancelled, now=AIMED + minutes(21), in_latest_global_feed=True)

        assert outcome is Outcome.CANCELLED

    def test_without_any_prediction_the_schedule_is_used(self) -> None:
        unpredicted = self.departure(expected_departure=None, expected_arrival=None)

        assert (
            decide_outcome(unpredicted, now=AIMED + minutes(2), in_latest_global_feed=False)
            is Outcome.DEPARTED
        )
        assert (
            decide_outcome(unpredicted, now=AIMED - minutes(30), in_latest_global_feed=False)
            is Outcome.UNKNOWN
        )

    def test_a_terminus_uses_its_arrival_time(self) -> None:
        terminus = self.departure(aimed_departure=None, expected_departure=None)

        outcome = decide_outcome(terminus, now=AIMED + minutes(2), in_latest_global_feed=False)

        assert outcome is Outcome.DEPARTED
