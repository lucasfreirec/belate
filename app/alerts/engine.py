"""Turn incidents into alerts: open one when an incident starts, notify it once, close it.

An alert stays open while its incident lasts, so the same incident never notifies twice. It
is closed once the incident has been absent for 30 minutes; if the incident comes back after
that, it is a new alert.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts.rules import current_incidents, in_window
from app.alerts.senders import Sender, SendError
from app.db.models import Alert, Subscription

logger = logging.getLogger(__name__)

RESOLVE_AFTER = timedelta(minutes=30)
MAX_SEND_ATTEMPTS = 5


@dataclass(slots=True)
class EvaluationStats:
    opened: int = 0
    resolved: int = 0
    sent: int = 0
    failed: int = 0


def evaluate(session: Session, now: datetime, senders: dict[str, Sender]) -> EvaluationStats:
    """Check every subscription and send what is due. The caller commits."""
    stats = EvaluationStats()
    for subscription in session.scalars(select(Subscription)):
        open_alerts = {
            (alert.kind, alert.key): alert
            for alert in session.scalars(
                select(Alert).where(
                    Alert.subscription_id == subscription.id, Alert.resolved_at.is_(None)
                )
            )
        }

        # Outside its window a subscription sees no incident, so its alerts run out.
        if in_window(subscription, now):
            for incident in current_incidents(session, subscription, now):
                alert = open_alerts.get((incident.kind, incident.key))
                if alert is not None:
                    alert.last_active_at = now
                    continue
                alert = Alert(
                    subscription_id=subscription.id,
                    kind=incident.kind,
                    key=incident.key,
                    message=incident.message,
                    opened_at=now,
                    last_active_at=now,
                    send_attempts=0,
                )
                session.add(alert)
                open_alerts[(incident.kind, incident.key)] = alert
                stats.opened += 1

        for alert in open_alerts.values():
            if now - alert.last_active_at >= RESOLVE_AFTER:
                alert.resolved_at = now
                stats.resolved += 1
            elif alert.notified_at is None and alert.send_attempts < MAX_SEND_ATTEMPTS:
                _send(subscription, alert, now, senders, stats)
    return stats


def _send(
    subscription: Subscription,
    alert: Alert,
    now: datetime,
    senders: dict[str, Sender],
    stats: EvaluationStats,
) -> None:
    alert.send_attempts += 1
    sender = senders.get(subscription.channel)
    try:
        if sender is None:
            raise SendError(f"no sender for channel {subscription.channel!r}")
        sender.send(subscription, alert)
    except SendError as exc:
        stats.failed += 1
        logger.warning(
            "alert %s for subscription %s not sent (attempt %d): %s",
            alert.kind,
            subscription.id,
            alert.send_attempts,
            exc,
        )
        return
    alert.notified_at = now
    stats.sent += 1
