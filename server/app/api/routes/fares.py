from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import DatabaseSession, IdentityDependency, SettingsDependency
from app.api.pagination import (
    BucketCursor,
    DatePairCursor,
    InvalidCursorError,
    OfferCursor,
    RunCursor,
    TimestampCursor,
    decode_bucket_cursor,
    decode_date_pair_cursor,
    decode_offer_cursor,
    decode_run_cursor,
    decode_timestamp_cursor,
    encode_bucket_cursor,
    encode_date_pair_cursor,
    encode_offer_cursor,
    encode_run_cursor,
    encode_timestamp_cursor,
)
from app.api.schemas.fares import (
    CalendarPricePointPublic,
    CalendarPriceResponse,
    CollectionDiagnosticPublic,
    CollectionHealthPublic,
    CollectionOperationsResponse,
    CollectionQueueDepthsPublic,
    CollectionRunCountsPublic,
    CollectionRunListResponse,
    CollectionRunPublic,
    CollectionSchemaSignalPublic,
    CollectionStatePublic,
    DashboardOverviewResponse,
    DashboardStatsPublic,
    FareLegPublic,
    FareOfferPublic,
    FareSearchQueryPublic,
    FareSearchResponse,
    FareSegmentPublic,
    PriceHistoryResponse,
    PricePointPublic,
    ResponseMeta,
    RoutePublic,
)
from app.domain.search import (
    FareSearch,
    PassengerMix,
    SearchFilters,
    SearchLeg,
    TripType,
)
from app.models import (
    CollectionRun,
    FareOffer,
    Itinerary,
    SearchQuery,
    Segment,
    Subscription,
    SubscriptionFilter,
)
from app.models import (
    SearchLeg as SearchLegRow,
)
from app.models.enums import CollectionStatus
from app.repositories.canonical_searches import get_or_create_canonical_search
from app.services.collection_dispatch import dispatch_collection_run_safely
from app.services.collection_operations import load_collection_operations
from app.services.collection_runs import ensure_on_demand_collection_run
from app.services.collection_visibility import visible_collection_run_condition
from app.services.fare_data import (
    FareFilterSpec,
    SubscriptionFareContext,
    SubscriptionLatestFare,
    itinerary_filter_conditions,
    list_latest_calendar_prices,
    load_collection_health,
    load_dashboard_price_analytics,
    load_dashboard_subscription_stats,
    load_price_history,
    load_subscription_fare_context,
    load_subscription_latest_fares,
    resolve_history_resolution,
    validate_calendar_cursor_mode,
    validate_calendar_window,
)

router = APIRouter()


@router.get("/fares/search", response_model=FareSearchResponse)
async def search_fares(
    request: Request,
    identity: IdentityDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
    departure_date: Annotated[date, Query(alias="departureDate")],
    trip_type: Annotated[Literal["oneway", "roundtrip"], Query(alias="tripType")] = "oneway",
    origin: Annotated[str, Query(min_length=3, max_length=3)] = "SHA",
    destination: Annotated[str, Query(min_length=3, max_length=3)] = "TYO",
    return_date: Annotated[date | None, Query(alias="returnDate")] = None,
    direct_only: Annotated[bool, Query(alias="directOnly")] = False,
    passengers: Annotated[int, Query(ge=1, le=9)] = 1,
    airline_codes: Annotated[
        str | None,
        Query(
            alias="airlineCodes",
            max_length=100,
            description="Comma-separated marketing airline codes; any matching segment qualifies",
        ),
    ] = None,
    departure_airports: Annotated[
        str | None,
        Query(
            alias="departureAirports",
            max_length=100,
            description="Comma-separated actual outbound departure airport codes",
        ),
    ] = None,
    arrival_airports: Annotated[
        str | None,
        Query(
            alias="arrivalAirports",
            max_length=100,
            description="Comma-separated final outbound arrival airport codes",
        ),
    ] = None,
    max_price_minor: Annotated[
        int | None, Query(alias="maxPriceMinor", ge=0, le=2_000_000_000)
    ] = None,
    max_stops: Annotated[
        int | None,
        Query(alias="maxStops", ge=0, le=3, description="Maximum total itinerary stops"),
    ] = None,
    max_duration_minutes: Annotated[
        int | None, Query(alias="maxDurationMinutes", ge=30, le=2880)
    ] = None,
    departure_minute_start: Annotated[
        int | None, Query(alias="departureMinuteStart", ge=0, le=1439)
    ] = None,
    departure_minute_end: Annotated[
        int | None, Query(alias="departureMinuteEnd", ge=1, le=1440)
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> FareSearchResponse:
    normalized_trip_type = TripType.ROUND_TRIP if trip_type == "roundtrip" else TripType.ONE_WAY
    legs = [
        SearchLeg(origin=origin, destination=destination, departure_date=departure_date),
    ]
    if normalized_trip_type is TripType.ROUND_TRIP:
        if return_date is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="returnDate is required for roundtrip searches",
            )
        legs.append(
            SearchLeg(
                origin=destination,
                destination=origin,
                departure_date=return_date,
            )
        )
    try:
        search = FareSearch(
            trip_type=normalized_trip_type,
            legs=tuple(legs),
            passengers=PassengerMix(adults=passengers),
            filters=SearchFilters(
                direct_only=direct_only,
                airline_codes=_split_codes(airline_codes),
                departure_airports=_split_codes(departure_airports),
                arrival_airports=_split_codes(arrival_airports),
                max_price_minor=max_price_minor,
                max_stops=max_stops,
                max_duration_minutes=max_duration_minutes,
                departure_minute_start=departure_minute_start,
                departure_minute_end=departure_minute_end,
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    filter_key = _offer_filter_key(search.filters)
    decoded_offer_cursor: OfferCursor | None = None
    if cursor is not None:
        try:
            decoded_offer_cursor = decode_offer_cursor(cursor)
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        if decoded_offer_cursor.filter_key != filter_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pagination cursor does not match the fare filters",
            )

    async with database.begin():
        search_query, _ = await get_or_create_canonical_search(database, search)
        run = await ensure_on_demand_collection_run(
            database,
            search_query=search_query,
            user_id=identity.user.id,
        )

    await dispatch_collection_run_safely(
        request.app.state.session_factory,
        run_id=run.id,
        lease_seconds=settings.collection_dispatch_lease_seconds,
        realtime_settings=settings,
    )
    await database.refresh(run)

    if decoded_offer_cursor is not None:
        latest_run = await database.scalar(
            select(CollectionRun).where(
                CollectionRun.id == decoded_offer_cursor.run_id,
                CollectionRun.search_query_id == search_query.id,
                CollectionRun.status == CollectionStatus.SUCCEEDED.value,
                CollectionRun.finished_at.is_not(None),
            )
        )
        if latest_run is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pagination cursor does not match this fare search",
            )
    else:
        latest_run = await database.scalar(
            select(CollectionRun)
            .where(
                CollectionRun.search_query_id == search_query.id,
                CollectionRun.status == CollectionStatus.SUCCEEDED.value,
                CollectionRun.finished_at.is_not(None),
            )
            .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
            .limit(1)
        )
    offers, has_more, total = await _load_offers(
        database,
        latest_run.id if latest_run else None,
        filters=search.filters,
        provider=search.provider,
        currency=search.currency,
        after=decoded_offer_cursor,
        limit=limit,
    )
    next_cursor = None
    if has_more and latest_run is not None and offers:
        last_offer = offers[-1]
        next_cursor = encode_offer_cursor(
            OfferCursor(
                run_id=latest_run.id,
                price_minor=last_offer.total_price_minor,
                row_id=last_offer.id,
                filter_key=filter_key,
            )
        )
    return FareSearchResponse(
        meta=ResponseMeta(generatedAt=datetime.now(UTC)),
        query=FareSearchQueryPublic(
            tripType=trip_type,
            origin=search.legs[0].origin,
            destination=search.legs[0].destination,
            departureDate=search.legs[0].departure_date,
            returnDate=search.legs[1].departure_date if len(search.legs) > 1 else None,
            directOnly=direct_only,
            passengers=passengers,
        ),
        offers=offers,
        total=total,
        hasMore=has_more,
        nextCursor=next_cursor,
        collection=CollectionStatePublic(
            status=run.status,
            runId=run.id,
            scheduledAt=run.scheduled_at,
            finishedAt=run.finished_at,
            errorCode=run.error_code,
        ),
    )


@router.get("/prices/history", response_model=PriceHistoryResponse)
async def price_history(
    identity: IdentityDependency,
    database: DatabaseSession,
    route_id: Annotated[UUID, Query(alias="routeId")],
    days: Annotated[int, Query(ge=1, le=365)] = 90,
    resolution: Annotated[Literal["auto", "raw", "hour", "day"], Query()] = "auto",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> PriceHistoryResponse:
    context = await load_subscription_fare_context(
        database,
        user_id=identity.user.id,
        subscription_id=route_id,
    )
    if context is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="route not found")

    resolved_resolution = resolve_history_resolution(resolution, days=days)
    as_of = datetime.now(UTC)
    decoded_cursor: TimestampCursor | BucketCursor | None = None
    if cursor is not None:
        try:
            decoded_cursor = (
                decode_timestamp_cursor(cursor)
                if resolved_resolution == "raw"
                else decode_bucket_cursor(cursor)
            )
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        as_of = decoded_cursor.as_of
        cursor_timestamp = (
            decoded_cursor.timestamp
            if isinstance(decoded_cursor, TimestampCursor)
            else decoded_cursor.bucket
        )
        if cursor_timestamp > as_of:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pagination cursor is outside its history snapshot",
            )
        if (
            isinstance(decoded_cursor, BucketCursor)
            and decoded_cursor.resolution != resolved_resolution
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pagination cursor resolution does not match the request",
            )

    try:
        since = as_of - timedelta(days=days)
    except OverflowError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pagination cursor has an invalid history snapshot",
        ) from error

    page = await load_price_history(
        database,
        context=context,
        since=since,
        as_of=as_of,
        resolution=resolved_resolution,
        limit=limit,
        after=decoded_cursor,
    )
    points = [
        PricePointPublic(
            observedAt=item.observed_at,
            priceMinor=item.price_minor,
            lowestPriceMinor=item.lowest_price_minor,
            highestPriceMinor=item.highest_price_minor,
            averagePriceMinor=item.average_price_minor,
            sampleCount=item.sample_count,
        )
        for item in page.items
    ]
    next_cursor = None
    if page.has_more and page.items:
        last = page.items[-1]
        if resolved_resolution == "raw" and last.row_id is not None:
            next_cursor = encode_timestamp_cursor(
                TimestampCursor(
                    as_of=as_of,
                    timestamp=last.observed_at,
                    row_id=last.row_id,
                )
            )
        elif resolved_resolution in ("hour", "day"):
            next_cursor = encode_bucket_cursor(
                BucketCursor(
                    as_of=as_of,
                    bucket=last.observed_at,
                    resolution=resolved_resolution,
                )
            )
    latest_fares = await load_subscription_latest_fares(database, contexts=(context,))
    generated_at = datetime.now(UTC)
    return PriceHistoryResponse(
        meta=ResponseMeta(generatedAt=generated_at),
        route=await _route_public(
            database,
            context.search_query,
            context.subscription,
            latest_fare=latest_fares.get(context.subscription.id),
            generated_at=generated_at,
            legs=list(context.legs),
        ),
        points=points,
        minPriceMinor=page.minimum_price_minor,
        maxPriceMinor=page.maximum_price_minor,
        averagePriceMinor=page.average_price_minor,
        sampleCount=page.sample_count,
        resolution=resolved_resolution,
        hasMore=page.has_more,
        nextCursor=next_cursor,
    )


@router.get("/prices/calendar", response_model=CalendarPriceResponse)
async def calendar_prices(
    identity: IdentityDependency,
    database: DatabaseSession,
    route_id: Annotated[UUID, Query(alias="routeId")],
    departure_start: Annotated[date | None, Query(alias="departureStart")] = None,
    departure_end: Annotated[date | None, Query(alias="departureEnd")] = None,
    return_start: Annotated[date | None, Query(alias="returnStart")] = None,
    return_end: Annotated[date | None, Query(alias="returnEnd")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> CalendarPriceResponse:
    context = await load_subscription_fare_context(
        database,
        user_id=identity.user.id,
        subscription_id=route_id,
    )
    if context is None or not context.legs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="route not found")

    is_round_trip = context.search_query.trip_type == "round_trip"
    outbound_date = context.legs[0].departure_date
    departure_start = departure_start or outbound_date
    departure_end = departure_end or departure_start + timedelta(days=180)
    if is_round_trip:
        default_return_date = (
            context.legs[1].departure_date if len(context.legs) > 1 else departure_start
        )
        return_start = return_start or default_return_date
        return_end = return_end or return_start + timedelta(days=180)
    elif return_start is not None or return_end is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="return date bounds are only valid for roundtrip routes",
        )

    try:
        validate_calendar_window(departure_start, departure_end)
        if return_start is not None and return_end is not None:
            validate_calendar_window(return_start, return_end)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    decoded_cursor: DatePairCursor | None = None
    if cursor is not None:
        try:
            decoded_cursor = decode_date_pair_cursor(cursor)
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        try:
            validate_calendar_cursor_mode(decoded_cursor, round_trip=is_round_trip)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error

    page = await list_latest_calendar_prices(
        database,
        search_query_id=context.search_query.id,
        currency=context.search_query.currency,
        round_trip=is_round_trip,
        departure_start=departure_start,
        departure_end=departure_end,
        return_start=return_start,
        return_end=return_end,
        after=decoded_cursor,
        limit=limit,
    )
    next_cursor = None
    if page.has_more and page.items:
        last = page.items[-1]
        next_cursor = encode_date_pair_cursor(
            DatePairCursor(
                departure_date=last.departure_date,
                return_date=last.return_date,
            )
        )
    latest_fares = await load_subscription_latest_fares(database, contexts=(context,))
    generated_at = datetime.now(UTC)
    return CalendarPriceResponse(
        meta=ResponseMeta(generatedAt=generated_at),
        route=await _route_public(
            database,
            context.search_query,
            context.subscription,
            latest_fare=latest_fares.get(context.subscription.id),
            generated_at=generated_at,
            legs=list(context.legs),
        ),
        points=[
            CalendarPricePointPublic(
                departureDate=item.departure_date,
                returnDate=item.return_date,
                currency=item.currency,
                lowestPriceMinor=item.lowest_price_minor,
                totalPriceMinor=item.total_price_minor,
                observedAt=item.observed_at,
                directVerified=item.direct_verified,
            )
            for item in page.items
        ],
        hasMore=page.has_more,
        nextCursor=next_cursor,
    )


@router.get("/collection/runs", response_model=CollectionRunListResponse)
async def collection_runs(
    identity: IdentityDependency,
    database: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> CollectionRunListResponse:
    decoded_cursor: RunCursor | None = None
    if cursor is not None:
        try:
            decoded_cursor = decode_run_cursor(cursor)
        except InvalidCursorError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
    statement = select(CollectionRun).where(visible_collection_run_condition(identity.user.id))
    if decoded_cursor is not None:
        statement = statement.where(
            tuple_(CollectionRun.scheduled_at, CollectionRun.id)
            < tuple_(decoded_cursor.scheduled_at, decoded_cursor.row_id)
        )
    runs = (
        await database.scalars(
            statement.order_by(CollectionRun.scheduled_at.desc(), CollectionRun.id.desc()).limit(
                limit + 1
            )
        )
    ).all()
    has_more = len(runs) > limit
    runs = runs[:limit]
    query_map = {
        row.id: row
        for row in (
            await database.scalars(
                select(SearchQuery).where(SearchQuery.id.in_({run.search_query_id for run in runs}))
            )
        ).all()
    }
    now = datetime.now(UTC)
    health = await load_collection_health(
        database,
        user_id=identity.user.id,
        now=now,
    )
    next_cursor = None
    if has_more and runs:
        last_run = runs[-1]
        next_cursor = encode_run_cursor(
            RunCursor(scheduled_at=last_run.scheduled_at, row_id=last_run.id)
        )
    return CollectionRunListResponse(
        meta=ResponseMeta(generatedAt=now),
        items=[
            CollectionRunPublic(
                id=run.id,
                queryLabel=_query_label(query_map.get(run.search_query_id)),
                provider=query_map.get(run.search_query_id).provider
                if query_map.get(run.search_query_id)
                else "unknown",
                status=_public_run_status(run.status),
                startedAt=run.started_at or run.scheduled_at,
                finishedAt=run.finished_at,
                observations=run.offer_count,
                calendarObservations=_run_count(run, "calendar_count"),
                itineraries=run.itinerary_count,
                offers=run.offer_count,
                attempt=run.attempt,
                maxAttempts=run.max_attempts,
                upstreamStatus=run.upstream_status,
                warningCode=_run_warning_code(run),
                schemaFingerprint=run.schema_fingerprint,
                diagnostics=_run_diagnostics(run),
                durationMs=_duration_ms(run),
                errorCode=run.error_code,
            )
            for run in runs
        ],
        health=CollectionHealthPublic(
            lastSuccessAt=health.last_success_at,
            successRate24h=health.success_rate_24h,
            nextScheduledAt=health.next_scheduled_at,
        ),
        hasMore=has_more,
        nextCursor=next_cursor,
    )


@router.get("/collection/operations", response_model=CollectionOperationsResponse)
async def collection_operations(
    identity: IdentityDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CollectionOperationsResponse:
    snapshot = await load_collection_operations(
        database,
        user_id=identity.user.id,
        redis_url=settings.redis_url,
    )
    return CollectionOperationsResponse(
        meta=ResponseMeta(generatedAt=snapshot.generated_at),
        runs=CollectionRunCountsPublic(
            ready=snapshot.run_counts.ready,
            retrying=snapshot.run_counts.retrying,
            leased=snapshot.run_counts.leased,
            running=snapshot.run_counts.running,
            failed24h=snapshot.run_counts.failed_24h,
        ),
        queues=CollectionQueueDepthsPublic(
            available=snapshot.queue_depths.available,
            collector=snapshot.queue_depths.collector,
            default=snapshot.queue_depths.default,
            analysis=snapshot.queue_depths.analysis,
            notifications=snapshot.queue_depths.notifications,
        ),
        schemas=[
            CollectionSchemaSignalPublic(
                provider=item.provider,
                endpoint=item.endpoint,
                schemaFingerprint=item.schema_fingerprint,
                topLevelFields=list(item.top_level_fields),
                firstSeenAt=item.first_seen_at,
                lastSeenAt=item.last_seen_at,
                occurrenceCount=item.occurrence_count,
                state=item.state,
            )
            for item in snapshot.schema_signals
        ],
    )


@router.get("/dashboard/overview", response_model=DashboardOverviewResponse)
async def dashboard_overview(
    identity: IdentityDependency,
    database: DatabaseSession,
) -> DashboardOverviewResponse:
    subscriptions = (
        await database.scalars(
            select(Subscription)
            .where(Subscription.user_id == identity.user.id)
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
            .limit(100)
        )
    ).all()
    query_ids = {subscription.search_query_id for subscription in subscriptions}
    queries = (
        (await database.scalars(select(SearchQuery).where(SearchQuery.id.in_(query_ids)))).all()
        if query_ids
        else []
    )
    query_map = {query.id: query for query in queries}
    filters = (
        (
            await database.scalars(
                select(SubscriptionFilter).where(
                    SubscriptionFilter.subscription_id.in_(
                        {subscription.id for subscription in subscriptions}
                    )
                )
            )
        ).all()
        if subscriptions
        else []
    )
    filter_by_subscription = {item.subscription_id: item for item in filters}
    search_legs = (
        (
            await database.scalars(
                select(SearchLegRow)
                .where(SearchLegRow.search_query_id.in_(query_ids))
                .order_by(SearchLegRow.search_query_id, SearchLegRow.position)
            )
        ).all()
        if query_ids
        else []
    )
    legs_by_query: dict[UUID, list[SearchLegRow]] = defaultdict(list)
    for search_leg in search_legs:
        legs_by_query[search_leg.search_query_id].append(search_leg)
    contexts = tuple(
        SubscriptionFareContext(
            subscription=subscription,
            search_query=query_map[subscription.search_query_id],
            filters=filter_by_subscription[subscription.id],
            legs=tuple(legs_by_query.get(subscription.search_query_id, [])),
        )
        for subscription in subscriptions
        if subscription.search_query_id in query_map and subscription.id in filter_by_subscription
    )
    latest_fares = await load_subscription_latest_fares(database, contexts=contexts)
    generated_at = datetime.now(UTC)
    analytics = await load_dashboard_price_analytics(
        database,
        contexts=contexts,
        as_of=generated_at,
    )
    subscription_stats = await load_dashboard_subscription_stats(
        database,
        user_id=identity.user.id,
    )
    collection_health = await load_collection_health(
        database,
        user_id=identity.user.id,
        now=generated_at,
    )
    routes = [
        await _route_public(
            database,
            context.search_query,
            context.subscription,
            latest_fare=latest_fares.get(context.subscription.id),
            generated_at=generated_at,
            legs=list(context.legs),
        )
        for context in contexts
    ]
    priced_routes = [route for route in routes if route.latest_price_minor is not None]
    currencies = {route.currency for route in priced_routes}
    latest_prices = [route.latest_price_minor for route in priced_routes]
    return DashboardOverviewResponse(
        meta=ResponseMeta(generatedAt=generated_at),
        stats=DashboardStatsPublic(
            lowestPriceMinor=(
                min(latest_prices) if latest_prices and len(currencies) == 1 else None
            ),
            priceChangePercent=analytics.price_change_percent,
            activeSubscriptions=subscription_stats.active_subscriptions,
            routesTracked=subscription_stats.routes_tracked,
            collectionSuccessRate=collection_health.success_rate_24h,
        ),
        trend=[
            PricePointPublic(
                observedAt=item.observed_at,
                priceMinor=item.price_minor,
                lowestPriceMinor=item.lowest_price_minor,
                highestPriceMinor=item.highest_price_minor,
                averagePriceMinor=item.average_price_minor,
                sampleCount=item.sample_count,
            )
            for item in analytics.trend
        ],
        routes=routes,
    )


async def _load_offers(
    database: AsyncSession,
    run_id: UUID | None,
    *,
    filters: SearchFilters,
    provider: str,
    currency: str,
    after: OfferCursor | None,
    limit: int,
) -> tuple[list[FareOfferPublic], bool, int]:
    if run_id is None:
        return [], False, 0
    filter_spec = FareFilterSpec.from_search_filters(filters)
    conditions = list(itinerary_filter_conditions(filter_spec))
    if filter_spec.max_price_minor is not None:
        conditions.append(FareOffer.total_price_minor <= filter_spec.max_price_minor)
    total = await database.scalar(
        select(func.count())
        .select_from(FareOffer)
        .join(Itinerary, Itinerary.id == FareOffer.itinerary_id)
        .where(
            FareOffer.collection_run_id == run_id,
            FareOffer.currency == currency,
            *conditions,
        )
    )
    page_conditions = list(conditions)
    if after is not None:
        page_conditions.append(
            tuple_(FareOffer.total_price_minor, FareOffer.id)
            > tuple_(after.price_minor, after.row_id)
        )
    rows = (
        await database.execute(
            select(FareOffer, Itinerary)
            .join(Itinerary, Itinerary.id == FareOffer.itinerary_id)
            .where(
                FareOffer.collection_run_id == run_id,
                FareOffer.currency == currency,
                *page_conditions,
            )
            .order_by(FareOffer.total_price_minor, FareOffer.id)
            .limit(limit + 1)
        )
    ).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    itinerary_ids = {itinerary.id for _, itinerary in rows}
    segment_rows = (
        (
            await database.scalars(
                select(Segment)
                .where(Segment.itinerary_id.in_(itinerary_ids))
                .order_by(Segment.itinerary_id, Segment.position)
            )
        ).all()
        if itinerary_ids
        else []
    )
    segments_by_itinerary: dict[UUID, list[Segment]] = defaultdict(list)
    for segment in segment_rows:
        segments_by_itinerary[segment.itinerary_id].append(segment)
    result: list[FareOfferPublic] = []
    for offer, itinerary in rows:
        segments = segments_by_itinerary.get(itinerary.id, [])
        by_leg: dict[int, list[Segment]] = defaultdict(list)
        for segment in segments:
            by_leg[segment.leg_position].append(segment)
        legs = []
        for leg_position, leg_segments in sorted(by_leg.items()):
            legs.append(_fare_leg_public(leg_position, leg_segments))
        result.append(
            FareOfferPublic(
                id=offer.id,
                totalPriceMinor=offer.total_price_minor,
                currency=offer.currency,
                cabin=offer.cabin,
                legs=legs,
                provider=provider,
                observedAt=offer.created_at,
            )
        )
    return result, has_more, int(total or 0)


def _fare_leg_public(leg_position: int, segments: list[Segment]) -> FareLegPublic:
    first = segments[0]
    last = segments[-1]
    elapsed_minutes = int(
        (last.arrival_at_utc - first.departure_at_utc).total_seconds() // 60
    )
    segment_payloads = [_fare_segment_public(segment) for segment in segments]
    technical_stops = sum(segment.technical_stop_count for segment in segment_payloads)
    return FareLegPublic(
        direction="outbound" if leg_position == 0 else "inbound",
        flightNumber=first.flight_number,
        airline=first.marketing_airline_code,
        origin=first.origin_airport_code,
        originName=segment_payloads[0].origin_name,
        destination=last.destination_airport_code,
        destinationName=segment_payloads[-1].destination_name,
        departureAt=first.departure_at_utc,
        arrivalAt=last.arrival_at_utc,
        stops=max(0, len(segments) - 1) + technical_stops,
        durationMinutes=(
            elapsed_minutes
            if elapsed_minutes > 0
            else sum(segment.duration_minutes for segment in segments)
        ),
        segments=segment_payloads,
    )


def _fare_segment_public(segment: Segment) -> FareSegmentPublic:
    metadata = segment.segment_metadata or {}
    technical_stop_count = metadata.get("technical_stop_count")
    try:
        parsed_technical_stop_count = max(0, int(technical_stop_count or 0))
    except (TypeError, ValueError):
        parsed_technical_stop_count = 0
    return FareSegmentPublic(
        position=segment.position,
        flightNumber=segment.flight_number,
        operatingFlightNumber=_metadata_text(metadata, "operating_flight_number"),
        airline=segment.marketing_airline_code,
        airlineName=_metadata_text(metadata, "marketing_airline_name"),
        origin=segment.origin_airport_code,
        originName=(
            _metadata_text(metadata, "departure_airport_name")
            or segment.origin_airport_code
        ),
        originTerminal=_metadata_text(metadata, "departure_terminal"),
        destination=segment.destination_airport_code,
        destinationName=(
            _metadata_text(metadata, "arrival_airport_name")
            or segment.destination_airport_code
        ),
        destinationTerminal=_metadata_text(metadata, "arrival_terminal"),
        departureAt=segment.departure_at_utc,
        arrivalAt=segment.arrival_at_utc,
        departureLocal=segment.departure_local,
        arrivalLocal=segment.arrival_local,
        departureTimezone=segment.departure_timezone,
        arrivalTimezone=segment.arrival_timezone,
        durationMinutes=segment.duration_minutes,
        technicalStopCount=parsed_technical_stop_count,
        aircraftCode=segment.aircraft_code,
    )


def _metadata_text(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:160] or None


def _split_codes(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _offer_filter_key(filters: SearchFilters) -> str:
    payload = json.dumps(
        filters.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _route_public(
    database: AsyncSession,
    query: SearchQuery,
    subscription: Subscription,
    *,
    latest_fare: SubscriptionLatestFare | None,
    generated_at: datetime,
    legs: list[SearchLegRow] | None = None,
) -> RoutePublic:
    if legs is None:
        legs = list(
            (
                await database.scalars(
                    select(SearchLegRow)
                    .where(SearchLegRow.search_query_id == query.id)
                    .order_by(SearchLegRow.position)
                    .limit(2)
                )
            ).all()
        )
    outbound = legs[0] if legs else None
    stale_after = timedelta(seconds=max(3600, subscription.poll_interval_seconds * 2))
    if latest_fare is None:
        price_status: Literal["current", "stale", "unavailable"] = "unavailable"
    elif generated_at - latest_fare.observed_at > stale_after:
        price_status = "stale"
    else:
        price_status = "current"
    return RoutePublic(
        id=subscription.id,
        origin=outbound.origin_code if outbound else "",
        destination=outbound.destination_code if outbound else "",
        originName=outbound.origin_code if outbound else "",
        destinationName=outbound.destination_code if outbound else "",
        tripType="roundtrip" if query.trip_type == "round_trip" else "oneway",
        directOnly=query.direct_only,
        currency=query.currency,
        latestPriceMinor=latest_fare.total_price_minor if latest_fare else None,
        priceStatus=price_status,
        changePercent=None,
        observedAt=latest_fare.observed_at if latest_fare else None,
    )


def _query_label(query: SearchQuery | None) -> str:
    if query is None:
        return "Unknown search"
    return f"{query.provider} · {query.trip_type} · {query.query_hash[:8]}"


def _duration_ms(run: CollectionRun) -> int | None:
    if run.started_at is None or run.finished_at is None:
        return None
    return max(0, int((run.finished_at - run.started_at).total_seconds() * 1000))


def _run_count(run: CollectionRun, key: str) -> int:
    value = (run.run_metadata or {}).get(key, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _run_warning_code(run: CollectionRun) -> str | None:
    partial = (run.run_metadata or {}).get("partial_data")
    if run.upstream_status == "success_with_warnings" and isinstance(partial, dict):
        return "partial_fare_data"
    return None


def _run_diagnostics(run: CollectionRun) -> list[CollectionDiagnosticPublic]:
    metadata = run.run_metadata or {}
    candidates: list[tuple[object, Literal["warning", "error"]]] = []
    diagnostics = metadata.get("diagnostics")
    if isinstance(diagnostics, list):
        candidates.extend((item, "warning") for item in diagnostics)
    failure = metadata.get("failure")
    if isinstance(failure, dict) and isinstance(failure.get("diagnostics"), list):
        candidates.extend((item, "error") for item in failure["diagnostics"])

    result: list[CollectionDiagnosticPublic] = []
    seen: set[tuple[str, str, str | None]] = set()
    for value, default_severity in reversed(candidates):
        if not isinstance(value, dict):
            continue
        code = str(value.get("code") or value.get("kind") or "diagnostic")[:120]
        message = str(value.get("message") or code)[:500]
        path_value = value.get("path")
        path = str(path_value)[:240] if path_value is not None else None
        key = (code, message, path)
        if key in seen:
            continue
        seen.add(key)
        severity = "error" if value.get("severity") == "error" else default_severity
        retryable_value = value.get("retryable")
        retryable = _optional_bool(retryable_value)
        observed_type_value = value.get("observed_type")
        result.append(
            CollectionDiagnosticPublic(
                code=code,
                message=message,
                severity=severity,
                path=path,
                observedType=(
                    str(observed_type_value)[:120] if observed_type_value is not None else None
                ),
                retryable=retryable,
            )
        )
        if len(result) >= 20:
            break
    result.reverse()
    return result


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _public_run_status(value: str) -> Literal["success", "running", "failed", "blocked"]:
    if value == CollectionStatus.SUCCEEDED.value:
        return "success"
    if value == CollectionStatus.FAILED.value:
        return "failed"
    if value == CollectionStatus.CANCELED.value:
        return "blocked"
    return "running"
