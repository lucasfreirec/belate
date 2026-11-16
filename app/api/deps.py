"""Shared dependencies of the API: database session, rate limit, token, time window."""

import hmac
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request, status
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.alerts.urls import Resolver, resolve_host
from app.core.config import Settings, get_settings
from app.db.session import new_session

logger = logging.getLogger(__name__)

MAX_WINDOW = timedelta(days=31)
_WINDOW_PATTERN = re.compile(r"^([1-9]\d{0,2})([dh])$")


def get_session() -> Iterator[Session]:
    with new_session() as session:
        yield session


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def get_resolver() -> Resolver:
    """How webhook host names are resolved. Tests replace it."""
    return resolve_host


def rate_limit(
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Allow a fixed number of requests per client address per minute."""
    client = request.client.host if request.client else "unknown"
    minute = int(time.time() // 60)
    key = f"rate:{client}:{minute}"
    try:
        pipeline = redis.pipeline()
        pipeline.incr(key)
        pipeline.expire(key, 120)
        count = pipeline.execute()[0]
    except RedisError:
        # The limiter protects the API; it must not take it down when Redis is unavailable.
        logger.warning("rate limiter unavailable, request allowed")
        return
    if count > settings.rate_limit_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Try again in a minute.",
            headers={"Retry-After": "60"},
        )


def require_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Require `Authorization: Bearer <API_TOKEN>`."""
    expected = settings.api_token.get_secret_value()
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Subscriptions are disabled on this server."
        )
    scheme, _, given = (authorization or "").partition(" ")
    # compare_digest takes the same time whether the token is nearly right or not.
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A valid API token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )


@dataclass(frozen=True, slots=True)
class Window:
    since: datetime
    until: datetime


def window(
    window: Annotated[
        str,
        Query(description="How far back to look: a number of days or hours, such as 7d or 24h."),
    ] = "7d",
) -> Window:
    match = _WINDOW_PATTERN.match(window)
    if match is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "window must look like 7d or 24h"
        )
    amount, unit = int(match.group(1)), match.group(2)
    length = timedelta(days=amount) if unit == "d" else timedelta(hours=amount)
    if length > MAX_WINDOW:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "window cannot be longer than 31 days"
        )
    now = datetime.now(UTC)
    return Window(since=now - length, until=now)


SessionDep = Annotated[Session, Depends(get_session)]
WindowDep = Annotated[Window, Depends(window)]
