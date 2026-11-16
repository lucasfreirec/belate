"""Parse PRIM responses into the objects of `app.prim.models`, keeping only the RER B.

The responses come from an external service, so nothing about their shape is assumed: a
response with a broken envelope raises `ParseError`, and a broken item inside a valid
response is skipped and reported in the result's `errors`.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.prim.models import (
    RER_B_LINE_REF,
    RER_B_TRAFFIC_LINE_ID,
    Api,
    CallStatus,
    DepartureObservation,
    Direction,
    Disruption,
    FeedSnapshot,
    Period,
)

PARIS = ZoneInfo("Europe/Paris")

_DIRECTIONS = {"aller": Direction.SOUTHBOUND, "retour": Direction.NORTHBOUND}
# The global feed writes ON_TIME, the unitary feed onTime.
_STATUSES = {
    "ontime": CallStatus.ON_TIME,
    "delayed": CallStatus.DELAYED,
    "early": CallStatus.EARLY,
    "arrived": CallStatus.ARRIVED,
    "cancelled": CallStatus.CANCELLED,
}
_TRAFFIC_STOP_PREFIX = "stop_point:IDFM:monomodalStopPlace:"


class ParseError(ValueError):
    """The response does not have the expected envelope, so nothing can be read from it."""


def parse_estimated_timetable(body: Any) -> FeedSnapshot:
    """Parse a global `estimated-timetable` response."""
    try:
        delivery = body["Siri"]["ServiceDelivery"]
        produced_at = _time(delivery["ResponseTimestamp"])
        journeys = [
            journey
            for timetable in delivery["EstimatedTimetableDelivery"]
            for frame in timetable["EstimatedJourneyVersionFrame"]
            for journey in frame["EstimatedVehicleJourney"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError(f"unexpected estimated-timetable envelope: {exc!r}") from exc

    observations: list[DepartureObservation] = []
    errors: list[str] = []
    for journey in journeys:
        try:
            if _value(journey["LineRef"]) != RER_B_LINE_REF:
                continue
            journey_ref = _required(_value(journey["DatedVehicleJourneyRef"]), "journey ref")
            calls = journey["EstimatedCalls"]["EstimatedCall"]
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"journey skipped: {exc!r}")
            continue
        for call in calls:
            try:
                station_ref = _required(_value(call["StopPointRef"]), "station ref")
                observations.append(_observation(journey_ref, station_ref, journey, call))
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"call skipped in {journey_ref}: {exc!r}")
    return FeedSnapshot(Api.GLOBAL, produced_at, observations, errors)


def parse_stop_monitoring(body: Any) -> FeedSnapshot:
    """Parse a unitary `stop-monitoring` response."""
    try:
        delivery = body["Siri"]["ServiceDelivery"]
        produced_at = _time(delivery["ResponseTimestamp"])
        visits = [
            visit
            for monitoring in delivery["StopMonitoringDelivery"]
            for visit in monitoring.get("MonitoredStopVisit", [])
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ParseError(f"unexpected stop-monitoring envelope: {exc!r}") from exc

    observations: list[DepartureObservation] = []
    errors: list[str] = []
    for visit in visits:
        try:
            journey = visit["MonitoredVehicleJourney"]
            if _value(journey["LineRef"]) != RER_B_LINE_REF:
                continue
            # Here the journey ref is a plain string, not a {"value": ...} object.
            journey_ref = _required(
                journey["FramedVehicleJourneyRef"]["DatedVehicleJourneyRef"], "journey ref"
            )
            station_ref = _required(_value(visit["MonitoringRef"]), "station ref")
            call = journey["MonitoredCall"]
            observations.append(_observation(journey_ref, station_ref, journey, call))
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"visit skipped: {exc!r}")
    return FeedSnapshot(Api.UNITARY, produced_at, observations, errors)


def parse_traffic_info(body: Any) -> tuple[list[Disruption], list[str]]:
    """Parse a global traffic info response. Return the RER B disruptions and the errors."""
    try:
        lines = [line for line in body["lines"] if line["id"] == RER_B_TRAFFIC_LINE_ID]
        raw_disruptions = {item["id"]: item for item in body["disruptions"]}
    except (KeyError, TypeError) as exc:
        raise ParseError(f"unexpected traffic info envelope: {exc!r}") from exc

    # A disruption is attached to the line itself, to some of its stations, or to both.
    stations: dict[str, list[str]] = {}
    errors: list[str] = []
    for line in lines:
        for impacted in line.get("impactedObjects", []):
            try:
                station_ref = _traffic_station_ref(impacted)
                for disruption_id in impacted["disruptionIds"]:
                    refs = stations.setdefault(disruption_id, [])
                    if station_ref and station_ref not in refs:
                        refs.append(station_ref)
            except (KeyError, TypeError) as exc:
                errors.append(f"impacted object skipped: {exc!r}")

    disruptions: list[Disruption] = []
    for disruption_id, station_refs in stations.items():
        try:
            raw = raw_disruptions[disruption_id]
            disruptions.append(
                Disruption(
                    disruption_id=disruption_id,
                    cause=raw["cause"],
                    severity=raw["severity"],
                    title=raw["title"],
                    message=raw.get("message", ""),
                    last_update=_paris_time(raw["lastUpdate"]),
                    periods=[
                        Period(_paris_time(period["begin"]), _paris_time(period["end"]))
                        for period in raw["applicationPeriods"]
                    ],
                    station_refs=station_refs,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"disruption {disruption_id} skipped: {exc!r}")
    return disruptions, errors


def _observation(
    journey_ref: str, station_ref: str, journey: Any, call: Any
) -> DepartureObservation:
    return DepartureObservation(
        journey_ref=journey_ref,
        station_ref=station_ref,
        direction=_DIRECTIONS.get((_value(journey.get("DirectionRef")) or "").lower()),
        mission=_first(journey.get("JourneyNote")),
        train_number=_first(journey.get("VehicleJourneyName")),
        origin_ref=_value(journey.get("OriginRef")),
        destination_ref=_value(journey.get("DestinationRef")),
        aimed_departure=_optional_time(call.get("AimedDepartureTime")),
        expected_departure=_optional_time(call.get("ExpectedDepartureTime")),
        aimed_arrival=_optional_time(call.get("AimedArrivalTime")),
        expected_arrival=_optional_time(call.get("ExpectedArrivalTime")),
        departure_status=_status(call.get("DepartureStatus")),
        arrival_status=_status(call.get("ArrivalStatus")),
        platform=_value(call.get("DeparturePlatformName"))
        or _value(call.get("ArrivalPlatformName")),
        at_stop=call.get("VehicleAtStop") if isinstance(call.get("VehicleAtStop"), bool) else None,
    )


def _value(node: Any) -> str | None:
    """Read SIRI's `{"value": "..."}` wrapper. Empty objects and empty strings give None."""
    if isinstance(node, dict):
        node = node.get("value")
    return node if isinstance(node, str) and node else None


def _first(nodes: Any) -> str | None:
    """Read the first item of SIRI's `[{"value": "..."}]` lists."""
    return _value(nodes[0]) if isinstance(nodes, list) and nodes else None


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing {name}")
    return value


def _status(raw: Any) -> CallStatus:
    if not isinstance(raw, str):
        return CallStatus.UNKNOWN
    return _STATUSES.get(raw.replace("_", "").lower(), CallStatus.UNKNOWN)


def _time(raw: Any) -> datetime:
    """Parse a SIRI timestamp. It must carry a time zone; the result is in UTC."""
    if not isinstance(raw, str):
        raise ValueError(f"not a timestamp: {raw!r}")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without time zone: {raw!r}")
    return parsed.astimezone(UTC)


def _optional_time(raw: Any) -> datetime | None:
    return None if raw is None else _time(raw)


def _paris_time(raw: Any) -> datetime:
    """Parse a traffic info time such as `20261116T224500`: Paris local time, no zone."""
    if not isinstance(raw, str):
        raise ValueError(f"not a timestamp: {raw!r}")
    return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=PARIS).astimezone(UTC)


def _traffic_station_ref(impacted: Any) -> str | None:
    """Turn a traffic info stop ID into the station ref used by the departure feeds."""
    stop_id = impacted.get("id", "")
    if impacted.get("type") != "stop_point" or not stop_id.startswith(_TRAFFIC_STOP_PREFIX):
        return None
    return f"STIF:StopArea:SP:{stop_id.removeprefix(_TRAFFIC_STOP_PREFIX)}:"
