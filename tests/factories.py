"""Builders for test data shared by several test modules."""

from datetime import UTC, datetime, timedelta

from app.analytics.derive import derive
from app.db.models import Departure, Outcome
from app.prim.models import CallStatus, Direction

CITE_UNIVERSITAIRE = "STIF:StopArea:SP:473843:"
DENFERT = "STIF:StopArea:SP:473890:"
GENTILLY = "STIF:StopArea:SP:45877:"
GARE_DU_NORD = "STIF:StopArea:SP:462394:"
ROBINSON = "STIF:StopArea:SP:43186:"
SAINT_REMY = "STIF:StopArea:SP:47889:"
CDG_2 = "STIF:StopArea:SP:473364:"

# Monday 16 November 2026, 08:00 in Paris (winter time, UTC+1).
MONDAY_8AM = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
DAY = (datetime(2026, 11, 16, tzinfo=UTC), datetime(2026, 11, 17, tzinfo=UTC))
_counter = iter(range(1, 10_000))


def departure(
    *,
    aimed: datetime = MONDAY_8AM,
    delay: float = 0,
    outcome: Outcome = Outcome.DEPARTED,
    station: str = CITE_UNIVERSITAIRE,
    direction: Direction = Direction.SOUTHBOUND,
    destination: str | None = SAINT_REMY,
    journey: str | None = None,
    terminus: bool = False,
    seen_until: datetime | None = None,
    global_delay: float | None = None,
    unitary_delay: float | None = None,
) -> Departure:
    """A departure scheduled at `aimed` that left `delay` seconds late."""
    actual = aimed + timedelta(seconds=delay)
    row = Departure(
        journey_ref=journey or f"train-{next(_counter)}",
        station_ref=station,
        direction=direction,
        destination_ref=destination,
        aimed_departure=None if terminus else aimed,
        aimed_arrival=aimed if terminus else None,
        aimed_time=aimed,
        first_expected_departure=None if terminus else actual,
        last_expected_departure=None if terminus else actual,
        last_expected_arrival=actual if terminus else None,
        global_expected_departure=None
        if global_delay is None
        else aimed + timedelta(seconds=global_delay),
        unitary_expected_departure=None
        if unitary_delay is None
        else aimed + timedelta(seconds=unitary_delay),
        last_status=CallStatus.CANCELLED if outcome is Outcome.CANCELLED else CallStatus.ON_TIME,
        first_seen_at=aimed - timedelta(hours=1),
        # By default the departure was still listed one minute after it left.
        last_seen_at=seen_until or actual + timedelta(minutes=1),
        outcome=outcome,
        closed_at=actual + timedelta(minutes=2),
    )
    derive(row)
    return row
