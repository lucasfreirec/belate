"""Departure lifecycle: from repeated observations to one final row per departure.

The same train at the same station is seen in every poll until it leaves: about 100 times in
the global feed, plus once a minute in the unitary feed at the chosen station. These two functions
turn that stream into one `Departure` row, and decide what finally became of it.

Both are pure: they take objects and return a result, with no database and no clock of their
own. The code that loads and saves rows calls them. The tests are in
tests/unit/test_lifecycle.py and encode the rules of docs/data.md, "Definitions".

Three facts shape the rules:

- The two feeds are not equally fresh. A global response can be 2 minutes older than a
  unitary response received just before it, so observations do not always arrive in time
  order. `seen_at` (when PRIM produced the response) says which one is newer.
- A field missing from one observation is not information. A cancelled call often has no
  expected time; that must not erase the last one we saw.
- "Absent from the feed" has three possible meanings: the train left, it was cancelled, or
  something we do not understand yet. `decide_outcome` tells them apart.
"""

from datetime import datetime, timedelta

from app.db.models import Departure, Outcome
from app.prim.models import Api, CallStatus, DepartureObservation

# A departure still listed this long after its expected time is treated as gone.
LINGER_LIMIT = timedelta(minutes=20)
# A departure that leaves the feed earlier than this before its expected time is suspicious.
EARLY_EXIT_LIMIT = timedelta(minutes=5)


def merge(
    departure: Departure | None, observation: DepartureObservation, seen_at: datetime, api: Api
) -> Departure:
    """Record one observation of a departure and return its row.

    `departure` is the existing row for this (train, station), or None the first time it is
    seen. `seen_at` is when PRIM produced the response the observation comes from, and `api`
    says which feed that was.

    - First time: create the row. The first and last expected departure are both the
      observed one, `first_seen_at` and `last_seen_at` are both `seen_at`, the outcome is
      OPEN.
    - Later: update the last expected times, the status, the platform and `last_seen_at`.
      Never change `first_expected_departure` or `first_seen_at`.
    - An expected time missing from the observation keeps the last known one.
    - An observation older than `last_seen_at` changes nothing.
    - A row closed as UNKNOWN that is observed again becomes OPEN again. Rows closed as
      DEPARTED or CANCELLED keep their outcome.
    """
    if departure is None:
        departure = Departure(
            journey_ref=observation.journey_ref,
            station_ref=observation.station_ref,
            direction=observation.direction,
            mission=observation.mission,
            train_number=observation.train_number,
            origin_ref=observation.origin_ref,
            destination_ref=observation.destination_ref,
            platform=observation.platform,
            aimed_departure=observation.aimed_departure,
            aimed_arrival=observation.aimed_arrival,
            aimed_time=observation.aimed_departure or observation.aimed_arrival,
            first_expected_departure=observation.expected_departure,
            last_expected_departure=observation.expected_departure,
            last_expected_arrival=observation.expected_arrival,
            last_status=observation.departure_status,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            outcome=Outcome.OPEN,
            closed_at=None,
        )
        _record_feed_prediction(departure, observation, api)
        return departure

    if seen_at <= departure.last_seen_at:
        return departure

    if observation.expected_departure is not None:
        if departure.first_expected_departure is None:
            departure.first_expected_departure = observation.expected_departure
        departure.last_expected_departure = observation.expected_departure
    if observation.expected_arrival is not None:
        departure.last_expected_arrival = observation.expected_arrival
    if observation.platform is not None:
        departure.platform = observation.platform
    departure.last_status = observation.departure_status
    departure.last_seen_at = seen_at
    _record_feed_prediction(departure, observation, api)

    if departure.outcome == Outcome.UNKNOWN:
        departure.outcome = Outcome.OPEN
        departure.closed_at = None
    return departure


def _record_feed_prediction(
    departure: Departure, observation: DepartureObservation, api: Api
) -> None:
    """Keep the last prediction of each feed apart, to compare them later."""
    if observation.expected_departure is None:
        return
    if api is Api.GLOBAL:
        departure.global_expected_departure = observation.expected_departure
    elif api is Api.UNITARY:
        departure.unitary_expected_departure = observation.expected_departure


def decide_outcome(departure: Departure, *, now: datetime, in_latest_global_feed: bool) -> Outcome:
    """Decide what became of an open departure. Return OPEN if it is not over yet.

    `now` is when PRIM produced the latest global response, and `in_latest_global_feed` says
    whether that response still lists this departure. "Its time" below is the last expected
    time, or the aimed time if the feed never gave a prediction.

    - Still listed, and its time is less than LINGER_LIMIT in the past: OPEN.
    - Over (no longer listed, or listed but past LINGER_LIMIT) with CANCELLED as its last
      status: CANCELLED.
    - No longer listed although its time is more than EARLY_EXIT_LIMIT in the future, and not
      cancelled: UNKNOWN.
    - Otherwise over: DEPARTED.
    """
    its_time = departure.actual_time or departure.aimed_time
    if its_time is None:
        # Nothing says when it was due, so there is nothing to wait for once it is gone.
        return Outcome.OPEN if in_latest_global_feed else Outcome.UNKNOWN

    if in_latest_global_feed and now - its_time <= LINGER_LIMIT:
        return Outcome.OPEN
    if departure.last_status == CallStatus.CANCELLED:
        return Outcome.CANCELLED
    if not in_latest_global_feed and its_time - now > EARLY_EXIT_LIMIT:
        return Outcome.UNKNOWN
    return Outcome.DEPARTED
