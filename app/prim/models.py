"""Clean internal objects built from PRIM responses. See docs/data.md for the raw fields."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

RER_B_LINE_REF = "STIF:Line::C01743:"
RER_B_TRAFFIC_LINE_ID = "line:IDFM:C01743"


class Api(StrEnum):
    """The PRIM APIs the project calls. Each one has its own daily quota."""

    GLOBAL = "global"
    UNITARY = "unitary"
    TRAFFIC_INFO = "traffic_info"


class Direction(StrEnum):
    SOUTHBOUND = "southbound"  # "Aller": towards Robinson, Massy, Saint-Rémy
    NORTHBOUND = "northbound"  # "Retour": towards Aéroport CDG, Mitry - Claye


class CallStatus(StrEnum):
    """Status of a train at a station, as published by the feed.

    Only CANCELLED is trusted: the feed marks trains ON_TIME with several minutes of delay.
    """

    ON_TIME = "on_time"
    DELAYED = "delayed"
    EARLY = "early"
    ARRIVED = "arrived"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DepartureObservation:
    """One train at one station, as seen in one response."""

    journey_ref: str
    station_ref: str
    direction: Direction | None
    mission: str | None
    train_number: str | None
    origin_ref: str | None
    destination_ref: str | None
    aimed_departure: datetime | None
    expected_departure: datetime | None
    aimed_arrival: datetime | None
    expected_arrival: datetime | None
    departure_status: CallStatus
    arrival_status: CallStatus
    platform: str | None
    # Only the unitary feed says whether the train is at the platform.
    at_stop: bool | None = None


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    """The RER B content of one global or unitary response."""

    api: Api
    # When PRIM produced the response. The global feed can be 2 minutes older than the request.
    produced_at: datetime
    observations: list[DepartureObservation]
    # One message per item that could not be parsed. The rest of the response is still used.
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Period:
    begin: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class Disruption:
    """One traffic info message that applies to the RER B."""

    disruption_id: str
    cause: str  # TRAVAUX (planned works), PERTURBATION (incident) or INFORMATION
    severity: str  # BLOQUANTE, PERTURBEE or INFORMATION
    title: str
    message: str
    last_update: datetime
    periods: list[Period]
    # Stations named by the disruption. Empty when it applies to the line as a whole.
    station_refs: list[str]
