from datetime import UTC, date, datetime, timedelta

from app.analytics.derive import derive
from app.db.models import Departure


def closed(aimed: datetime, delay: float = 0, seen_after: float = 60) -> Departure:
    """A departure due at `aimed`, last predicted `delay` seconds late."""
    actual = aimed + timedelta(seconds=delay)
    row = Departure(
        aimed_departure=aimed,
        aimed_time=aimed,
        last_expected_departure=actual,
        last_seen_at=actual + timedelta(seconds=seen_after),
    )
    derive(row)
    return row


def test_delay_is_the_last_prediction_minus_the_schedule() -> None:
    assert closed(datetime(2026, 11, 16, 7, 0, tzinfo=UTC), delay=195).delay_seconds == 195
    assert closed(datetime(2026, 11, 16, 7, 0, tzinfo=UTC), delay=-64).delay_seconds == -64


def test_hour_and_day_are_paris_time() -> None:
    winter = closed(datetime(2026, 11, 16, 7, 30, tzinfo=UTC))
    summer = closed(datetime(2026, 7, 1, 7, 30, tzinfo=UTC))

    assert (winter.service_date, winter.local_hour) == (date(2026, 11, 16), 8)
    assert (summer.service_date, summer.local_hour) == (date(2026, 7, 1), 9)


def test_a_train_before_three_belongs_to_the_previous_service_day() -> None:
    at_0259 = closed(datetime(2026, 11, 16, 1, 59, tzinfo=UTC))
    at_0300 = closed(datetime(2026, 11, 16, 2, 0, tzinfo=UTC))

    assert (at_0259.service_date, at_0259.local_hour) == (date(2026, 11, 15), 2)
    assert (at_0300.service_date, at_0300.local_hour) == (date(2026, 11, 16), 3)


def test_a_recent_prediction_is_reliable() -> None:
    aimed = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)

    assert closed(aimed, seen_after=60).reliable is True
    assert closed(aimed, seen_after=-300).reliable is True
    assert closed(aimed, seen_after=-301).reliable is False


def test_a_terminus_uses_its_arrival() -> None:
    aimed = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    row = Departure(
        aimed_arrival=aimed,
        aimed_time=aimed,
        last_expected_arrival=aimed + timedelta(seconds=120),
        last_seen_at=aimed + timedelta(seconds=180),
    )

    derive(row)

    assert row.delay_seconds == 120
    assert row.reliable is True


def test_without_a_schedule_nothing_can_be_derived() -> None:
    row = Departure(last_seen_at=datetime(2026, 11, 16, 7, 0, tzinfo=UTC))

    derive(row)

    assert row.delay_seconds is None
    assert row.service_date is None
    assert row.local_hour is None
    assert row.reliable is False


def test_without_a_prediction_there_is_no_delay() -> None:
    aimed = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    row = Departure(aimed_departure=aimed, aimed_time=aimed, last_seen_at=aimed)

    derive(row)

    assert row.delay_seconds is None
    assert row.local_hour == 8
    assert row.reliable is False
