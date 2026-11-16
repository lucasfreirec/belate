"""Print the health of the collector: age of the latest data and quota used today.

Run with `make status`. Exits with 1 if the latest global poll is more than 10 minutes old.
"""

import sys
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.models import Departure, DisruptionRecord, Outcome
from app.db.session import new_session
from app.ingest.polling import QUOTA_LIMITS
from app.worker.tasks import get_guard

STALE_AFTER_SECONDS = 600


def main() -> int:
    now = datetime.now(UTC)
    with new_session() as session:
        latest = session.scalar(select(func.max(Departure.last_seen_at)))
        counts = session.execute(
            select(Departure.outcome, func.count()).group_by(Departure.outcome)
        )
        by_outcome = {outcome: count for outcome, count in counts}
        disruptions = session.scalar(select(func.count()).select_from(DisruptionRecord)) or 0

    age = None if latest is None else (now - latest).total_seconds()
    print(f"Latest departure data: {'never' if age is None else f'{age:.0f} s ago'}")
    print("Departures: " + ", ".join(f"{by_outcome.get(o, 0)} {o}" for o in Outcome))
    print(f"Disruptions recorded: {disruptions}")
    print("Quota used today (UTC):")
    guard = get_guard()
    for api, limit in QUOTA_LIMITS.items():
        print(f"  {api:<13} {guard.used_today(api):>4} / {limit.daily}")

    return 0 if age is not None and age < STALE_AFTER_SECONDS else 1


if __name__ == "__main__":
    sys.exit(main())
