from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, text

from app.api.dependencies import DatabaseSession, IdentityDependency, require_csrf
from app.api.pagination import (
    InvalidCursorError,
    TimestampCursor,
    decode_timestamp_cursor,
    encode_timestamp_cursor,
)
from app.api.schemas.comparisons import (
    ComparisonCalendarPointPublic,
    ComparisonRouteSnapshotPublic,
    ComparisonSnapshotResponse,
    ComparisonViewCreateRequest,
    ComparisonViewListResponse,
    ComparisonViewPublic,
    ComparisonViewReplaceRequest,
)
from app.api.schemas.fares import PricePointPublic, ResponseMeta
from app.services.comparisons import (
    ComparisonConflictError,
    ComparisonError,
    ComparisonLimitError,
    ComparisonNotFoundError,
    ComparisonVersionConflictError,
    ComparisonViewRecord,
    create_comparison_view,
    delete_comparison_view,
    get_comparison_view,
    list_comparison_views,
    replace_comparison_view,
)
from app.services.fare_data import (
    HistoryPoint,
    SubscriptionFareContext,
    load_subscription_calendar_analytics,
    load_subscription_fare_contexts,
    load_subscription_latest_calendar_fares,
    load_subscription_latest_fares,
    load_subscription_price_analytics,
)

router = APIRouter()


def _serialize_view(record: ComparisonViewRecord) -> ComparisonViewPublic:
    view = record.view
    return ComparisonViewPublic(
        id=view.id,
        name=view.name,
        currency=view.currency,
        trendDays=view.trend_days,
        version=view.version,
        configuredRouteCount=view.configured_route_count,
        activeRouteCount=record.active_route_count,
        missingSubscriptionCount=record.missing_subscription_count,
        comparable=record.comparable,
        subscriptionIds=list(record.subscription_ids),
        createdAt=view.created_at,
        updatedAt=view.updated_at,
    )


@router.post(
    "",
    response_model=ComparisonViewPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def create_saved_comparison(
    payload: ComparisonViewCreateRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> ComparisonViewPublic:
    try:
        async with database.begin():
            record, _ = await create_comparison_view(
                database,
                user=identity.user,
                name=payload.name,
                subscription_ids=tuple(payload.subscription_ids),
                trend_days=payload.trend_days,
                idempotency_key=payload.idempotency_key,
            )
    except ComparisonNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ComparisonLimitError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(error),
        ) from error
    except ComparisonConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ComparisonError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _serialize_view(record)


@router.get("", response_model=ComparisonViewListResponse)
async def list_saved_comparisons(
    identity: IdentityDependency,
    database: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ComparisonViewListResponse:
    as_of = datetime.now(UTC)
    before_created_at = None
    before_id = None
    if cursor is not None:
        try:
            decoded = decode_timestamp_cursor(cursor)
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        as_of = decoded.as_of
        before_created_at = decoded.timestamp
        before_id = decoded.row_id
    page = await list_comparison_views(
        database,
        user_id=identity.user.id,
        as_of=as_of,
        limit=limit,
        before_created_at=before_created_at,
        before_id=before_id,
    )
    next_cursor = None
    if page.has_more and page.items:
        last = page.items[-1].view
        next_cursor = encode_timestamp_cursor(
            TimestampCursor(as_of=as_of, timestamp=last.created_at, row_id=last.id)
        )
    return ComparisonViewListResponse(
        items=[_serialize_view(record) for record in page.items],
        nextCursor=next_cursor,
        asOf=as_of,
    )


@router.get("/{comparison_id}", response_model=ComparisonViewPublic)
async def get_saved_comparison(
    comparison_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> ComparisonViewPublic:
    try:
        record = await get_comparison_view(
            database,
            user_id=identity.user.id,
            comparison_id=comparison_id,
        )
    except ComparisonNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return _serialize_view(record)


@router.put(
    "/{comparison_id}",
    response_model=ComparisonViewPublic,
    dependencies=[Depends(require_csrf)],
)
async def replace_saved_comparison(
    comparison_id: UUID,
    payload: ComparisonViewReplaceRequest,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> ComparisonViewPublic:
    try:
        async with database.begin():
            record = await replace_comparison_view(
                database,
                user=identity.user,
                comparison_id=comparison_id,
                name=payload.name,
                subscription_ids=tuple(payload.subscription_ids),
                trend_days=payload.trend_days,
                expected_version=payload.expected_version,
            )
    except ComparisonNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ComparisonVersionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ComparisonConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ComparisonError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return _serialize_view(record)


@router.delete(
    "/{comparison_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def remove_saved_comparison(
    comparison_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> Response:
    try:
        async with database.begin():
            await delete_comparison_view(
                database,
                user=identity.user,
                comparison_id=comparison_id,
            )
    except ComparisonNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{comparison_id}/snapshot", response_model=ComparisonSnapshotResponse)
async def comparison_snapshot(
    comparison_id: UUID,
    identity: IdentityDependency,
    database: DatabaseSession,
) -> ComparisonSnapshotResponse:
    try:
        async with database.begin():
            await database.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            generated_at = await database.scalar(select(func.transaction_timestamp()))
            if generated_at is None:
                raise RuntimeError("database snapshot timestamp is unavailable")
            record = await get_comparison_view(
                database,
                user_id=identity.user.id,
                comparison_id=comparison_id,
            )
            contexts = await load_subscription_fare_contexts(
                database,
                user_id=identity.user.id,
                subscription_ids=record.subscription_ids,
            )
            detailed_latest = await load_subscription_latest_fares(
                database,
                contexts=contexts,
            )
            calendar_latest = await load_subscription_latest_calendar_fares(
                database,
                contexts=contexts,
            )
            detailed_analytics = await load_subscription_price_analytics(
                database,
                contexts=contexts,
                as_of=generated_at,
                days=record.view.trend_days,
            )
            calendar_analytics = await load_subscription_calendar_analytics(
                database,
                contexts=contexts,
                as_of=generated_at,
                days=record.view.trend_days,
            )
    except ComparisonNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return ComparisonSnapshotResponse(
        meta=ResponseMeta(generatedAt=generated_at),
        view=_serialize_view(record),
        routes=[
            _serialize_route_snapshot(
                context,
                generated_at=generated_at,
                detailed_latest=detailed_latest.get(context.subscription.id),
                calendar_latest=calendar_latest.get(context.subscription.id),
                detailed_analytics=detailed_analytics.get(context.subscription.id),
                calendar_analytics=calendar_analytics.get(context.subscription.id),
            )
            for context in contexts
        ],
    )


def _serialize_route_snapshot(
    context: SubscriptionFareContext,
    *,
    generated_at: datetime,
    detailed_latest,
    calendar_latest,
    detailed_analytics,
    calendar_analytics,
) -> ComparisonRouteSnapshotPublic:
    outbound = context.legs[0] if context.legs else None
    round_trip = context.search_query.trip_type == "round_trip"
    inbound = context.legs[1] if round_trip and len(context.legs) > 1 else None
    stale_after = timedelta(seconds=max(3600, context.subscription.poll_interval_seconds * 2))
    if detailed_latest is None:
        detailed_status = "unavailable"
    elif generated_at - detailed_latest.observed_at > stale_after:
        detailed_status = "stale"
    else:
        detailed_status = "current"

    calendar_basis = "round_trip_total" if round_trip else "one_way_lowest"
    if calendar_latest is None:
        latest_calendar_price = None
    elif round_trip:
        latest_calendar_price = calendar_latest.total_price_minor
    else:
        latest_calendar_price = calendar_latest.lowest_price_minor

    analytics = detailed_analytics
    calendar_points = calendar_analytics.trend if calendar_analytics is not None else ()
    return ComparisonRouteSnapshotPublic(
        subscriptionId=context.subscription.id,
        name=context.subscription.name,
        enabled=context.subscription.enabled,
        origin=outbound.origin_code if outbound else "",
        destination=outbound.destination_code if outbound else "",
        originName=outbound.origin_code if outbound else "",
        destinationName=outbound.destination_code if outbound else "",
        tripType="roundtrip" if round_trip else "oneway",
        departureDate=outbound.departure_date if outbound else date.min,
        returnDate=inbound.departure_date if inbound else None,
        directOnly=context.search_query.direct_only,
        currency=context.search_query.currency,
        latestDetailedPriceMinor=(
            detailed_latest.total_price_minor if detailed_latest is not None else None
        ),
        detailedPriceStatus=detailed_status,
        detailedObservedAt=(detailed_latest.observed_at if detailed_latest is not None else None),
        periodMinPriceMinor=(analytics.minimum_price_minor if analytics is not None else None),
        periodMaxPriceMinor=(analytics.maximum_price_minor if analytics is not None else None),
        periodAveragePriceMinor=(analytics.average_price_minor if analytics is not None else None),
        periodSampleCount=(analytics.sample_count if analytics is not None else 0),
        changePercent=(analytics.price_change_percent if analytics is not None else None),
        detailedTrend=(
            [_serialize_price_point(point) for point in analytics.trend]
            if analytics is not None
            else []
        ),
        latestCalendarPriceMinor=latest_calendar_price,
        calendarLowestPriceMinor=(
            calendar_latest.lowest_price_minor if calendar_latest is not None else None
        ),
        calendarTotalPriceMinor=(
            calendar_latest.total_price_minor if calendar_latest is not None else None
        ),
        calendarPriceBasis=calendar_basis,
        calendarObservedAt=(calendar_latest.observed_at if calendar_latest is not None else None),
        calendarDirectVerified=(
            calendar_latest.direct_verified if calendar_latest is not None else False
        ),
        calendarTrend=[
            ComparisonCalendarPointPublic(
                **_serialize_price_point(point).model_dump(),
                directVerified=False,
            )
            for point in calendar_points
        ],
    )


def _serialize_price_point(point: HistoryPoint) -> PricePointPublic:
    return PricePointPublic(
        observedAt=point.observed_at,
        priceMinor=point.price_minor,
        lowestPriceMinor=point.lowest_price_minor,
        highestPriceMinor=point.highest_price_minor,
        averagePriceMinor=point.average_price_minor,
        sampleCount=point.sample_count,
    )
