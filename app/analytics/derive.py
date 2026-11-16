"""Fields computed once, when a departure is over, so that analytics queries stay cheap.

Hours and days are Paris local time. A train scheduled before 03:00 belongs to the previous
service day: the last trains of the evening run past midnight.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app.db.models import Departure

PARIS = ZoneInfo("Europe/Paris")
SERVICE_DAY_STARTS_AT = timedelta(hours=3)
# A prediction older than this when the train was due is too stale to count as the actual
# time. It happens when collection stopped for a while.
MAX_PREDICTION_AGE = timedelta(minutes=5)


def derive(departure: Departure) -> None:
    """Fill the analytics fields of a departure from its times."""
    aimed, actual = departure.aimed_time, departure.actual_time
    if aimed is None:
        departure.service_date = None
        departure.local_hour = None
        departure.delay_seconds = None
        departure.reliable = False
        return

    local = aimed.astimezone(PARIS)
    departure.service_date = (local - SERVICE_DAY_STARTS_AT).date()
    departure.local_hour = local.hour
    departure.delay_seconds = None if actual is None else round((actual - aimed).total_seconds())
    departure.reliable = (
        actual is not None and departure.last_seen_at >= actual - MAX_PREDICTION_AGE
    )


# The same computation in SQL, for rows that were closed before these fields existed and
# for bulk loads. A test checks that it agrees with `derive`.
DERIVE_SQL = """
UPDATE departures
SET service_date = (timezone('Europe/Paris', aimed_time) - interval '3 hours')::date,
    local_hour = extract(hour FROM timezone('Europe/Paris', aimed_time)),
    delay_seconds = round(extract(epoch FROM
        COALESCE(last_expected_departure, last_expected_arrival) - aimed_time)),
    reliable = COALESCE(
        last_seen_at >= COALESCE(last_expected_departure, last_expected_arrival)
            - interval '5 minutes',
        false)
WHERE outcome <> 'open' AND aimed_time IS NOT NULL
"""
