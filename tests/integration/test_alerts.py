from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts.engine import MAX_SEND_ATTEMPTS, evaluate
from app.alerts.rules import in_window
from app.alerts.senders import Sender, SendError, WebhookSender
from app.db.models import Alert, DisruptionRecord, Outcome, Subscription
from app.prim.models import CallStatus, Direction
from tests.factories import CDG_2, CITE_UNIVERSITAIRE, DENFERT, departure
from tests.integration.conftest import API_TOKEN, PUBLIC_HOST, fake_resolve

Sessions = Callable[[], Session]

# Monday 16 November 2026, 08:10 in Paris.
NOW = datetime(2026, 11, 16, 7, 10, tzinfo=UTC)


def minutes(count: float) -> timedelta:
    return timedelta(minutes=count)


class RecordingSender:
    """Keeps what it was asked to send. Fails while `failing` is true."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str, str]] = []
        self.failing = False

    def send(self, subscription: Subscription, alert: Alert) -> None:
        if self.failing:
            raise SendError("webhook is down")
        self.sent.append((subscription.id, alert.kind, alert.message))


def commute(**changes: Any) -> Subscription:
    """Weekday mornings, 07:30 to 09:00, northbound from Cité Universitaire."""
    values: dict[str, Any] = {
        "station_ref": CITE_UNIVERSITAIRE,
        "direction": Direction.NORTHBOUND,
        "days": [1, 2, 3, 4, 5],
        "start_time": time(7, 30),
        "end_time": time(9, 0),
        "channel": "webhook",
        "target": f"https://{PUBLIC_HOST}/notify",
        "created_at": NOW - timedelta(days=30),
    }
    values.update(changes)
    return Subscription(**values)


def incident(
    disruption_id: str = "incident-1",
    *,
    begin: datetime = NOW - timedelta(minutes=30),
    end: datetime = NOW + timedelta(hours=3),
    last_seen: datetime = NOW - timedelta(minutes=1),
    cause: str = "PERTURBATION",
    severity: str = "PERTURBEE",
    stations: list[str] | None = None,
) -> DisruptionRecord:
    return DisruptionRecord(
        id=disruption_id,
        cause=cause,
        severity=severity,
        title="RER B : trafic perturbé",
        message="",
        last_update=begin,
        periods=[[begin.isoformat(), end.isoformat()]],
        station_refs=stations or [],
        first_seen_at=begin,
        last_seen_at=last_seen,
    )


def train(minutes_ago: float, delay: float = 0, **changes: Any) -> Any:
    """A northbound train at Cité Universitaire that left `minutes_ago` minutes before NOW."""
    actual = NOW - minutes(minutes_ago)
    changes.setdefault("direction", Direction.NORTHBOUND)
    changes.setdefault("destination", CDG_2)
    return departure(aimed=actual - timedelta(seconds=delay), delay=delay, **changes)


def cancelled(minutes_ago: float, **changes: Any) -> Any:
    row = train(minutes_ago, **changes)
    row.last_status = CallStatus.CANCELLED
    row.outcome = Outcome.OPEN
    return row


class Scenario:
    """A database, a sender, and a clock moved by hand."""

    def __init__(self, sessions: Sessions) -> None:
        self.session = sessions()
        self.sender = RecordingSender()
        self.senders: dict[str, Sender] = {"webhook": self.sender}

    def add(self, *rows: object) -> None:
        self.session.add_all(rows)
        self.session.commit()

    def evaluate(self, at: datetime = NOW) -> Any:
        stats = evaluate(self.session, at, self.senders)
        self.session.commit()
        return stats

    def kinds_sent(self) -> list[str]:
        return [kind for _, kind, _ in self.sender.sent]

    def alerts(self) -> list[Alert]:
        return list(self.session.scalars(select(Alert).order_by(Alert.id)))


@pytest.fixture
def scenario(sessions: Sessions) -> Scenario:
    return Scenario(sessions)


class TestWindow:
    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            (datetime(2026, 11, 16, 6, 30, tzinfo=UTC), True),  # Monday 07:30 in Paris
            (datetime(2026, 11, 16, 6, 29, tzinfo=UTC), False),
            (datetime(2026, 11, 16, 8, 0, tzinfo=UTC), True),  # 09:00, the last minute
            (datetime(2026, 11, 16, 8, 1, tzinfo=UTC), False),
            (datetime(2026, 11, 21, 7, 0, tzinfo=UTC), False),  # Saturday
            (datetime(2026, 7, 1, 6, 0, tzinfo=UTC), True),  # Wednesday 08:00, summer time
            (datetime(2026, 7, 1, 7, 30, tzinfo=UTC), False),  # 09:30 in summer
        ],
    )
    def test_days_and_hours_are_paris_time(self, moment: datetime, expected: bool) -> None:
        assert in_window(commute(), moment) is expected


class TestDisruptionAlerts:
    def test_an_active_disruption_alerts_once(self, scenario: Scenario) -> None:
        scenario.add(commute(), incident())

        first = scenario.evaluate()
        second = scenario.evaluate(NOW + minutes(2))
        third = scenario.evaluate(NOW + minutes(4))

        assert (first.opened, first.sent) == (1, 1)
        assert (second.opened, second.sent) == (0, 0)
        assert (third.opened, third.sent) == (0, 0)
        assert scenario.sender.sent == [(1, "disruption", "RER B : trafic perturbé")]

    def test_two_disruptions_are_two_alerts(self, scenario: Scenario) -> None:
        scenario.add(commute(), incident("a"), incident("b"))

        scenario.evaluate()
        scenario.evaluate(NOW + minutes(2))

        assert scenario.kinds_sent() == ["disruption", "disruption"]

    def test_each_subscription_gets_its_own_alert(self, scenario: Scenario) -> None:
        scenario.add(commute(), commute(station_ref=DENFERT), incident())

        scenario.evaluate()

        assert [subscription_id for subscription_id, _, _ in scenario.sender.sent] == [1, 2]

    @pytest.mark.parametrize(
        "changes",
        [
            {"begin": NOW + timedelta(hours=2)},  # starts later
            {"end": NOW - minutes(5)},  # already over
            {"last_seen": NOW - timedelta(hours=1)},  # no longer published
            {"cause": "INFORMATION"},
            {"severity": "INFORMATION"},
            {"stations": [DENFERT]},  # another station
        ],
    )
    def test_a_disruption_that_does_not_apply_is_ignored(
        self, scenario: Scenario, changes: dict[str, Any]
    ) -> None:
        scenario.add(commute(), incident(**changes))

        assert scenario.evaluate().opened == 0

    def test_planned_works_and_station_disruptions_alert(self, scenario: Scenario) -> None:
        scenario.add(
            commute(),
            incident("works", cause="TRAVAUX", severity="BLOQUANTE"),
            incident("station", stations=[DENFERT, CITE_UNIVERSITAIRE]),
        )

        assert scenario.evaluate().opened == 2

    def test_nothing_is_sent_outside_the_window(self, scenario: Scenario) -> None:
        scenario.add(commute(start_time=time(17, 0), end_time=time(19, 0)), incident())

        assert scenario.evaluate().opened == 0
        assert scenario.sender.sent == []

    def test_an_alert_is_resolved_after_thirty_quiet_minutes(self, scenario: Scenario) -> None:
        # The disruption ends 10 minutes from now; it is last seen active now.
        scenario.add(commute(), incident(end=NOW + minutes(10)))
        scenario.evaluate()

        too_early = scenario.evaluate(NOW + minutes(28))
        resolved = scenario.evaluate(NOW + minutes(30))

        assert too_early.resolved == 0
        assert resolved.resolved == 1
        (alert,) = scenario.alerts()
        assert alert.resolved_at == NOW + minutes(30)
        assert alert.notified_at == NOW

    def test_an_incident_that_returns_after_being_resolved_alerts_again(
        self, scenario: Scenario
    ) -> None:
        scenario.add(commute(end_time=time(12, 0)), incident(end=NOW + minutes(1)))
        scenario.evaluate()
        scenario.evaluate(NOW + minutes(40))
        scenario.add(incident("incident-2", begin=NOW + minutes(50), last_seen=NOW + minutes(59)))

        again = scenario.evaluate(NOW + minutes(60))

        assert again.opened == 1
        assert scenario.kinds_sent() == ["disruption", "disruption"]
        assert [alert.resolved_at is None for alert in scenario.alerts()] == [False, True]

    def test_a_short_pause_does_not_alert_twice(self, scenario: Scenario) -> None:
        # The disruption drops out of the feed for a few polls, then comes back.
        scenario.add(commute(), incident())
        scenario.evaluate()
        disruption = scenario.session.get(DisruptionRecord, "incident-1")
        assert disruption is not None
        disruption.last_seen_at = NOW - timedelta(hours=1)
        scenario.session.commit()
        scenario.evaluate(NOW + minutes(10))
        disruption.last_seen_at = NOW + minutes(19)
        scenario.session.commit()

        scenario.evaluate(NOW + minutes(20))

        assert scenario.kinds_sent() == ["disruption"]
        assert len(scenario.alerts()) == 1


class TestDelayAlerts:
    def test_three_late_trains_alert(self, scenario: Scenario) -> None:
        scenario.add(commute(), train(5, delay=420), train(12, delay=480), train(20, delay=360))

        scenario.evaluate()

        assert scenario.sender.sent == [
            (
                1,
                "delays",
                "Trains at Cité Universitaire (northbound) are running about 7 minutes late.",
            )
        ]

    def test_two_trains_are_not_enough(self, scenario: Scenario) -> None:
        scenario.add(commute(), train(5, delay=900), train(12, delay=900))

        assert scenario.evaluate().opened == 0

    def test_the_median_must_pass_five_minutes(self, scenario: Scenario) -> None:
        # One very late train does not move the median.
        scenario.add(commute(), train(5, delay=1800), train(12, delay=120), train(20, delay=300))

        assert scenario.evaluate().opened == 0

    def test_only_recent_trains_at_this_station_and_direction_count(
        self, scenario: Scenario
    ) -> None:
        scenario.add(
            commute(),
            train(5, delay=600),
            train(12, delay=600),
            train(45, delay=600),  # too old
            train(8, delay=600, station=DENFERT),
            train(8, delay=600, direction=Direction.SOUTHBOUND),
            cancelled(10),
        )

        assert scenario.evaluate().opened == 0

    def test_trains_still_to_come_do_not_count(self, scenario: Scenario) -> None:
        scenario.add(commute(), train(-5, delay=600), train(-10, delay=600), train(-15, delay=600))

        assert scenario.evaluate().opened == 0

    def history(self, days: int, delay: float) -> list[Any]:
        """Past days at the same hour, every train `delay` seconds late."""
        return [
            train(24 * 60 * day + offset, delay=delay)
            for day in range(1, days + 1)
            for offset in (0, 10, 20)
        ]

    def test_delays_usual_for_that_hour_do_not_alert(self, scenario: Scenario) -> None:
        # This hour is usually 10 minutes late; 7 minutes is a good day.
        scenario.add(commute(days=[1, 2, 3, 4, 5, 6, 7]), *self.history(days=20, delay=600))
        scenario.add(train(5, delay=420), train(12, delay=420), train(20, delay=420))

        assert scenario.evaluate().opened == 0

    def test_delays_above_the_usual_level_alert(self, scenario: Scenario) -> None:
        scenario.add(commute(days=[1, 2, 3, 4, 5, 6, 7]), *self.history(days=20, delay=600))
        scenario.add(train(5, delay=900), train(12, delay=900), train(20, delay=900))

        assert scenario.evaluate().opened == 1

    def test_too_little_history_falls_back_to_five_minutes(self, scenario: Scenario) -> None:
        scenario.add(commute(days=[1, 2, 3, 4, 5, 6, 7]), *self.history(days=10, delay=600))
        scenario.add(train(5, delay=420), train(12, delay=420), train(20, delay=420))

        assert scenario.evaluate().opened == 1

    def test_ongoing_delays_alert_once_then_again_after_a_quiet_spell(
        self, scenario: Scenario
    ) -> None:
        scenario.add(commute(end_time=time(12, 0)), *(train(m, delay=600) for m in (5, 12, 20)))
        scenario.evaluate()
        scenario.add(*(train(m, delay=600) for m in (-5, -12)))
        scenario.evaluate(NOW + minutes(14))
        assert scenario.kinds_sent() == ["delays"]

        # Nothing late for a while, then three more late trains an hour and a half later.
        scenario.evaluate(NOW + minutes(60))
        scenario.add(*(train(m, delay=600) for m in (-75, -80, -85)))
        scenario.evaluate(NOW + minutes(90))

        assert scenario.kinds_sent() == ["delays", "delays"]


class TestCancellationAlerts:
    def test_two_cancelled_trains_alert(self, scenario: Scenario) -> None:
        scenario.add(commute(), cancelled(5), cancelled(15), train(10))

        scenario.evaluate()

        assert scenario.sender.sent == [
            (
                1,
                "cancellations",
                "2 trains at Cité Universitaire (northbound) were cancelled "
                "in the last 30 minutes.",
            )
        ]

    def test_one_cancelled_train_is_not_enough(self, scenario: Scenario) -> None:
        scenario.add(commute(), cancelled(5), cancelled(50), cancelled(-10))

        assert scenario.evaluate().opened == 0

    def test_other_directions_and_stations_do_not_count(self, scenario: Scenario) -> None:
        scenario.add(
            commute(),
            cancelled(5),
            cancelled(8, direction=Direction.SOUTHBOUND),
            cancelled(8, station=DENFERT),
        )

        assert scenario.evaluate().opened == 0

    def test_several_kinds_of_incident_are_separate_alerts(self, scenario: Scenario) -> None:
        scenario.add(
            commute(),
            incident(),
            cancelled(5),
            cancelled(15),
            *(train(m, delay=600) for m in (4, 9, 14)),
        )

        scenario.evaluate()
        scenario.evaluate(NOW + minutes(2))

        assert sorted(scenario.kinds_sent()) == ["cancellations", "delays", "disruption"]


class TestDelivery:
    def test_a_failed_send_is_retried_at_the_next_evaluation(self, scenario: Scenario) -> None:
        scenario.add(commute(), incident())
        scenario.sender.failing = True

        failed = scenario.evaluate()
        scenario.sender.failing = False
        retried = scenario.evaluate(NOW + minutes(2))
        after = scenario.evaluate(NOW + minutes(4))

        assert (failed.sent, failed.failed) == (0, 1)
        assert (retried.sent, retried.failed) == (1, 0)
        assert (after.sent, after.failed) == (0, 0)
        (alert,) = scenario.alerts()
        assert alert.notified_at == NOW + minutes(2)
        assert alert.send_attempts == 2
        assert scenario.kinds_sent() == ["disruption"]

    def test_sending_stops_after_five_failures(self, scenario: Scenario) -> None:
        scenario.add(commute(), incident())
        scenario.sender.failing = True

        results = [scenario.evaluate(NOW + minutes(2 * step)) for step in range(8)]

        assert sum(result.failed for result in results) == MAX_SEND_ATTEMPTS
        (alert,) = scenario.alerts()
        assert alert.send_attempts == MAX_SEND_ATTEMPTS
        assert alert.notified_at is None

    def test_an_unknown_channel_fails_without_crashing(self, scenario: Scenario) -> None:
        scenario.add(commute(channel="pigeon"), commute(), incident())

        stats = scenario.evaluate()

        assert (stats.sent, stats.failed) == (1, 1)

    def test_deleting_a_subscription_deletes_its_alerts(
        self, scenario: Scenario, client: TestClient
    ) -> None:
        scenario.add(commute(), incident())
        scenario.evaluate()

        response = client.delete(
            "/subscriptions/1", headers={"Authorization": f"Bearer {API_TOKEN}"}
        )

        assert response.status_code == 204
        assert scenario.session.scalar(select(func.count()).select_from(Alert)) == 0


class TestWebhookSender:
    def alert(self) -> tuple[Subscription, Alert]:
        subscription = commute()
        alert = Alert(kind="delays", key="", message="Trains are late.", opened_at=NOW)
        return subscription, alert

    def sender(self, reply: httpx.Response) -> tuple[WebhookSender, list[httpx.Request]]:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return reply

        http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        return WebhookSender(http, fake_resolve), requests

    def test_posts_the_alert_as_json(self) -> None:
        sender, requests = self.sender(httpx.Response(204))
        subscription, alert = self.alert()

        sender.send(subscription, alert)

        (request,) = requests
        assert request.method == "POST"
        assert str(request.url) == f"https://{PUBLIC_HOST}/notify"
        assert httpx.Response(200, content=request.content).json() == {
            "kind": "delays",
            "message": "Trains are late.",
            "station": "Cité Universitaire",
            "direction": "northbound",
            "opened_at": "2026-11-16T07:10:00+00:00",
        }

    @pytest.mark.parametrize("status", [302, 400, 404, 500])
    def test_anything_but_success_is_a_failure(self, status: int) -> None:
        # A redirect is not followed: it could lead to a private address.
        reply = httpx.Response(status, headers={"Location": "https://internal.example.com/"})
        sender, requests = self.sender(reply)

        with pytest.raises(SendError, match=f"HTTP {status}"):
            sender.send(*self.alert())

        assert len(requests) == 1

    def test_nothing_is_sent_if_the_host_now_resolves_to_a_private_address(self) -> None:
        sender, requests = self.sender(httpx.Response(204))
        subscription, alert = self.alert()
        subscription.target = "https://internal.example.com/notify"

        with pytest.raises(SendError, match="unsafe webhook URL"):
            sender.send(subscription, alert)

        assert requests == []

    def test_a_network_error_is_a_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow")

        sender = WebhookSender(httpx.Client(transport=httpx.MockTransport(handler)), fake_resolve)

        with pytest.raises(SendError, match="ConnectTimeout"):
            sender.send(*self.alert())
