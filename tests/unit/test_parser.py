import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.prim.models import Api, CallStatus, Direction
from app.prim.parser import (
    ParseError,
    parse_estimated_timetable,
    parse_stop_monitoring,
    parse_traffic_info,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "prim"
CITE_UNIVERSITAIRE = "STIF:StopArea:SP:473843:"
CDG_1 = "STIF:StopArea:SP:462398:"
MASSY_PALAISEAU = "STIF:StopArea:SP:58774:"
PARTLY_CANCELLED_TRAIN = "SNCF_MAGENTA_PRD:VehicleJourney::75310902-a3de-4f6b-824f-3628f3df0e4e:LOC"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def global_body() -> Any:
    return load("estimated_timetable.json")


@pytest.fixture
def unitary_body() -> Any:
    return load("stop_monitoring.json")


@pytest.fixture
def traffic_body() -> Any:
    return load("traffic_info.json")


def journeys(body: Any) -> list[Any]:
    delivery = body["Siri"]["ServiceDelivery"]["EstimatedTimetableDelivery"][0]
    result: list[Any] = delivery["EstimatedJourneyVersionFrame"][0]["EstimatedVehicleJourney"]
    return result


class TestEstimatedTimetable:
    def test_keeps_only_rer_b_journeys(self, global_body: Any) -> None:
        snapshot = parse_estimated_timetable(global_body)

        # The fixture holds 7 RER B journeys (161 calls) and 1 RER A journey.
        assert len(journeys(global_body)) == 8
        assert len({o.journey_ref for o in snapshot.observations}) == 7
        assert len(snapshot.observations) == 161
        assert snapshot.errors == []

    def test_reads_feed_timestamp_in_utc(self, global_body: Any) -> None:
        snapshot = parse_estimated_timetable(global_body)

        assert snapshot.api is Api.GLOBAL
        assert snapshot.produced_at == datetime(2026, 11, 16, 12, 22, 1, 388000, tzinfo=UTC)

    def test_reads_a_cancelled_call(self, global_body: Any) -> None:
        snapshot = parse_estimated_timetable(global_body)

        call = next(
            o
            for o in snapshot.observations
            if o.journey_ref == PARTLY_CANCELLED_TRAIN and o.station_ref == CDG_1
        )
        assert call.departure_status is CallStatus.CANCELLED
        assert call.direction is Direction.SOUTHBOUND
        assert call.mission == "KALI"
        assert call.train_number == "KALI48"
        assert call.origin_ref == "STIF:StopArea:SP:47878:"
        assert call.destination_ref == MASSY_PALAISEAU
        assert call.aimed_departure == datetime(2026, 11, 16, 12, 24, 20, tzinfo=UTC)
        assert call.expected_departure == datetime(2026, 11, 16, 12, 24, 20, tzinfo=UTC)
        assert call.platform == "22"
        assert call.at_stop is None

    def test_a_train_can_be_cancelled_at_some_stations_only(self, global_body: Any) -> None:
        snapshot = parse_estimated_timetable(global_body)

        statuses = [
            o.departure_status
            for o in snapshot.observations
            if o.journey_ref == PARTLY_CANCELLED_TRAIN
        ]
        assert statuses.count(CallStatus.CANCELLED) == 2
        assert statuses.count(CallStatus.ON_TIME) == 21

    def test_terminus_has_arrival_times_only(self, global_body: Any) -> None:
        snapshot = parse_estimated_timetable(global_body)

        terminus = next(
            o
            for o in snapshot.observations
            if o.journey_ref == PARTLY_CANCELLED_TRAIN and o.station_ref == MASSY_PALAISEAU
        )
        assert terminus.aimed_departure is None
        assert terminus.expected_departure is None
        assert terminus.aimed_arrival == datetime(2026, 11, 16, 13, 29, 29, tzinfo=UTC)

    def test_reads_both_directions(self, global_body: Any) -> None:
        snapshot = parse_estimated_timetable(global_body)

        directions = {o.direction for o in snapshot.observations}
        assert directions == {Direction.SOUTHBOUND, Direction.NORTHBOUND}

    def test_skips_a_journey_without_id_and_keeps_the_rest(self, global_body: Any) -> None:
        del journeys(global_body)[0]["DatedVehicleJourneyRef"]

        snapshot = parse_estimated_timetable(global_body)

        assert len({o.journey_ref for o in snapshot.observations}) == 6
        assert len(snapshot.errors) == 1

    def test_skips_a_call_with_a_bad_timestamp_and_keeps_the_rest(self, global_body: Any) -> None:
        call = journeys(global_body)[0]["EstimatedCalls"]["EstimatedCall"][0]
        call["AimedDepartureTime"] = "not a time"

        snapshot = parse_estimated_timetable(global_body)

        assert len(snapshot.observations) == 160
        assert len(snapshot.errors) == 1

    def test_rejects_a_timestamp_without_time_zone(self, global_body: Any) -> None:
        call = journeys(global_body)[0]["EstimatedCalls"]["EstimatedCall"][0]
        call["ExpectedDepartureTime"] = "2026-11-16T12:24:20"

        snapshot = parse_estimated_timetable(global_body)

        assert len(snapshot.errors) == 1

    def test_missing_optional_fields_become_none(self, global_body: Any) -> None:
        journey = journeys(global_body)[0]
        journey["JourneyNote"] = []
        journey["DirectionRef"] = {}
        del journey["VehicleJourneyName"]
        for call in journey["EstimatedCalls"]["EstimatedCall"]:
            call["DepartureStatus"] = "SOMETHING_NEW"

        snapshot = parse_estimated_timetable(global_body)

        observation = next(
            o for o in snapshot.observations if o.journey_ref == PARTLY_CANCELLED_TRAIN
        )
        assert snapshot.errors == []
        assert observation.mission is None
        assert observation.direction is None
        assert observation.train_number is None
        assert observation.departure_status is CallStatus.UNKNOWN

    @pytest.mark.parametrize("body", [None, [], {}, {"Siri": {}}, {"Siri": {"ServiceDelivery": 3}}])
    def test_rejects_a_broken_envelope(self, body: Any) -> None:
        with pytest.raises(ParseError):
            parse_estimated_timetable(body)

    def test_an_empty_feed_is_valid(self, global_body: Any) -> None:
        journeys(global_body).clear()

        snapshot = parse_estimated_timetable(global_body)

        assert snapshot.observations == []
        assert snapshot.errors == []


class TestStopMonitoring:
    def test_reads_every_train_at_the_station(self, unitary_body: Any) -> None:
        snapshot = parse_stop_monitoring(unitary_body)

        assert snapshot.api is Api.UNITARY
        assert snapshot.produced_at == datetime(2026, 11, 16, 12, 24, 6, 900000, tzinfo=UTC)
        assert len(snapshot.observations) == 12
        assert {o.station_ref for o in snapshot.observations} == {CITE_UNIVERSITAIRE}
        assert snapshot.errors == []

    def test_normalises_statuses_written_in_camel_case(self, unitary_body: Any) -> None:
        snapshot = parse_stop_monitoring(unitary_body)

        statuses = [o.departure_status for o in snapshot.observations]
        assert statuses.count(CallStatus.ON_TIME) == 10
        assert statuses.count(CallStatus.DELAYED) == 2

    def test_reads_whether_the_train_is_at_the_platform(self, unitary_body: Any) -> None:
        snapshot = parse_stop_monitoring(unitary_body)

        assert [o.at_stop for o in snapshot.observations].count(True) == 1
        assert [o.at_stop for o in snapshot.observations].count(False) == 11

    def test_uses_the_same_train_ids_as_the_global_feed(
        self, unitary_body: Any, global_body: Any
    ) -> None:
        unitary = {o.journey_ref for o in parse_stop_monitoring(unitary_body).observations}
        in_global = {o.journey_ref for o in parse_estimated_timetable(global_body).observations}

        assert len(unitary & in_global) == 6

    def test_skips_a_broken_visit_and_keeps_the_rest(self, unitary_body: Any) -> None:
        visits = unitary_body["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"][0]
        del visits["MonitoredStopVisit"][0]["MonitoredVehicleJourney"]["MonitoredCall"]

        snapshot = parse_stop_monitoring(unitary_body)

        assert len(snapshot.observations) == 11
        assert len(snapshot.errors) == 1

    def test_a_station_without_trains_is_valid(self, unitary_body: Any) -> None:
        delivery = unitary_body["Siri"]["ServiceDelivery"]["StopMonitoringDelivery"][0]
        del delivery["MonitoredStopVisit"]

        assert parse_stop_monitoring(unitary_body).observations == []

    @pytest.mark.parametrize("body", [None, "text", {}, {"Siri": {"ServiceDelivery": {}}}])
    def test_rejects_a_broken_envelope(self, body: Any) -> None:
        with pytest.raises(ParseError):
            parse_stop_monitoring(body)


class TestTrafficInfo:
    def test_keeps_only_rer_b_disruptions(self, traffic_body: Any) -> None:
        disruptions, errors = parse_traffic_info(traffic_body)

        # The fixture holds 6 disruptions: 4 for the RER B, 2 for another line.
        assert len(traffic_body["disruptions"]) == 6
        assert len(disruptions) == 4
        assert {d.cause for d in disruptions} == {"TRAVAUX"}
        assert errors == []

    def test_reads_periods_as_paris_time(self, traffic_body: Any) -> None:
        disruptions, _ = parse_traffic_info(traffic_body)

        works = next(d for d in disruptions if d.disruption_id.startswith("89063746"))
        # "20261214T224500" in Paris in winter is 21:45 UTC.
        assert works.periods[0].begin == datetime(2026, 12, 14, 21, 45, tzinfo=UTC)
        assert works.periods[0].end == datetime(2026, 12, 15, 2, 0, tzinfo=UTC)
        assert len(works.periods) == 5

    def test_reads_summer_time(self, traffic_body: Any) -> None:
        disruptions, _ = parse_traffic_info(traffic_body)

        works = next(d for d in disruptions if d.disruption_id.startswith("209bc1f3"))
        # Last updated "20260702T150909" in Paris in summer is 13:09 UTC.
        assert works.last_update == datetime(2026, 7, 2, 13, 9, 9, tzinfo=UTC)

    def test_lists_the_stations_a_disruption_names(self, traffic_body: Any) -> None:
        disruptions, _ = parse_traffic_info(traffic_body)

        by_id = {d.disruption_id[:8]: d for d in disruptions}
        assert len(by_id["89063746"].station_refs) == 6
        assert "STIF:StopArea:SP:43071:" in by_id["89063746"].station_refs
        # This one applies to the line as a whole.
        assert by_id["728257b6"].station_refs == []

    def test_skips_a_disruption_missing_from_the_list(self, traffic_body: Any) -> None:
        body = copy.deepcopy(traffic_body)
        body["disruptions"] = [d for d in body["disruptions"] if not d["id"].startswith("728257b6")]

        disruptions, errors = parse_traffic_info(body)

        assert len(disruptions) == 3
        assert len(errors) == 1

    @pytest.mark.parametrize("body", [None, {}, {"lines": []}, {"lines": [3], "disruptions": []}])
    def test_rejects_a_broken_envelope(self, body: Any) -> None:
        with pytest.raises(ParseError):
            parse_traffic_info(body)
