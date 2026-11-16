from collections.abc import Callable

import httpx
import pytest

from app.prim.client import PrimClient, PrimError, PrimUnavailable
from app.prim.models import Api

Handler = Callable[[httpx.Request], httpx.Response]


class RecordingGate:
    """Stands in for the quota guard: records each request it is asked to allow."""

    def __init__(self) -> None:
        self.calls: list[tuple[Api, bool]] = []

    def acquire(self, api: Api, *, retry: bool = False) -> None:
        self.calls.append((api, retry))


class ClosedGate:
    def acquire(self, api: Api, *, retry: bool = False) -> None:
        raise RuntimeError("quota exhausted")


def replies(*responses: httpx.Response | Exception) -> tuple[Handler, list[httpx.Request]]:
    """Build a handler that answers with each given response in turn."""
    queue = list(responses)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return handler, seen


def make_client(
    handler: Handler, gate: RecordingGate | ClosedGate
) -> tuple[PrimClient, list[float]]:
    http = httpx.Client(
        base_url="https://prim.test/marketplace",
        headers={"apikey": "secret-key"},
        transport=httpx.MockTransport(handler),
    )
    sleeps: list[float] = []
    return PrimClient(http, gate, sleep=sleeps.append), sleeps


def ok(body: object = None, remaining: str = "990") -> httpx.Response:
    return httpx.Response(
        200, json=body or {"ok": True}, headers={"x-ratelimit-remaining-day": remaining}
    )


def test_returns_the_body_and_the_remaining_quota() -> None:
    handler, seen = replies(ok({"Siri": {}}, remaining="987"))
    client, sleeps = make_client(handler, RecordingGate())

    response = client.estimated_timetable()

    assert response.body == {"Siri": {}}
    assert response.quota_remaining == 987
    assert sleeps == []
    assert seen[0].url.path == "/marketplace/estimated-timetable"
    assert seen[0].url.params["LineRef"] == "ALL"
    assert seen[0].headers["apikey"] == "secret-key"


def test_each_api_uses_its_own_path_and_quota() -> None:
    handler, seen = replies(ok(), ok())
    gate = RecordingGate()
    client, _ = make_client(handler, gate)

    client.stop_monitoring("STIF:StopArea:SP:473843:")
    client.traffic_info()

    assert seen[0].url.path == "/marketplace/stop-monitoring"
    assert seen[0].url.params["MonitoringRef"] == "STIF:StopArea:SP:473843:"
    assert seen[1].url.path == "/marketplace/disruptions_bulk/disruptions/v2"
    assert gate.calls == [(Api.UNITARY, False), (Api.TRAFFIC_INFO, False)]


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_retries_on_server_errors_and_rate_limiting(status: int) -> None:
    handler, seen = replies(httpx.Response(status), ok())
    gate = RecordingGate()
    client, sleeps = make_client(handler, gate)

    response = client.estimated_timetable()

    assert response.body == {"ok": True}
    assert len(seen) == 2
    assert sleeps == [2.0]
    # The retry is announced to the gate, so it can draw on the retry reserve.
    assert gate.calls == [(Api.GLOBAL, False), (Api.GLOBAL, True)]


def test_retries_on_network_errors() -> None:
    handler, seen = replies(httpx.ConnectError("down"), httpx.ReadTimeout("slow"), ok())
    client, sleeps = make_client(handler, RecordingGate())

    client.estimated_timetable()

    assert len(seen) == 3
    assert sleeps == [2.0, 4.0]


def test_gives_up_after_three_attempts() -> None:
    handler, seen = replies(httpx.Response(503), httpx.Response(503), httpx.Response(503))
    client, sleeps = make_client(handler, RecordingGate())

    with pytest.raises(PrimUnavailable, match="HTTP 503"):
        client.estimated_timetable()

    assert len(seen) == 3
    assert sleeps == [2.0, 4.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_does_not_retry_a_refused_request(status: int) -> None:
    handler, seen = replies(httpx.Response(status))
    client, sleeps = make_client(handler, RecordingGate())

    with pytest.raises(PrimError, match=f"HTTP {status}"):
        client.estimated_timetable()

    assert len(seen) == 1
    assert sleeps == []


def test_sends_nothing_when_the_gate_refuses() -> None:
    handler, seen = replies(ok())
    client, _ = make_client(handler, ClosedGate())

    with pytest.raises(RuntimeError, match="quota exhausted"):
        client.estimated_timetable()

    assert seen == []


def test_rejects_a_body_that_is_not_json() -> None:
    handler, _ = replies(httpx.Response(200, text="<html>maintenance</html>"))
    client, _ = make_client(handler, RecordingGate())

    with pytest.raises(PrimError, match="not JSON"):
        client.estimated_timetable()


def test_remaining_quota_is_none_without_the_header() -> None:
    handler, _ = replies(httpx.Response(200, json={}))
    client, _ = make_client(handler, RecordingGate())

    assert client.estimated_timetable().quota_remaining is None


def test_error_messages_never_contain_the_api_key() -> None:
    handler, _ = replies(httpx.Response(401))
    client, _ = make_client(handler, RecordingGate())

    with pytest.raises(PrimError) as error:
        client.estimated_timetable()

    assert "secret-key" not in str(error.value)
