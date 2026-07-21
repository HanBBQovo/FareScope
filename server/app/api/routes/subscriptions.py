from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import DatabaseSession, IdentityDependency, require_csrf
from app.api.pagination import (
    InvalidCursorError,
    TimestampCursor,
    decode_timestamp_cursor,
    encode_timestamp_cursor,
)
from app.api.schemas.subscriptions import (
    SearchLegPublic,
    SubscriptionCreateRequest,
    SubscriptionFiltersPublic,
    SubscriptionListResponse,
    SubscriptionPublic,
    SubscriptionStateRequest,
)
from app.services.subscriptions import (
    SubscriptionNotFoundError,
    SubscriptionView,
    create_subscription,
    delete_subscription,
    get_subscription_view,
    list_subscription_views,
    set_subscription_enabled,
)

router = APIRouter()


def _serialize_subscription(view: SubscriptionView) -> SubscriptionPublic:
    subscription = view.subscription
    query = view.search_query
    filters = view.subscription_filter
    return SubscriptionPublic(
        id=subscription.id,
        name=subscription.name,
        enabled=subscription.enabled,
        poll_interval_seconds=subscription.poll_interval_seconds,
        tags=subscription.tags,
        provider=query.provider,
        query_hash=query.query_hash,
        trip_type=query.trip_type,
        cabin=query.cabin,
        currency=query.currency,
        adults=query.adults,
        children=query.children,
        infants=query.infants,
        legs=[
            SearchLegPublic(
                position=leg.position,
                origin=leg.origin_code,
                destination=leg.destination_code,
                departure_date=leg.departure_date,
            )
            for leg in view.legs
        ],
        filters=SubscriptionFiltersPublic(
            direct_only=query.direct_only,
            airline_codes=filters.airline_codes,
            departure_airports=filters.origin_airport_codes,
            arrival_airports=filters.destination_airport_codes,
            max_price_minor=filters.max_price_minor,
            max_stops=filters.max_stops,
            max_duration_minutes=filters.max_duration_minutes,
            departure_minute_start=filters.departure_time_start_minutes,
            departure_minute_end=filters.departure_time_end_minutes,
        ),
        target_price_minor=view.target_price_minor,
        target_currency=view.target_currency,
        next_due_at=subscription.next_due_at,
        last_collected_at=subscription.last_collected_at,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


@router.get("", response_model=SubscriptionListResponse)
async def list_subscriptions(
    identity: IdentityDependency,
    database: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> SubscriptionListResponse:
    as_of = datetime.now(UTC)
    before_created_at = None
    before_id = None
    if cursor is not None:
        try:
            decoded_cursor = decode_timestamp_cursor(cursor)
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        as_of = decoded_cursor.as_of
        before_created_at = decoded_cursor.timestamp
        before_id = decoded_cursor.row_id

    page = await list_subscription_views(
        database,
        user_id=identity.user.id,
        limit=limit,
        as_of=as_of,
        before_created_at=before_created_at,
        before_id=before_id,
    )
    next_cursor = None
    if page.has_more and page.items:
        last_subscription = page.items[-1].subscription
        next_cursor = encode_timestamp_cursor(
            TimestampCursor(
                as_of=as_of,
                timestamp=last_subscription.created_at,
                row_id=last_subscription.id,
            )
        )
    return SubscriptionListResponse(
        items=[_serialize_subscription(item) for item in page.items],
        next_cursor=next_cursor,
        as_of=as_of,
    )


@router.post(
    "",
    response_model=SubscriptionPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_user_subscription(
    payload: SubscriptionCreateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> SubscriptionPublic:
    async with database.begin():
        view = await create_subscription(
            database,
            user=identity.user,
            name=payload.name,
            search=payload.search,
            target_price_minor=payload.target_price_minor,
            poll_interval_seconds=payload.poll_interval_seconds,
            enabled=payload.enabled,
            tags=payload.tags,
        )
    return _serialize_subscription(view)


@router.get("/{subscription_id}", response_model=SubscriptionPublic)
async def get_user_subscription(
    subscription_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> SubscriptionPublic:
    try:
        view = await get_subscription_view(
            database,
            user_id=identity.user.id,
            subscription_id=subscription_id,
        )
    except SubscriptionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return _serialize_subscription(view)


@router.patch(
    "/{subscription_id}/state",
    response_model=SubscriptionPublic,
    dependencies=[Depends(require_csrf)],
)
async def update_subscription_state(
    subscription_id: UUID,
    payload: SubscriptionStateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> SubscriptionPublic:
    try:
        async with database.begin():
            view = await set_subscription_enabled(
                database,
                user=identity.user,
                subscription_id=subscription_id,
                enabled=payload.enabled,
            )
    except SubscriptionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return _serialize_subscription(view)


@router.delete(
    "/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def remove_subscription(
    subscription_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> Response:
    try:
        async with database.begin():
            await delete_subscription(
                database,
                user=identity.user,
                subscription_id=subscription_id,
            )
    except SubscriptionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
