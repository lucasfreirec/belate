"""Celery tasks: thin wrappers that build the dependencies and call `app.ingest.polling`."""

import logging
from datetime import UTC, datetime
from functools import lru_cache

from redis import Redis

from app.alerts.engine import evaluate
from app.alerts.senders import Sender, WebhookSender, build_webhook_client
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.telemetry import Telemetry
from app.db.session import new_session
from app.ingest import polling
from app.prim.client import PrimClient, build_http_client
from app.prim.quota import QuotaGuard
from app.worker.celery_app import celery_app

configure_logging()
logger = logging.getLogger(__name__)


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


@lru_cache
def get_guard() -> QuotaGuard:
    return QuotaGuard(get_redis(), polling.QUOTA_LIMITS)


@lru_cache
def get_telemetry() -> Telemetry:
    return Telemetry(get_redis())


@lru_cache
def get_client() -> PrimClient:
    settings = get_settings()
    api_key = settings.prim_api_key.get_secret_value()
    if not api_key:
        raise RuntimeError("PRIM_API_KEY is not set")
    return PrimClient(
        build_http_client(settings.prim_base_url, api_key),
        get_guard(),
        on_attempt=get_telemetry().prim_request,
    )


@celery_app.task
def poll_global() -> None:
    polling.poll_global(get_client(), get_guard(), new_session, get_telemetry())


@celery_app.task
def poll_unitary() -> None:
    polling.poll_unitary(
        get_client(), get_guard(), new_session, get_settings().prim_station_ref, get_telemetry()
    )


@celery_app.task
def poll_traffic_info() -> None:
    polling.poll_traffic_info(get_client(), get_guard(), new_session, get_telemetry())


@lru_cache
def get_senders() -> dict[str, Sender]:
    return {"webhook": WebhookSender(build_webhook_client())}


@celery_app.task
def evaluate_alerts() -> None:
    with new_session() as session:
        stats = evaluate(session, datetime.now(UTC), get_senders())
        session.commit()
    if stats.opened or stats.resolved or stats.failed:
        logger.info(
            "alerts: %d opened, %d sent, %d failed, %d resolved",
            stats.opened,
            stats.sent,
            stats.failed,
            stats.resolved,
        )
