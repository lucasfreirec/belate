"""Shapes of the API's requests and responses."""

from datetime import datetime, time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.prim.models import Direction


class StationOut(BaseModel):
    ref: str
    name: str
    section: str
    position: int


class WindowOut(BaseModel):
    since: datetime
    until: datetime


class PunctualityRowOut(BaseModel):
    group: dict[str, Any]
    station_name: str | None = None
    trains: int
    cancelled: int
    cancellation_rate: float | None
    avg_delay_seconds: float | None
    p90_delay_seconds: float | None
    late_share: float | None


class PunctualityOut(BaseModel):
    window: WindowOut
    group_by: list[str]
    rows: list[PunctualityRowOut]


class SegmentOut(BaseModel):
    from_station: str
    from_station_name: str | None
    to_station: str
    to_station_name: str | None
    trains: int
    avg_delay_gained_seconds: float


class SegmentsOut(BaseModel):
    window: WindowOut
    direction: Direction
    segments: list[SegmentOut]


class CancellationsDayOut(BaseModel):
    service_date: str
    trains: int
    cancelled: int
    cancellation_rate: float | None


class CancellationsOut(BaseModel):
    window: WindowOut
    trains: int
    cancelled: int
    cancellation_rate: float | None
    by_date: list[CancellationsDayOut]


class WaitingTimeHourOut(BaseModel):
    hour: int
    trains: int
    avg_wait_seconds: float
    avg_gap_seconds: float


class WaitingTimeOut(BaseModel):
    window: WindowOut
    station: str
    direction: Direction
    hours: list[WaitingTimeHourOut]


class PeriodOut(BaseModel):
    begin: datetime
    end: datetime


class DisruptionOut(BaseModel):
    id: str
    cause: str
    severity: str
    title: str
    message: str  # plain text; the HTML published by PRIM is stripped
    last_update: datetime
    active: bool  # inside one of its periods right now
    periods: list[PeriodOut]
    station_refs: list[str]


class LiveDepartureOut(BaseModel):
    train_number: str | None
    mission: str | None
    direction: str | None
    destination: str | None
    platform: str | None
    aimed_time: datetime | None
    expected_time: datetime | None
    delay_seconds: int | None
    cancelled: bool


class LiveOut(BaseModel):
    station: str
    # When PRIM produced the data shown. None when nothing was collected yet.
    as_of: datetime | None
    departures: list[LiveDepartureOut]


Weekday = Annotated[int, Field(ge=1, le=7)]


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    station_ref: str
    direction: Direction
    days: Annotated[list[Weekday], Field(min_length=1, max_length=7)]
    start_time: time
    end_time: time
    channel: Literal["webhook"] = "webhook"
    target: Annotated[str, Field(max_length=2000)]

    @model_validator(mode="after")
    def check(self) -> "SubscriptionIn":
        if len(set(self.days)) != len(self.days):
            raise ValueError("days must not repeat")
        if self.start_time.tzinfo is not None or self.end_time.tzinfo is not None:
            raise ValueError("times are Paris local times and must not carry a time zone")
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_ref: str
    direction: str
    days: list[int]
    start_time: time
    end_time: time
    channel: str
    target: str
    created_at: datetime
