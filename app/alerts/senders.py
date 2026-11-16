"""Ways to deliver an alert. One sender per subscription channel."""

from typing import Protocol

import httpx

from app.alerts.urls import Resolver, UnsafeUrl, resolve_host, validate_webhook_url
from app.db.models import Alert, Subscription
from app.reference.stations import STATIONS_BY_REF

WEBHOOK_TIMEOUT_SECONDS = 5.0


class SendError(Exception):
    """The alert could not be delivered. It will be tried again at the next evaluation."""


class Sender(Protocol):
    def send(self, subscription: Subscription, alert: Alert) -> None:
        """Deliver the alert, or raise `SendError`."""


class WebhookSender:
    """POST the alert as JSON to the subscription's URL."""

    def __init__(self, http: httpx.Client, resolve: Resolver = resolve_host) -> None:
        self._http = http
        self._resolve = resolve

    def send(self, subscription: Subscription, alert: Alert) -> None:
        try:
            # Checked again at every send: the host may resolve elsewhere than at creation.
            validate_webhook_url(subscription.target, self._resolve)
            response = self._http.post(
                subscription.target,
                json={
                    "kind": alert.kind,
                    "message": alert.message,
                    "station": STATIONS_BY_REF[subscription.station_ref].name,
                    "direction": subscription.direction,
                    "opened_at": alert.opened_at.isoformat(),
                },
            )
        except UnsafeUrl as exc:
            raise SendError(f"unsafe webhook URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise SendError(f"webhook request failed: {type(exc).__name__}") from exc
        if not response.is_success:
            raise SendError(f"webhook answered HTTP {response.status_code}")


def build_webhook_client() -> httpx.Client:
    # Redirects are refused: a public URL could redirect to a private address.
    return httpx.Client(timeout=WEBHOOK_TIMEOUT_SECONDS, follow_redirects=False)
