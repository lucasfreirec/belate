"""What counts as an incident for a subscription, right now.

Three kinds, each with a fixed starting threshold to tune on real data:

- disruption: PRIM publishes an incident or works that apply now to the line or the station
- delays: trains at the station are running well above their usual delay
- cancellations: several trains at the station were cancelled
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.analytics.derive import SERVICE_DAY_STARTS_AT
from app.db.models import Departure, DisruptionRecord, Outcome, Subscription
from app.prim.models import CallStatus
from app.reference.stations import STATIONS_BY_REF

PARIS = ZoneInfo("Europe/Paris")

RECENT = timedelta(minutes=30)
# A disruption missing from the traffic info feed for this long is no longer published.
STILL_PUBLISHED = timedelta(minutes=15)
ALERTING_CAUSES = ("PERTURBATION", "TRAVAUX")

MIN_TRAINS_FOR_DELAY = 3
DELAY_FLOOR_SECONDS = 300
# The usual delay of an hour is trusted once this many days of history exist.
BASELINE_DAYS = 28
MIN_BASELINE_DAYS = 14
MIN_CANCELLATIONS = 2

_ACTUAL = func.coalesce(Departure.last_expected_departure, Departure.last_expected_arrival)


@dataclass(frozen=True, slots=True)
class Incident:
    kind: str  # disruption, delays or cancellations
    key: str  # the disruption ID, or "" for the other kinds
    message: str


def in_window(subscription: Subscription, now: datetime) -> bool:
    """Is `now` inside the days and hours the subscription watches (Paris time)?"""
    local = now.astimezone(PARIS)
    return (
        local.isoweekday() in subscription.days
        and subscription.start_time <= local.time() <= subscription.end_time
    )


def current_incidents(
    session: Session, subscription: Subscription, now: datetime
) -> list[Incident]:
    incidents = _disruptions(session, subscription, now)
    for incident in (
        _delays(session, subscription, now),
        _cancellations(session, subscription, now),
    ):
        if incident is not None:
            incidents.append(incident)
    return incidents


def _disruptions(session: Session, subscription: Subscription, now: datetime) -> list[Incident]:
    published = session.scalars(
        select(DisruptionRecord).where(
            DisruptionRecord.last_seen_at >= now - STILL_PUBLISHED,
            DisruptionRecord.cause.in_(ALERTING_CAUSES),
            DisruptionRecord.severity != "INFORMATION",
        )
    )
    incidents = []
    for disruption in published:
        concerns_station = (
            not disruption.station_refs or subscription.station_ref in disruption.station_refs
        )
        applies_now = any(
            datetime.fromisoformat(begin) <= now < datetime.fromisoformat(end)
            for begin, end in disruption.periods
        )
        if concerns_station and applies_now:
            incidents.append(Incident("disruption", disruption.id, disruption.title))
    return incidents


def _delays(session: Session, subscription: Subscription, now: datetime) -> Incident | None:
    delay = cast(func.extract("epoch", _ACTUAL - Departure.aimed_time), Float)
    trains, median = session.execute(
        select(func.count(), func.percentile_cont(0.5).within_group(delay)).where(
            Departure.station_ref == subscription.station_ref,
            Departure.direction == subscription.direction,
            Departure.last_status != CallStatus.CANCELLED,
            Departure.aimed_time.is_not(None),
            now - RECENT < _ACTUAL,
            now >= _ACTUAL,
        )
    ).one()
    if trains < MIN_TRAINS_FOR_DELAY or median is None:
        return None

    threshold = max(DELAY_FLOOR_SECONDS, _usual_p90(session, subscription, now) or 0)
    if median <= threshold:
        return None
    station = STATIONS_BY_REF[subscription.station_ref].name
    return Incident(
        "delays",
        "",
        f"Trains at {station} ({subscription.direction}) are running about "
        f"{round(median / 60)} minutes late.",
    )


def _usual_p90(session: Session, subscription: Subscription, now: datetime) -> float | None:
    """90th percentile delay at this station, direction and hour over the past weeks.

    None until enough days of history exist; the fixed floor applies alone until then.
    """
    local = now.astimezone(PARIS)
    today = (local - SERVICE_DAY_STARTS_AT).date()
    days, p90 = session.execute(
        select(
            func.count(Departure.service_date.distinct()),
            func.percentile_cont(0.9).within_group(Departure.delay_seconds),
        ).where(
            Departure.station_ref == subscription.station_ref,
            Departure.direction == subscription.direction,
            Departure.local_hour == local.hour,
            Departure.outcome == Outcome.DEPARTED,
            Departure.reliable.is_(True),
            Departure.aimed_time >= now - timedelta(days=BASELINE_DAYS),
            Departure.service_date < today,
        )
    ).one()
    return float(p90) if days >= MIN_BASELINE_DAYS and p90 is not None else None


def _cancellations(session: Session, subscription: Subscription, now: datetime) -> Incident | None:
    cancelled = session.scalar(
        select(func.count()).where(
            Departure.station_ref == subscription.station_ref,
            Departure.direction == subscription.direction,
            Departure.last_status == CallStatus.CANCELLED,
            Departure.aimed_time > now - RECENT,
            Departure.aimed_time <= now,
        )
    )
    if not cancelled or cancelled < MIN_CANCELLATIONS:
        return None
    station = STATIONS_BY_REF[subscription.station_ref].name
    return Incident(
        "cancellations",
        "",
        f"{cancelled} trains at {station} ({subscription.direction}) were cancelled "
        "in the last 30 minutes.",
    )
