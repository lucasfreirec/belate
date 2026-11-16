"""Departure lifecycle: from repeated observations to one final row per departure.  ✍️ Lucas

The same train at the same station is seen in every poll until it leaves: about 100 times in
the global feed, plus once a minute in the unitary feed at my station. These two functions
turn that stream into one `Departure` row, and decide what finally became of it.

Both are pure: they take objects and return a result, with no database and no clock of their
own. The code that loads and saves rows calls them. The tests are in
tests/unit/test_lifecycle.py and encode the rules of docs/data.md, "Definitions".

Things worth thinking about before writing the code:

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
from app.prim.models import DepartureObservation

# A departure still listed this long after its expected time is treated as gone.
LINGER_LIMIT = timedelta(minutes=20)
# A departure that leaves the feed earlier than this before its expected time is suspicious.
EARLY_EXIT_LIMIT = timedelta(minutes=5)


def merge(
    departure: Departure | None, observation: DepartureObservation, seen_at: datetime
) -> Departure:
    """Record one observation of a departure and return its row.

    `departure` is the existing row for this (train, station), or None the first time it is
    seen. `seen_at` is when PRIM produced the response the observation comes from.

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
    raise NotImplementedError


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
    raise NotImplementedError
