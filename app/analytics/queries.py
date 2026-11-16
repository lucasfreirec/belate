"""Reliability metrics computed from the stored departures. Definitions: docs/data.md.

Every query works on a window of scheduled times, `since <= aimed_time < until`, and only on
departures that are over. Hours and days are Paris local time; a train scheduled before
03:00 belongs to the previous service day.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Float, Integer, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.db.models import Departure, Outcome, StationRecord
from app.prim.models import Direction

LATE_AFTER_SECONDS = 300
# Gaps longer than this are a break in service or in collection, not a waiting time.
MAX_GAP_SECONDS = 3600


class Dimension(StrEnum):
    """What punctuality can be broken down by."""

    BRANCH = "branch"  # part of the line the train is heading to
    DIRECTION = "direction"
    STATION = "station"
    DAY_TYPE = "day_type"  # weekday or weekend
    HOUR = "hour"  # 0 to 23, Paris time
    SERVICE_DATE = "service_date"


@dataclass(frozen=True, slots=True)
class PunctualityRow:
    group: dict[str, Any]
    trains: int  # departures that ran
    cancelled: int
    cancellation_rate: float | None  # cancelled / (ran + cancelled)
    avg_delay_seconds: float | None
    p90_delay_seconds: float | None
    late_share: float | None  # share of departures that ran more than 5 minutes late


@dataclass(frozen=True, slots=True)
class SegmentRow:
    from_station: str
    to_station: str
    trains: int
    # Average change in delay between the two stations. Positive: time is lost.
    avg_delay_gained_seconds: float


@dataclass(frozen=True, slots=True)
class WaitingTimeRow:
    hour: int
    trains: int
    avg_wait_seconds: float  # for a passenger arriving at a random moment
    avg_gap_seconds: float  # plain average time between two trains


@dataclass(frozen=True, slots=True)
class MeasurementError:
    departures: int
    mean_abs_seconds: float | None
    p90_abs_seconds: float | None
    identical_share: float | None


_ACTUAL = func.coalesce(Departure.last_expected_departure, Departure.last_expected_arrival)
# The next four are stored on the row when the departure is closed (app.analytics.derive).
_DELAY = Departure.delay_seconds
_SERVICE_DATE = Departure.service_date
_RELIABLE = Departure.reliable.is_(True)
_RAN = Departure.outcome == Outcome.DEPARTED
_CANCELLED = Departure.outcome == Outcome.CANCELLED


def _window(since: datetime, until: datetime) -> ColumnElement[bool]:
    return (Departure.aimed_time >= since) & (Departure.aimed_time < until)


def punctuality(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    group_by: Sequence[Dimension] = (),
    direction: Direction | None = None,
    station_ref: str | None = None,
    branch: str | None = None,
) -> list[PunctualityRow]:
    """Delay and cancellation figures, broken down by the given dimensions."""
    destination = aliased(StationRecord)
    columns: dict[Dimension, Any] = {
        Dimension.BRANCH: destination.section,
        Dimension.DIRECTION: Departure.direction,
        Dimension.STATION: Departure.station_ref,
        Dimension.DAY_TYPE: case(
            (func.extract("isodow", _SERVICE_DATE) >= 6, literal("weekend")),
            else_=literal("weekday"),
        ),
        Dimension.HOUR: Departure.local_hour,
        Dimension.SERVICE_DATE: _SERVICE_DATE,
    }
    groups = [columns[dimension].label(dimension.value) for dimension in group_by]

    late = case((_DELAY > LATE_AFTER_SECONDS, 1.0), else_=0.0)
    query = (
        select(
            *groups,
            func.count().filter(_RAN).label("trains"),
            func.count().filter(_CANCELLED).label("cancelled"),
            func.avg(_DELAY).filter(_RAN).label("avg_delay"),
            func.percentile_cont(0.9).within_group(_DELAY).filter(_RAN).label("p90_delay"),
            func.avg(late).filter(_RAN).label("late_share"),
        )
        .select_from(Departure)
        .where(_window(since, until), or_(_CANCELLED, _RAN & _RELIABLE))
        .group_by(*groups)
        .order_by(*groups)
    )
    # The branch is the section of the destination station; join only when it is needed.
    if branch is not None or Dimension.BRANCH in group_by:
        query = query.outerjoin(destination, destination.ref == Departure.destination_ref)
    if direction is not None:
        query = query.where(Departure.direction == direction)
    if station_ref is not None:
        query = query.where(Departure.station_ref == station_ref)
    if branch is not None:
        query = query.where(destination.section == branch)

    rows = []
    for row in session.execute(query).mappings():
        total = row["trains"] + row["cancelled"]
        rows.append(
            PunctualityRow(
                group={dimension.value: _plain(row[dimension.value]) for dimension in group_by},
                trains=row["trains"],
                cancelled=row["cancelled"],
                cancellation_rate=row["cancelled"] / total if total else None,
                avg_delay_seconds=_float(row["avg_delay"]),
                p90_delay_seconds=_float(row["p90_delay"]),
                late_share=_float(row["late_share"]),
            )
        )
    return rows


def segments(
    session: Session, *, since: datetime, until: datetime, direction: Direction
) -> list[SegmentRow]:
    """Where trains lose or recover time: the change in delay between consecutive stops.

    A train is followed from station to station by its journey reference. Consecutive means
    consecutive stops of that train, so a semi-direct train gives longer segments.
    """
    calls = (
        select(
            Departure.station_ref.label("station"),
            _DELAY.label("delay"),
            func.lag(Departure.station_ref)
            .over(partition_by=Departure.journey_ref, order_by=Departure.aimed_time)
            .label("previous_station"),
            func.lag(_DELAY)
            .over(partition_by=Departure.journey_ref, order_by=Departure.aimed_time)
            .label("previous_delay"),
        )
        .where(_window(since, until), _RAN, _RELIABLE, Departure.direction == direction)
        .subquery()
    )
    query = (
        select(
            calls.c.previous_station,
            calls.c.station,
            func.count().label("trains"),
            func.avg(calls.c.delay - calls.c.previous_delay).label("gained"),
        )
        .where(calls.c.previous_station.is_not(None))
        .group_by(calls.c.previous_station, calls.c.station)
        .order_by(func.avg(calls.c.delay - calls.c.previous_delay).desc())
    )
    return [
        SegmentRow(previous, station, trains, float(gained))
        for previous, station, trains, gained in session.execute(query)
    ]


def waiting_time(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    station_ref: str,
    direction: Direction,
) -> list[WaitingTimeRow]:
    """Average wait at a station by hour, for a passenger arriving at a random moment.

    With gaps h between consecutive trains, that wait is sum(h²) / (2 · sum(h)): long gaps
    count more, because more passengers arrive during them. Each gap is attributed to the
    hour in which it starts.
    """
    previous = func.lag(_ACTUAL).over(partition_by=_SERVICE_DATE, order_by=_ACTUAL)
    trains = (
        select(
            cast(func.extract("epoch", _ACTUAL - previous), Float).label("gap"),
            cast(func.extract("hour", func.timezone("Europe/Paris", previous)), Integer).label(
                "hour"
            ),
        )
        .where(
            _window(since, until),
            _RAN,
            _RELIABLE,
            Departure.station_ref == station_ref,
            Departure.direction == direction,
        )
        .subquery()
    )
    gap = trains.c.gap
    query = (
        select(
            trains.c.hour,
            func.count(),
            func.sum(gap * gap) / (2 * func.sum(gap)),
            func.avg(gap),
        )
        .where(gap > 0, gap <= MAX_GAP_SECONDS)
        .group_by(trains.c.hour)
        .order_by(trains.c.hour)
    )
    return [
        WaitingTimeRow(hour, count, float(wait), float(average))
        for hour, count, wait, average in session.execute(query)
    ]


def measurement_error(
    session: Session, *, since: datetime, until: datetime, station_ref: str
) -> MeasurementError:
    """How far the global feed's last prediction is from the unitary feed's, at one station.

    The unitary feed is polled every minute and is at most about 20 seconds old, so it is the
    reference. Only departures seen in both feeds are compared.
    """
    difference = func.abs(
        cast(
            func.extract(
                "epoch",
                Departure.global_expected_departure - Departure.unitary_expected_departure,
            ),
            Float,
        )
    )
    query = select(
        func.count(),
        func.avg(difference),
        func.percentile_cont(0.9).within_group(difference),
        func.avg(case((difference == 0, 1.0), else_=0.0)),
    ).where(
        _window(since, until),
        _RAN,
        Departure.station_ref == station_ref,
        Departure.global_expected_departure.is_not(None),
        Departure.unitary_expected_departure.is_not(None),
    )
    count, mean, p90, identical = session.execute(query).one()
    return MeasurementError(count, _float(mean), _float(p90), _float(identical))


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _plain(value: Any) -> Any:
    """Turn database values into plain JSON-friendly ones."""
    return value.isoformat() if isinstance(value, date) else value
