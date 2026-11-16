"""Read-only endpoints, open to everyone and rate limited."""

from datetime import UTC, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.analytics import queries
from app.analytics.queries import Dimension
from app.api.deps import SessionDep, WindowDep, rate_limit
from app.api.schemas import (
    CancellationsDayOut,
    CancellationsOut,
    DisruptionOut,
    LiveDepartureOut,
    LiveOut,
    PeriodOut,
    PunctualityOut,
    PunctualityRowOut,
    SegmentOut,
    SegmentsOut,
    StationOut,
    WaitingTimeHourOut,
    WaitingTimeOut,
    WindowOut,
)
from app.db.models import Departure, DisruptionRecord, Outcome, StationRecord
from app.prim.models import CallStatus, Direction
from app.reference.stations import STATIONS_BY_REF, Section

router = APIRouter(dependencies=[Depends(rate_limit)])

# A disruption missing from the traffic info feed for this long is no longer published.
STILL_PUBLISHED = timedelta(minutes=15)
LIVE_LIMIT = 12
# Keep a train on the live board this long after its expected time.
LIVE_GRACE = timedelta(minutes=1)

StationQuery = Annotated[str, Query(description="Station reference, as listed by /stations.")]


def _known_station(station: str) -> str:
    if station not in STATIONS_BY_REF:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown station. See /stations.")
    return station


def _name(station_ref: str | None) -> str | None:
    station = STATIONS_BY_REF.get(station_ref or "")
    return station.name if station else None


@router.get("/stations")
def list_stations(session: SessionDep) -> list[StationOut]:
    """The 47 stations of the RER B, section by section, from north to south."""
    rows = session.scalars(
        select(StationRecord).order_by(StationRecord.section, StationRecord.position)
    )
    return [
        StationOut(ref=r.ref, name=r.name, section=r.section, position=r.position) for r in rows
    ]


@router.get("/punctuality")
def get_punctuality(
    session: SessionDep,
    window: WindowDep,
    group_by: Annotated[list[Dimension] | None, Query()] = None,
    direction: Direction | None = None,
    station: Annotated[str | None, Query(description="Station reference.")] = None,
    branch: Section | None = None,
) -> PunctualityOut:
    """Delay and cancellation figures, optionally broken down and filtered."""
    dimensions = list(dict.fromkeys(group_by or []))
    rows = queries.punctuality(
        session,
        since=window.since,
        until=window.until,
        group_by=dimensions,
        direction=direction,
        station_ref=_known_station(station) if station else None,
        branch=branch.value if branch else None,
    )
    return PunctualityOut(
        window=WindowOut(since=window.since, until=window.until),
        group_by=[dimension.value for dimension in dimensions],
        rows=[
            PunctualityRowOut(
                group=row.group,
                station_name=_name(row.group.get("station")),
                trains=row.trains,
                cancelled=row.cancelled,
                cancellation_rate=row.cancellation_rate,
                avg_delay_seconds=row.avg_delay_seconds,
                p90_delay_seconds=row.p90_delay_seconds,
                late_share=row.late_share,
            )
            for row in rows
        ],
    )


@router.get("/segments")
def get_segments(session: SessionDep, window: WindowDep, direction: Direction) -> SegmentsOut:
    """Where trains lose or recover time, between consecutive stops, worst first."""
    rows = queries.segments(session, since=window.since, until=window.until, direction=direction)
    return SegmentsOut(
        window=WindowOut(since=window.since, until=window.until),
        direction=direction,
        segments=[
            SegmentOut(
                from_station=row.from_station,
                from_station_name=_name(row.from_station),
                to_station=row.to_station,
                to_station_name=_name(row.to_station),
                trains=row.trains,
                avg_delay_gained_seconds=row.avg_delay_gained_seconds,
            )
            for row in rows
        ],
    )


@router.get("/cancellations")
def get_cancellations(session: SessionDep, window: WindowDep) -> CancellationsOut:
    """Cancelled departures over the window, in total and per service day."""
    (total,) = queries.punctuality(session, since=window.since, until=window.until)
    by_date = queries.punctuality(
        session, since=window.since, until=window.until, group_by=[Dimension.SERVICE_DATE]
    )
    return CancellationsOut(
        window=WindowOut(since=window.since, until=window.until),
        trains=total.trains,
        cancelled=total.cancelled,
        cancellation_rate=total.cancellation_rate,
        by_date=[
            CancellationsDayOut(
                service_date=row.group["service_date"],
                trains=row.trains,
                cancelled=row.cancelled,
                cancellation_rate=row.cancellation_rate,
            )
            for row in by_date
        ],
    )


@router.get("/waiting-time")
def get_waiting_time(
    session: SessionDep, window: WindowDep, station: StationQuery, direction: Direction
) -> WaitingTimeOut:
    """Average wait at a station by hour, for a passenger arriving at a random moment."""
    rows = queries.waiting_time(
        session,
        since=window.since,
        until=window.until,
        station_ref=_known_station(station),
        direction=direction,
    )
    return WaitingTimeOut(
        window=WindowOut(since=window.since, until=window.until),
        station=station,
        direction=direction,
        hours=[
            WaitingTimeHourOut(
                hour=row.hour,
                trains=row.trains,
                avg_wait_seconds=row.avg_wait_seconds,
                avg_gap_seconds=row.avg_gap_seconds,
            )
            for row in rows
        ],
    )


@router.get("/disruptions")
def get_disruptions(
    session: SessionDep,
    active: Annotated[
        bool, Query(description="Only disruptions that apply at this moment.")
    ] = False,
) -> list[DisruptionOut]:
    """Disruptions PRIM currently publishes for the RER B."""
    now = datetime.now(UTC)
    rows = session.scalars(
        select(DisruptionRecord)
        .where(DisruptionRecord.last_seen_at >= now - STILL_PUBLISHED)
        .order_by(DisruptionRecord.last_update.desc())
    )
    disruptions = []
    for row in rows:
        periods = [
            PeriodOut(begin=datetime.fromisoformat(begin), end=datetime.fromisoformat(end))
            for begin, end in row.periods
        ]
        is_active = any(period.begin <= now < period.end for period in periods)
        if active and not is_active:
            continue
        disruptions.append(
            DisruptionOut(
                id=row.id,
                cause=row.cause,
                severity=row.severity,
                title=row.title,
                message=html_to_text(row.message),
                last_update=row.last_update,
                active=is_active,
                periods=periods,
                station_refs=row.station_refs,
            )
        )
    return disruptions


@router.get("/live")
def get_live(
    session: SessionDep, station: StationQuery, direction: Direction | None = None
) -> LiveOut:
    """Next trains at a station, from the latest collected data."""
    _known_station(station)
    now = datetime.now(UTC)
    actual = func.coalesce(Departure.last_expected_departure, Departure.last_expected_arrival)
    query = (
        select(Departure)
        .where(
            Departure.station_ref == station,
            Departure.outcome == Outcome.OPEN,
            # A terminus has no departure: nobody boards there.
            Departure.aimed_departure.is_not(None),
            actual >= now - LIVE_GRACE,
        )
        .order_by(actual)
        .limit(LIVE_LIMIT)
    )
    if direction is not None:
        query = query.where(Departure.direction == direction)

    departures = []
    for row in session.scalars(query):
        aimed, expected = row.aimed_time, row.actual_time
        departures.append(
            LiveDepartureOut(
                train_number=row.train_number,
                mission=row.mission,
                direction=row.direction,
                destination=_name(row.destination_ref),
                platform=row.platform,
                aimed_time=aimed,
                expected_time=expected,
                delay_seconds=round((expected - aimed).total_seconds())
                if aimed and expected
                else None,
                cancelled=row.last_status == CallStatus.CANCELLED,
            )
        )
    as_of = session.scalar(select(func.max(Departure.last_seen_at)))
    return LiveOut(station=station, as_of=as_of, departures=departures)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("br", "p", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    """Strip the markup of a message published by PRIM, keeping line breaks."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    lines = (line.strip() for line in unescape("".join(extractor.parts)).splitlines())
    return "\n".join(line for line in lines if line)
