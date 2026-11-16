"""Commute subscriptions. Every endpoint here requires the API token."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select

from app.alerts.urls import Resolver, UnsafeUrl, validate_webhook_url
from app.api.deps import SessionDep, get_resolver, rate_limit, require_token
from app.api.schemas import SubscriptionIn, SubscriptionOut
from app.db.models import Subscription
from app.reference.stations import STATIONS_BY_REF

router = APIRouter(
    prefix="/subscriptions", dependencies=[Depends(rate_limit), Depends(require_token)]
)

MAX_SUBSCRIPTIONS = 50


@router.post("", status_code=status.HTTP_201_CREATED)
def create_subscription(
    body: SubscriptionIn,
    session: SessionDep,
    resolve: Annotated[Resolver, Depends(get_resolver)],
) -> SubscriptionOut:
    """Watch a station and direction on some days, in a Paris-time window."""
    if body.station_ref not in STATIONS_BY_REF:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown station. See /stations."
        )
    try:
        validate_webhook_url(body.target, resolve)
    except UnsafeUrl as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"target refused: {exc}"
        ) from exc
    count = session.scalar(select(func.count()).select_from(Subscription)) or 0
    if count >= MAX_SUBSCRIPTIONS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"At most {MAX_SUBSCRIPTIONS} subscriptions are allowed."
        )

    subscription = Subscription(
        station_ref=body.station_ref,
        direction=body.direction,
        days=sorted(body.days),
        start_time=body.start_time,
        end_time=body.end_time,
        channel=body.channel,
        target=body.target,
        created_at=datetime.now(UTC),
    )
    session.add(subscription)
    session.commit()
    return SubscriptionOut.model_validate(subscription)


@router.get("")
def list_subscriptions(session: SessionDep) -> list[SubscriptionOut]:
    rows = session.scalars(select(Subscription).order_by(Subscription.id))
    return [SubscriptionOut.model_validate(row) for row in rows]


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(subscription_id: int, session: SessionDep) -> Response:
    subscription = session.get(Subscription, subscription_id)
    if subscription is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such subscription.")
    session.delete(subscription)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
