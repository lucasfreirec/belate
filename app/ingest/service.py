"""Store what the feeds say: load the rows a response touches, apply the lifecycle, save."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Departure, DisruptionRecord, Outcome
from app.ingest.lifecycle import decide_outcome, merge
from app.prim.models import Api, Disruption, FeedSnapshot

# Any constant works; it only has to be the same in every worker.
_INGEST_LOCK = 20261116


@dataclass(slots=True)
class IngestStats:
    api: Api
    observed: int = 0
    created: int = 0
    closed: Counter[str] = field(default_factory=Counter)
    parse_errors: int = 0


def ingest_snapshot(session: Session, snapshot: FeedSnapshot) -> IngestStats:
    """Record a global or unitary response. The caller commits."""
    # Two workers ingesting at once could both insert the same new departure. The lock
    # makes them take turns; it is released when the transaction ends.
    session.execute(select(func.pg_advisory_xact_lock(_INGEST_LOCK)))

    observed = {(o.journey_ref, o.station_ref): o for o in snapshot.observations}
    stats = IngestStats(snapshot.api, observed=len(observed), parse_errors=len(snapshot.errors))

    journey_refs = {journey_ref for journey_ref, _ in observed}
    known = {
        (row.journey_ref, row.station_ref): row
        for row in session.scalars(select(Departure).where(Departure.journey_ref.in_(journey_refs)))
    }
    for key, observation in observed.items():
        existing = known.get(key)
        row = merge(existing, observation, snapshot.produced_at)
        if existing is None:
            session.add(row)
            stats.created += 1

    # Only the global feed lists every departure, so only it can tell that one is gone.
    if snapshot.api is Api.GLOBAL:
        session.flush()
        # An empty feed says nothing about what left: close only what is long overdue.
        feed_is_empty = not observed
        for row in session.scalars(select(Departure).where(Departure.outcome == Outcome.OPEN)):
            listed = feed_is_empty or (row.journey_ref, row.station_ref) in observed
            outcome = decide_outcome(row, now=snapshot.produced_at, in_latest_global_feed=listed)
            if outcome is not Outcome.OPEN:
                row.outcome = outcome
                row.closed_at = snapshot.produced_at
                stats.closed[outcome] += 1
    return stats


def ingest_disruptions(session: Session, disruptions: list[Disruption], seen_at: datetime) -> int:
    """Record the RER B disruptions PRIM publishes now. Return how many are new."""
    created = 0
    for disruption in disruptions:
        row = session.get(DisruptionRecord, disruption.disruption_id)
        if row is None:
            row = DisruptionRecord(id=disruption.disruption_id, first_seen_at=seen_at)
            session.add(row)
            created += 1
        row.cause = disruption.cause
        row.severity = disruption.severity
        row.title = disruption.title
        row.message = disruption.message
        row.last_update = disruption.last_update
        row.periods = [[p.begin.isoformat(), p.end.isoformat()] for p in disruption.periods]
        row.station_refs = disruption.station_refs
        row.last_seen_at = seen_at
    return created
