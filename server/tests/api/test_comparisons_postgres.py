from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException, status
from sqlalchemy import delete, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.dependencies import CurrentIdentity, get_current_identity, get_database_session
from app.db.partitions import calendar_price_observation_partition_ddl
from app.main import create_app
from app.models import (
    AuditEvent,
    CalendarPriceObservation,
    CollectionRun,
    LatestCalendarPriceSnapshot,
    Provider,
    SearchLeg,
    SearchQuery,
    Subscription,
    SubscriptionFilter,
    User,
    UserSession,
)
from app.settings import Settings, get_settings

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_comparison_http_owner_csrf_snapshot_and_constant_query_count() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        owner, owner_session, owner_subscriptions = await _seed_api_owner(
            session,
            prefix="comparison-api-owner",
            route_count=8,
        )
        outsider, outsider_session, outsider_subscriptions = await _seed_api_owner(
            session,
            prefix="comparison-api-outsider",
            route_count=2,
        )
        owner_query_ids = tuple(item.search_query_id for item in owner_subscriptions)
        outsider_query_ids = tuple(item.search_query_id for item in outsider_subscriptions)

    settings = Settings()
    app = create_app()

    async def database_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    statements: list[str] = []

    def track_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        normalized = statement.lstrip().upper()
        if normalized.startswith(("SET TRANSACTION", "SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", track_statement)
    current_identity: dict[str, CurrentIdentity | None] = {"value": None}

    async def identity_override() -> CurrentIdentity:
        identity = current_identity["value"]
        if identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        return identity

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.get("/api/comparisons")
            assert unauthenticated.status_code == 401

        app.dependency_overrides[get_current_identity] = identity_override
        current_identity["value"] = CurrentIdentity(user=owner, session=owner_session)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            no_csrf = await client.post(
                "/api/comparisons",
                json=_create_payload(
                    "No CSRF",
                    [item.id for item in owner_subscriptions[:2]],
                    "comparison-api-no-csrf",
                ),
            )
            assert no_csrf.status_code == 403

        csrf_value = "comparison-api-csrf"
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={settings.csrf_cookie_name: csrf_value},
            headers={"x-csrf-token": csrf_value},
        ) as client:
            two_response = await client.post(
                "/api/comparisons",
                json=_create_payload(
                    "Two routes",
                    [item.id for item in owner_subscriptions[:2]],
                    "comparison-api-two",
                ),
            )
            assert two_response.status_code == 201
            two_id = two_response.json()["id"]
            assert two_response.json()["activeRouteCount"] == 2

            replay = await client.post(
                "/api/comparisons",
                json=_create_payload(
                    "Two routes",
                    [item.id for item in owner_subscriptions[:2]],
                    "comparison-api-two",
                ),
            )
            assert replay.status_code == 201
            assert replay.json()["id"] == two_id

            eight_response = await client.post(
                "/api/comparisons",
                json=_create_payload(
                    "Eight routes",
                    [item.id for item in owner_subscriptions],
                    "comparison-api-eight",
                ),
            )
            assert eight_response.status_code == 201
            eight_id = eight_response.json()["id"]

            foreign_route = await client.post(
                "/api/comparisons",
                json=_create_payload(
                    "Foreign route",
                    [owner_subscriptions[0].id, outsider_subscriptions[0].id],
                    "comparison-api-foreign-route",
                ),
            )
            assert foreign_route.status_code == 404

            listing = await client.get("/api/comparisons")
            assert listing.status_code == 200
            assert {item["id"] for item in listing.json()["items"]} == {two_id, eight_id}

            for comparison_id, expected_routes in ((two_id, 2), (eight_id, 8)):
                statements.clear()
                snapshot = await client.get(f"/api/comparisons/{comparison_id}/snapshot")
                assert snapshot.status_code == 200, snapshot.text
                assert len(snapshot.json()["routes"]) == expected_routes
                assert snapshot.json()["view"]["activeRouteCount"] == expected_routes
                assert len(statements) == 8

            current_identity["value"] = CurrentIdentity(
                user=outsider,
                session=outsider_session,
            )
            assert (await client.get(f"/api/comparisons/{two_id}")).status_code == 404
            assert (await client.get(f"/api/comparisons/{two_id}/snapshot")).status_code == 404
            outsider_put = await client.put(
                f"/api/comparisons/{two_id}",
                json={
                    "name": "Outsider update",
                    "subscriptionIds": [str(item.id) for item in outsider_subscriptions],
                    "trendDays": 30,
                    "expectedVersion": 1,
                },
            )
            assert outsider_put.status_code == 404
            assert (await client.delete(f"/api/comparisons/{two_id}")).status_code == 404

            current_identity["value"] = CurrentIdentity(user=owner, session=owner_session)
            updated = await client.put(
                f"/api/comparisons/{two_id}",
                json={
                    "name": "Two routes updated",
                    "subscriptionIds": [str(item.id) for item in reversed(owner_subscriptions[:2])],
                    "trendDays": 90,
                    "expectedVersion": 1,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["version"] == 2
            stale = await client.put(
                f"/api/comparisons/{two_id}",
                json={
                    "name": "Stale update",
                    "subscriptionIds": [str(item.id) for item in owner_subscriptions[:2]],
                    "trendDays": 7,
                    "expectedVersion": 1,
                },
            )
            assert stale.status_code == 409
            assert (await client.delete(f"/api/comparisons/{two_id}")).status_code == 204
            assert (await client.get(f"/api/comparisons/{two_id}")).status_code == 404
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", track_statement)
        app.dependency_overrides.clear()
        async with factory() as session, session.begin():
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_user_id.in_((owner.id, outsider.id)))
            )
            await session.execute(delete(User).where(User.id.in_((owner.id, outsider.id))))
            await session.execute(
                delete(SearchQuery).where(SearchQuery.id.in_(owner_query_ids + outsider_query_ids))
            )
        await engine.dispose()


async def test_comparison_snapshot_calendar_json_preserves_price_semantics() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    as_of = datetime.now(UTC).replace(microsecond=0)
    async with engine.begin() as connection:
        await connection.execute(text(calendar_price_observation_partition_ddl(as_of)))

    async with factory() as session, session.begin():
        owner, owner_session, subscriptions = await _seed_api_owner(
            session,
            prefix="comparison-api-calendar",
            route_count=2,
            round_trip_positions={1},
        )
        query_ids = tuple(item.search_query_id for item in subscriptions)
        provider = Provider(
            code=f"cmp-api-{uuid4().hex}",
            display_name="Comparison API calendar fixture",
            enabled=True,
            adapter_version="test",
        )
        session.add(provider)
        await session.flush()
        runs: list[CollectionRun] = []
        for index, subscription in enumerate(subscriptions):
            observed_at = as_of - timedelta(hours=index + 1)
            run = CollectionRun(
                search_query_id=subscription.search_query_id,
                provider_id=provider.id,
                idempotency_key=f"comparison-api-calendar:{uuid4().hex}",
                status="succeeded",
                attempt=1,
                max_attempts=3,
                scheduled_at=observed_at,
                started_at=observed_at,
                finished_at=observed_at,
                offer_count=0,
                itinerary_count=0,
                run_metadata={"fixture": "comparison-api-calendar"},
            )
            session.add(run)
            await session.flush()
            runs.append(run)

        one_way_departure = date(2026, 9, 10)
        round_trip_departure = date(2026, 9, 11)
        round_trip_return = date(2026, 9, 18)
        session.add_all(
            (
                LatestCalendarPriceSnapshot(
                    search_query_id=subscriptions[0].search_query_id,
                    collection_run_id=runs[0].id,
                    provider_id=provider.id,
                    departure_date=one_way_departure,
                    return_date=None,
                    currency="CNY",
                    lowest_price_minor=160_000,
                    total_price_minor=320_000,
                    observed_at=as_of - timedelta(hours=1),
                    source_endpoint="fixture",
                    direct_verified=True,
                ),
                LatestCalendarPriceSnapshot(
                    search_query_id=subscriptions[1].search_query_id,
                    collection_run_id=runs[1].id,
                    provider_id=provider.id,
                    departure_date=round_trip_departure,
                    return_date=round_trip_return,
                    currency="CNY",
                    lowest_price_minor=170_000,
                    total_price_minor=None,
                    observed_at=as_of - timedelta(hours=2),
                    source_endpoint="fixture",
                    direct_verified=False,
                ),
                _api_calendar_observation(
                    query_id=subscriptions[0].search_query_id,
                    run_id=runs[0].id,
                    provider_id=provider.id,
                    departure_date=one_way_departure,
                    return_date=None,
                    observed_at=as_of - timedelta(days=2),
                    lowest=155_000,
                    total=310_000,
                ),
                _api_calendar_observation(
                    query_id=subscriptions[1].search_query_id,
                    run_id=runs[1].id,
                    provider_id=provider.id,
                    departure_date=round_trip_departure,
                    return_date=round_trip_return,
                    observed_at=as_of - timedelta(days=2),
                    lowest=165_000,
                    total=None,
                ),
                _api_calendar_observation(
                    query_id=subscriptions[1].search_query_id,
                    run_id=runs[1].id,
                    provider_id=provider.id,
                    departure_date=round_trip_departure,
                    return_date=round_trip_return,
                    observed_at=as_of - timedelta(days=1),
                    lowest=175_000,
                    total=350_000,
                ),
            )
        )

    settings = Settings()
    app = create_app()

    async def database_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def identity_override() -> CurrentIdentity:
        return CurrentIdentity(user=owner, session=owner_session)

    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_identity] = identity_override
    transport = httpx.ASGITransport(app=app)
    csrf_value = "comparison-api-calendar-csrf"
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={settings.csrf_cookie_name: csrf_value},
            headers={"x-csrf-token": csrf_value},
        ) as client:
            created = await client.post(
                "/api/comparisons",
                json=_create_payload(
                    "Calendar semantics",
                    [subscription.id for subscription in subscriptions],
                    "comparison-api-calendar-create",
                ),
            )
            assert created.status_code == 201, created.text
            snapshot = await client.get(f"/api/comparisons/{created.json()['id']}/snapshot")
            assert snapshot.status_code == 200, snapshot.text

        routes = {route["subscriptionId"]: route for route in snapshot.json()["routes"]}
        one_way = routes[str(subscriptions[0].id)]
        assert one_way["latestCalendarPriceMinor"] == 160_000
        assert one_way["calendarLowestPriceMinor"] == 160_000
        assert one_way["calendarTotalPriceMinor"] == 320_000
        assert one_way["calendarPriceBasis"] == "one_way_lowest"
        assert one_way["calendarDirectVerified"] is True
        assert [point["priceMinor"] for point in one_way["calendarTrend"]] == [155_000]
        assert all(point["directVerified"] is False for point in one_way["calendarTrend"])

        round_trip = routes[str(subscriptions[1].id)]
        assert round_trip["latestCalendarPriceMinor"] is None
        assert round_trip["calendarLowestPriceMinor"] == 170_000
        assert round_trip["calendarTotalPriceMinor"] is None
        assert round_trip["calendarPriceBasis"] == "round_trip_total"
        assert round_trip["calendarDirectVerified"] is False
        assert [point["priceMinor"] for point in round_trip["calendarTrend"]] == [350_000]
        assert all(point["directVerified"] is False for point in round_trip["calendarTrend"])
    finally:
        app.dependency_overrides.clear()
        async with factory() as session, session.begin():
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == owner.id))
            await session.execute(delete(User).where(User.id == owner.id))
            await session.execute(
                delete(CollectionRun).where(CollectionRun.search_query_id.in_(query_ids))
            )
            await session.execute(delete(SearchQuery).where(SearchQuery.id.in_(query_ids)))
            await session.execute(delete(Provider).where(Provider.id == provider.id))
        await engine.dispose()


async def _seed_api_owner(
    session: AsyncSession,
    *,
    prefix: str,
    route_count: int,
    round_trip_positions: set[int] | None = None,
) -> tuple[User, UserSession, list[Subscription]]:
    suffix = uuid4().hex
    user = User(
        username=f"{prefix}-{suffix}",
        normalized_username=f"{prefix}-{suffix}",
        display_name=prefix,
        role="member",
        status="active",
    )
    session.add(user)
    await session.flush()
    user_session = UserSession(
        user_id=user.id,
        token_hash=uuid4().hex + uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(user_session)
    subscriptions: list[Subscription] = []
    for position in range(route_count):
        round_trip = position in (round_trip_positions or set())
        query = SearchQuery(
            provider="ctrip",
            query_hash=uuid4().hex + uuid4().hex,
            trip_type="round_trip" if round_trip else "one_way",
            adults=1,
            children=0,
            infants=0,
            cabin="economy",
            currency="CNY",
            direct_only=position % 2 == 0,
            normalized_query={"fixture": prefix, "position": position},
        )
        session.add(query)
        await session.flush()
        subscription = Subscription(
            user_id=user.id,
            search_query_id=query.id,
            name=f"Route {position}",
            enabled=position % 2 == 0,
            poll_interval_seconds=21_600,
            tags=[],
        )
        session.add(subscription)
        await session.flush()
        session.add(
            SubscriptionFilter(
                subscription_id=subscription.id,
                airline_codes=[],
                origin_airport_codes=[],
                destination_airport_codes=[],
                max_price_minor=None,
                currency=None,
                max_stops=None,
                max_duration_minutes=None,
                departure_time_start_minutes=None,
                departure_time_end_minutes=None,
                additional_filters={},
            )
        )
        session.add(
            SearchLeg(
                search_query_id=query.id,
                position=0,
                origin_code="SHA",
                destination_code="TYO",
                departure_date=date(2026, 9, 10 + position),
            )
        )
        if round_trip:
            session.add(
                SearchLeg(
                    search_query_id=query.id,
                    position=1,
                    origin_code="TYO",
                    destination_code="SHA",
                    departure_date=date(2026, 9, 18),
                )
            )
        subscriptions.append(subscription)
    await session.flush()
    return user, user_session, subscriptions


def _create_payload(name: str, subscription_ids: list, idempotency_key: str) -> dict:
    return {
        "name": name,
        "subscriptionIds": [str(value) for value in subscription_ids],
        "trendDays": 30,
        "idempotencyKey": idempotency_key,
    }


def _api_calendar_observation(
    *,
    query_id,
    run_id,
    provider_id,
    departure_date: date,
    return_date: date | None,
    observed_at: datetime,
    lowest: int,
    total: int | None,
) -> CalendarPriceObservation:
    return CalendarPriceObservation(
        observed_at=observed_at,
        search_query_id=query_id,
        collection_run_id=run_id,
        provider_id=provider_id,
        departure_date=departure_date,
        return_date=return_date,
        fingerprint=uuid4().hex + uuid4().hex,
        currency="CNY",
        lowest_price_minor=lowest,
        total_price_minor=total,
        source_endpoint="fixture",
        observation_metadata={},
    )
