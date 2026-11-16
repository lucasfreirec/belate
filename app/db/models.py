from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Outcome(StrEnum):
    """What became of a departure. See "Definitions" in docs/data.md."""

    OPEN = "open"  # still expected; the feeds may update it
    DEPARTED = "departed"  # final: the train left (or arrived, at a terminus)
    CANCELLED = "cancelled"  # final: the feed flagged it as cancelled
    UNKNOWN = "unknown"  # left the feed well before its expected time, without a flag


class Departure(Base):
    """One train at one station. Each poll updates the row instead of adding a new one."""

    __tablename__ = "departures"
    __table_args__ = (
        UniqueConstraint("journey_ref", "station_ref", name="uq_departures_journey_station"),
        # Closing departures scans the open ones; analytics read by station and time.
        Index("ix_departures_outcome", "outcome"),
        Index("ix_departures_aimed_time", "aimed_time"),
        Index("ix_departures_station_aimed_time", "station_ref", "aimed_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    journey_ref: Mapped[str] = mapped_column(String(128))
    station_ref: Mapped[str] = mapped_column(String(64))

    direction: Mapped[str | None] = mapped_column(String(16))
    mission: Mapped[str | None] = mapped_column(String(16))
    train_number: Mapped[str | None] = mapped_column(String(16))
    origin_ref: Mapped[str | None] = mapped_column(String(64))
    destination_ref: Mapped[str | None] = mapped_column(String(64))
    platform: Mapped[str | None] = mapped_column(String(16))

    # Scheduled times. A terminus has an arrival only, an origin a departure only.
    aimed_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aimed_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Scheduled time at the station: the departure, or the arrival at a terminus. Stored so
    # that analytics can filter and sort on one indexed column.
    aimed_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Predictions. The last expected time seen is used as the actual time.
    first_expected_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_expected_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_expected_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last expected departure seen in each feed separately. Where both exist, their
    # difference measures the error of relying on the global feed alone.
    global_expected_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unitary_expected_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last departure status published by the feed (a CallStatus value).
    last_status: Mapped[str] = mapped_column(String(16))

    # When PRIM produced the first and the latest response that contained this departure.
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    outcome: Mapped[str] = mapped_column(String(16), default=Outcome.OPEN)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Filled when the departure is closed (see app.analytics.derive), for analytics.
    delay_seconds: Mapped[int | None] = mapped_column()
    service_date: Mapped[date | None] = mapped_column(Date)
    local_hour: Mapped[int | None] = mapped_column(SmallInteger)
    # True when the last prediction is recent enough to stand for the actual time.
    reliable: Mapped[bool | None] = mapped_column()

    @property
    def actual_time(self) -> datetime | None:
        """Best known actual time: the last prediction seen."""
        return self.last_expected_departure or self.last_expected_arrival


class DisruptionRecord(Base):
    """One traffic info message about the RER B, kept up to date while PRIM publishes it."""

    __tablename__ = "disruptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cause: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    last_update: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # [[begin, end], ...] as ISO 8601 strings in UTC. A disruption is active inside a period.
    periods: Mapped[list[list[str]]] = mapped_column(JSONB)
    # Stations named by the disruption. Empty when it applies to the line as a whole.
    station_refs: Mapped[list[str]] = mapped_column(JSONB)

    # First and latest poll in which PRIM published this disruption.
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StationRecord(Base):
    """A station of the RER B. Filled by a migration from `app.reference.stations`."""

    __tablename__ = "stations"

    ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    # Part of the line: cdg, mitry, trunk, robinson or saint_remy.
    section: Mapped[str] = mapped_column(String(16))
    # Rank inside the section, from north to south.
    position: Mapped[int] = mapped_column()


class Subscription(Base):
    """A commute to watch: a station and direction, on some days, in a time window."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_ref: Mapped[str] = mapped_column(ForeignKey("stations.ref"))
    direction: Mapped[str] = mapped_column(String(16))
    # ISO weekdays: 1 is Monday, 7 is Sunday.
    days: Mapped[list[int]] = mapped_column(JSONB)
    # Paris local time. The window may not cross midnight.
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    channel: Mapped[str] = mapped_column(String(16))
    # Where to notify. For the webhook channel, an https URL.
    target: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Alert(Base):
    """One incident reported to one subscription. It is notified once, when it opens."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_open", "subscription_id", "resolved_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # disruption, delays or cancellations
    # What tells two incidents of the same kind apart: the disruption ID, or "" otherwise.
    key: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Last evaluation in which the condition was still true.
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_attempts: Mapped[int] = mapped_column(default=0)
