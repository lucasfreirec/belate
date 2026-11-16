from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
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
        Index("ix_departures_station_aimed", "station_ref", "aimed_departure"),
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

    # Predictions. The last expected time seen is used as the actual time.
    first_expected_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_expected_departure: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_expected_arrival: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last departure status published by the feed (a CallStatus value).
    last_status: Mapped[str] = mapped_column(String(16))

    # When PRIM produced the first and the latest response that contained this departure.
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    outcome: Mapped[str] = mapped_column(String(16), default=Outcome.OPEN)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def aimed_time(self) -> datetime | None:
        """Scheduled time at the station: the departure, or the arrival at a terminus."""
        return self.aimed_departure or self.aimed_arrival

    @property
    def actual_time(self) -> datetime | None:
        """Best known actual time: the last prediction seen."""
        return self.last_expected_departure or self.last_expected_arrival

    @property
    def delay_seconds(self) -> float | None:
        if self.aimed_time is None or self.actual_time is None:
            return None
        return (self.actual_time - self.aimed_time).total_seconds()
