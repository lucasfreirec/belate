from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Departure, DisruptionRecord, Outcome
from app.prim.models import Direction
from tests.factories import CDG_2, CITE_UNIVERSITAIRE, DENFERT, GENTILLY, SAINT_REMY, departure
from tests.integration.conftest import API_TOKEN, PUBLIC_HOST

Sessions = Callable[[], Session]
AUTH = {"Authorization": f"Bearer {API_TOKEN}"}


def store(sessions: Sessions, *rows: object) -> None:
    with sessions() as session:
        session.add_all(rows)
        session.commit()


def ago(**duration: float) -> datetime:
    return datetime.now(UTC) - timedelta(**duration)


def ahead(**duration: float) -> datetime:
    return datetime.now(UTC) + timedelta(**duration)


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


class TestStations:
    def test_lists_the_47_stations_in_line_order(self, client: TestClient) -> None:
        stations = client.get("/stations").json()

        assert len(stations) == 47
        assert stations[0] == {
            "ref": CDG_2,
            "name": "Aéroport Charles de Gaulle 2 (Terminal 2)",
            "section": "cdg",
            "position": 0,
        }
        trunk = [s["name"] for s in stations if s["section"] == "trunk"]
        assert trunk[0] == "Aulnay-sous-Bois"
        assert trunk[-1] == "Bourg-la-Reine"


class TestPunctuality:
    def test_overall_figures_for_the_default_week(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        store(
            sessions,
            departure(aimed=ago(days=1), delay=60),
            departure(aimed=ago(days=2), delay=180),
            departure(aimed=ago(days=3), outcome=Outcome.CANCELLED),
            # Too old for the default 7 days.
            departure(aimed=ago(days=10), delay=3000),
        )

        body = client.get("/punctuality").json()

        assert body["group_by"] == []
        (row,) = body["rows"]
        assert row["trains"] == 2
        assert row["cancelled"] == 1
        assert row["avg_delay_seconds"] == pytest.approx(120)
        assert row["cancellation_rate"] == pytest.approx(1 / 3)

    def test_a_longer_window_reaches_older_departures(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        store(sessions, departure(aimed=ago(days=1)), departure(aimed=ago(days=10)))

        assert client.get("/punctuality?window=7d").json()["rows"][0]["trains"] == 1
        assert client.get("/punctuality?window=30d").json()["rows"][0]["trains"] == 2
        assert client.get("/punctuality?window=12h").json()["rows"][0]["trains"] == 0

    def test_grouping_by_station_adds_station_names(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        store(
            sessions,
            departure(aimed=ago(days=1), station=DENFERT, delay=30),
            departure(aimed=ago(days=1), station=CITE_UNIVERSITAIRE, delay=90),
        )

        body = client.get("/punctuality?group_by=station").json()

        assert body["group_by"] == ["station"]
        assert {r["station_name"]: r["avg_delay_seconds"] for r in body["rows"]} == {
            "Denfert-Rochereau": 30,
            "Cité Universitaire": 90,
        }

    def test_several_groups_and_filters(self, client: TestClient, sessions: Sessions) -> None:
        store(
            sessions,
            departure(aimed=ago(days=1), delay=30),
            departure(
                aimed=ago(days=1), delay=90, direction=Direction.NORTHBOUND, destination=CDG_2
            ),
            departure(aimed=ago(days=1), delay=500, station=DENFERT),
        )

        body = client.get(
            "/punctuality",
            params={"group_by": ["branch", "direction"], "station": CITE_UNIVERSITAIRE},
        ).json()

        assert [(r["group"], r["trains"]) for r in body["rows"]] == [
            ({"branch": "cdg", "direction": "northbound"}, 1),
            ({"branch": "saint_remy", "direction": "southbound"}, 1),
        ]
        only_north = client.get("/punctuality?direction=northbound&branch=cdg").json()
        assert only_north["rows"][0]["avg_delay_seconds"] == 90

    @pytest.mark.parametrize(
        ("query", "status"),
        [
            ("window=abc", 422),
            ("window=0d", 422),
            ("window=32d", 422),
            ("window=1000h", 422),
            ("group_by=colour", 422),
            ("direction=sideways", 422),
            ("branch=lyon", 422),
            ("station=STIF:StopArea:SP:1:", 404),
        ],
    )
    def test_bad_parameters_are_refused(self, client: TestClient, query: str, status: int) -> None:
        assert client.get(f"/punctuality?{query}").status_code == status


class TestOtherMetrics:
    def test_segments(self, client: TestClient, sessions: Sessions) -> None:
        start = ago(days=1)
        store(
            sessions,
            departure(journey="a", station=DENFERT, aimed=start, delay=0),
            departure(
                journey="a",
                station=CITE_UNIVERSITAIRE,
                aimed=start + timedelta(minutes=3),
                delay=120,
            ),
            departure(journey="a", station=GENTILLY, aimed=start + timedelta(minutes=6), delay=60),
        )

        body = client.get("/segments?direction=southbound").json()

        assert [
            (s["from_station_name"], s["to_station_name"], s["avg_delay_gained_seconds"])
            for s in body["segments"]
        ] == [
            ("Denfert-Rochereau", "Cité Universitaire", 120),
            ("Cité Universitaire", "Gentilly", -60),
        ]
        assert client.get("/segments").status_code == 422

    def test_cancellations_per_service_day(self, client: TestClient, sessions: Sessions) -> None:
        store(
            sessions,
            departure(aimed=ago(days=1)),
            departure(aimed=ago(days=1), outcome=Outcome.CANCELLED),
            departure(aimed=ago(days=3)),
        )

        body = client.get("/cancellations").json()

        assert (body["trains"], body["cancelled"]) == (2, 1)
        assert body["cancellation_rate"] == pytest.approx(1 / 3)
        assert [(d["trains"], d["cancelled"]) for d in body["by_date"]] == [(1, 0), (1, 1)]

    def test_waiting_time(self, client: TestClient, sessions: Sessions) -> None:
        # Midday yesterday in UTC: one hour of the Paris day, away from the 03:00 day change.
        start = (datetime.now(UTC) - timedelta(days=1)).replace(hour=11, minute=0, second=0)
        store(
            sessions,
            departure(aimed=start),
            departure(aimed=start + timedelta(minutes=10)),
            departure(aimed=start + timedelta(minutes=30)),
        )

        body = client.get(
            "/waiting-time", params={"station": CITE_UNIVERSITAIRE, "direction": "southbound"}
        ).json()

        (hour,) = body["hours"]
        assert hour["trains"] == 2
        assert hour["avg_wait_seconds"] == pytest.approx(500)
        assert client.get("/waiting-time?direction=southbound").status_code == 422
        assert client.get("/waiting-time?station=nowhere&direction=southbound").status_code == 404


def disruption(
    disruption_id: str,
    *,
    periods: list[tuple[datetime, datetime]],
    last_seen: datetime | None = None,
    message: str = "<p>Trafic <b>perturb&#233;</b>.</p>",
) -> DisruptionRecord:
    return DisruptionRecord(
        id=disruption_id,
        cause="PERTURBATION",
        severity="PERTURBEE",
        title=f"RER B : {disruption_id}",
        message=message,
        last_update=ago(hours=1),
        periods=[[begin.isoformat(), end.isoformat()] for begin, end in periods],
        station_refs=[CITE_UNIVERSITAIRE],
        first_seen_at=ago(hours=2),
        last_seen_at=last_seen or ago(minutes=1),
    )


class TestDisruptions:
    def test_lists_what_prim_currently_publishes(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        store(
            sessions,
            disruption("now", periods=[(ago(hours=1), ahead(hours=1))]),
            disruption("tonight", periods=[(ahead(hours=5), ahead(hours=9))]),
            # PRIM stopped publishing this one an hour ago.
            disruption("over", periods=[(ago(hours=3), ahead(hours=1))], last_seen=ago(hours=1)),
        )

        body = client.get("/disruptions").json()

        assert {d["id"]: d["active"] for d in body} == {"now": True, "tonight": False}
        assert body[0]["message"] == "Trafic perturbé."
        assert body[0]["station_refs"] == [CITE_UNIVERSITAIRE]

    def test_active_keeps_only_what_applies_now(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        store(
            sessions,
            disruption("now", periods=[(ago(hours=1), ahead(hours=1))]),
            disruption("tonight", periods=[(ahead(hours=5), ahead(hours=9))]),
        )

        assert [d["id"] for d in client.get("/disruptions?active=true").json()] == ["now"]


def upcoming(minutes: float, **changes: Any) -> Departure:
    """A train due in `minutes`, still open."""
    return departure(aimed=ahead(minutes=minutes), outcome=Outcome.OPEN, **changes)


class TestLive:
    def test_next_trains_in_time_order(self, client: TestClient, sessions: Sessions) -> None:
        late = upcoming(4, delay=300)
        late.train_number, late.mission, late.platform = "KALI48", "KALI", "2"
        store(sessions, upcoming(12), late, upcoming(6))

        body = client.get("/live", params={"station": CITE_UNIVERSITAIRE}).json()

        assert body["station"] == CITE_UNIVERSITAIRE
        assert body["as_of"] is not None
        delays = [d["delay_seconds"] for d in body["departures"]]
        # The train due in 4 minutes is 5 minutes late, so it comes after the one due in 6.
        assert delays == [0, 300, 0]
        delayed = body["departures"][1]
        assert delayed["train_number"] == "KALI48"
        assert delayed["mission"] == "KALI"
        assert delayed["platform"] == "2"
        assert delayed["destination"] == "Saint-Rémy-lès-Chevreuse"
        assert delayed["cancelled"] is False

    def test_leaves_out_trains_that_left_other_stations_and_termini(
        self, client: TestClient, sessions: Sessions
    ) -> None:
        cancelled = upcoming(8)
        cancelled.last_status = "cancelled"
        store(
            sessions,
            upcoming(5),
            cancelled,
            upcoming(-10),  # still open in the data, but long past
            departure(aimed=ago(minutes=3)),  # closed
            upcoming(5, station=DENFERT),
            upcoming(5, terminus=True),
        )

        body = client.get("/live", params={"station": CITE_UNIVERSITAIRE}).json()

        assert [d["cancelled"] for d in body["departures"]] == [False, True]

    def test_direction_filter(self, client: TestClient, sessions: Sessions) -> None:
        store(
            sessions,
            upcoming(5),
            upcoming(7, direction=Direction.NORTHBOUND, destination=CDG_2),
        )

        body = client.get(
            "/live", params={"station": CITE_UNIVERSITAIRE, "direction": "northbound"}
        ).json()

        assert [d["destination"] for d in body["departures"]] == [
            "Aéroport Charles de Gaulle 2 (Terminal 2)"
        ]

    def test_no_data_yet(self, client: TestClient) -> None:
        body = client.get("/live", params={"station": SAINT_REMY}).json()

        assert body == {"station": SAINT_REMY, "as_of": None, "departures": []}

    def test_unknown_station(self, client: TestClient) -> None:
        assert client.get("/live?station=nowhere").status_code == 404
        assert client.get("/live").status_code == 422


class TestRateLimit:
    @pytest.fixture
    def api_settings(self) -> Settings:
        return Settings(api_token=SecretStr(API_TOKEN), rate_limit_per_minute=3)

    def test_requests_past_the_limit_are_refused(self, client: TestClient) -> None:
        statuses = [client.get("/stations").status_code for _ in range(5)]

        assert statuses == [200, 200, 200, 429, 429]
        assert client.get("/stations").headers["Retry-After"] == "60"

    def test_health_is_not_limited(self, client: TestClient) -> None:
        for _ in range(5):
            client.get("/stations")

        assert client.get("/health").status_code == 200


def subscription(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "station_ref": CITE_UNIVERSITAIRE,
        "direction": "northbound",
        "days": [1, 2, 3, 4, 5],
        "start_time": "07:30",
        "end_time": "09:00",
        "channel": "webhook",
        "target": f"https://{PUBLIC_HOST}/notify",
    }
    body.update(changes)
    return body


class TestSubscriptions:
    def test_create_list_and_delete(self, client: TestClient) -> None:
        created = client.post("/subscriptions", json=subscription(days=[5, 1]), headers=AUTH)

        assert created.status_code == 201
        body = created.json()
        assert body["days"] == [1, 5]
        assert body["start_time"] == "07:30:00"
        assert body["target"] == f"https://{PUBLIC_HOST}/notify"
        assert client.get("/subscriptions", headers=AUTH).json() == [body]

        assert client.delete(f"/subscriptions/{body['id']}", headers=AUTH).status_code == 204
        assert client.get("/subscriptions", headers=AUTH).json() == []
        assert client.delete(f"/subscriptions/{body['id']}", headers=AUTH).status_code == 404

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer wrong-token"},
            {"Authorization": API_TOKEN},
            {"Authorization": f"Basic {API_TOKEN}"},
            {"Authorization": "Bearer "},
        ],
    )
    def test_every_endpoint_needs_the_token(
        self, client: TestClient, headers: dict[str, str]
    ) -> None:
        assert (
            client.post("/subscriptions", json=subscription(), headers=headers).status_code == 401
        )
        assert client.get("/subscriptions", headers=headers).status_code == 401
        assert client.delete("/subscriptions/1", headers=headers).status_code == 401

    @pytest.mark.parametrize(
        "changes",
        [
            {"target": f"http://{PUBLIC_HOST}/notify"},
            {"target": "https://internal.example.com/notify"},
            {"target": "https://mixed.example.com/notify"},
            {"target": "https://127.0.0.1/notify"},
            {"target": "https://unknown.example.com/notify"},
            {"target": "not a url"},
            {"station_ref": "STIF:StopArea:SP:1:"},
            {"direction": "sideways"},
            {"days": []},
            {"days": [1, 1]},
            {"days": [0]},
            {"days": [8]},
            {"start_time": "09:00", "end_time": "07:30"},
            {"start_time": "08:00", "end_time": "08:00"},
            {"start_time": "08:00+01:00"},
            {"channel": "pigeon"},
            {"unexpected": True},
        ],
    )
    def test_invalid_subscriptions_are_refused(
        self, client: TestClient, changes: dict[str, Any]
    ) -> None:
        response = client.post("/subscriptions", json=subscription(**changes), headers=AUTH)

        assert response.status_code == 422
        assert client.get("/subscriptions", headers=AUTH).json() == []

    def test_the_refusal_says_why_the_target_is_unsafe(self, client: TestClient) -> None:
        response = client.post(
            "/subscriptions",
            json=subscription(target="https://internal.example.com/notify"),
            headers=AUTH,
        )

        assert "private or reserved address" in response.json()["detail"]

    def test_the_number_of_subscriptions_is_capped(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.api.subscriptions.MAX_SUBSCRIPTIONS", 2)
        for _ in range(2):
            assert (
                client.post("/subscriptions", json=subscription(), headers=AUTH).status_code == 201
            )

        assert client.post("/subscriptions", json=subscription(), headers=AUTH).status_code == 409


class TestSubscriptionsDisabled:
    @pytest.fixture
    def api_settings(self) -> Settings:
        return Settings(api_token=SecretStr(""), rate_limit_per_minute=1000)

    def test_without_a_configured_token_the_endpoints_are_closed(self, client: TestClient) -> None:
        assert client.get("/subscriptions", headers=AUTH).status_code == 503
        assert client.get("/subscriptions", headers={"Authorization": "Bearer "}).status_code == 503
        assert client.post("/subscriptions", json=subscription(), headers=AUTH).status_code == 503
        # Reading stays open.
        assert client.get("/stations").status_code == 200
