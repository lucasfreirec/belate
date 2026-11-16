"""HTTP client for the PRIM APIs: timeouts, retries with backoff, and quota accounting."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.prim.models import Api

logger = logging.getLogger(__name__)

# The global response is 58 MB and takes 6 to 12 seconds to download.
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 2.0


class PrimError(Exception):
    """A PRIM request failed and retrying will not help (bad key, bad request, bad body)."""


class PrimUnavailable(PrimError):
    """PRIM kept failing after every retry."""


class RequestGate(Protocol):
    """Decides whether a request may be sent. Implemented by the quota guard."""

    def acquire(self, api: Api, *, retry: bool = False) -> None:
        """Count one request against the quota of `api`, or raise if it must not be sent."""


@dataclass(frozen=True, slots=True)
class PrimResponse:
    body: Any
    # What PRIM says is left of the daily quota, when the header is present.
    quota_remaining: int | None


class PrimClient:
    def __init__(
        self,
        http: httpx.Client,
        gate: RequestGate,
        *,
        sleep: Callable[[float], None] = time.sleep,
        on_attempt: Callable[[Api, str], None] | None = None,
    ) -> None:
        self._http = http
        self._gate = gate
        self._sleep = sleep
        # Called after every HTTP attempt with its outcome: a status code or an error name.
        self._on_attempt = on_attempt

    def estimated_timetable(self) -> PrimResponse:
        """Next departures for the whole network (global query)."""
        return self._get(Api.GLOBAL, "/estimated-timetable", {"LineRef": "ALL"})

    def stop_monitoring(self, station_ref: str) -> PrimResponse:
        """Next departures at one station (unitary request)."""
        return self._get(Api.UNITARY, "/stop-monitoring", {"MonitoringRef": station_ref})

    def traffic_info(self) -> PrimResponse:
        """Traffic info messages for the whole network."""
        return self._get(Api.TRAFFIC_INFO, "/disruptions_bulk/disruptions/v2", {})

    def _get(self, api: Api, path: str, params: dict[str, str]) -> PrimResponse:
        failure = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                self._sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
            # Every attempt uses quota, so every attempt goes through the gate.
            self._gate.acquire(api, retry=attempt > 0)
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                failure = type(exc).__name__
                self._report(api, failure)
            else:
                self._report(api, str(response.status_code))
                if response.status_code == 200:
                    return PrimResponse(_json(response, api), _quota_remaining(response))
                failure = f"HTTP {response.status_code}"
                if response.status_code != 429 and response.status_code < 500:
                    raise PrimError(f"{api} request refused: {failure}")
            logger.warning("PRIM %s attempt %d failed: %s", api, attempt + 1, failure)
        raise PrimUnavailable(f"{api} failed {MAX_ATTEMPTS} times, last error: {failure}")

    def _report(self, api: Api, outcome: str) -> None:
        if self._on_attempt is not None:
            self._on_attempt(api, outcome)


def build_http_client(base_url: str, api_key: str) -> httpx.Client:
    headers = {"apikey": api_key, "Accept": "application/json"}
    return httpx.Client(base_url=base_url, headers=headers, timeout=DEFAULT_TIMEOUT)


def _json(response: httpx.Response, api: Api) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise PrimError(f"{api} response is not JSON") from exc


def _quota_remaining(response: httpx.Response) -> int | None:
    raw = response.headers.get("x-ratelimit-remaining-day", "")
    return int(raw) if raw.isdigit() else None
