from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.pagination import BucketCursor, OfferCursor
from app.api.routes.fares import _load_offers
from app.collectors.runtime.models import CapturedPayload
from app.domain.search import SearchFilters
from app.models import (
    CalendarPriceObservation,
    CollectionRun,
    DailyTrendAggregate,
    DailyTrendAggregateCoverage,
    FareOffer,
    Itinerary,
    LatestCalendarPriceSnapshot,
    LatestPriceSnapshot,
    PriceObservation,
    Provider,
    SchemaObservation,
    SearchLeg,
    SearchQuery,
    Segment,
    Subscription,
    SubscriptionFilter,
    User,
)
from app.models.enums import CollectionStatus
from app.services.collection_persistence import (
    finalize_collection_success,
    persist_collection_payloads,
)
from app.services.daily_trends import maintain_daily_trend_aggregates
from app.services.fare_data import (
    SubscriptionFareContext,
    list_latest_calendar_prices,
    load_collection_health,
    load_dashboard_price_analytics,
    load_dashboard_subscription_stats,
    load_price_history,
    load_subscription_fare_context,
    load_subscription_latest_fares,
)

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")
FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "ctrip"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


async def test_fixture_capture_persists_idempotent_calendar_and_itinerary_graph() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    observed_at = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            provider = await session.scalar(select(Provider).where(Provider.code == "ctrip"))
            assert provider is not None
            query = SearchQuery(
                provider="ctrip",
                query_hash=uuid4().hex + uuid4().hex,
                trip_type="one_way",
                adults=1,
                children=0,
                infants=0,
                cabin="economy",
                currency="CNY",
                direct_only=False,
                normalized_query={"fixture": True},
            )
            user_token = uuid4().hex
            user = User(
                username=f"fixture-{user_token}",
                normalized_username=f"fixture-{user_token}",
                email=f"fixture-{user_token}@example.test",
                display_name=f"fixture-{user_token}",
                role="member",
                status="active",
            )
            session.add_all([query, user])
            await session.flush()
            session.add(
                SearchLeg(
                    search_query_id=query.id,
                    position=0,
                    origin_code="SHA",
                    destination_code="TYO",
                    departure_date=date(2026, 8, 15),
                )
            )
            subscription = Subscription(
                user_id=user.id,
                search_query_id=query.id,
                name="Fixture route",
                enabled=True,
                poll_interval_seconds=900,
                next_due_at=observed_at,
                tags=[],
            )
            run = CollectionRun(
                search_query_id=query.id,
                provider_id=provider.id,
                idempotency_key=f"fixture:{uuid4().hex}",
                status=CollectionStatus.RUNNING.value,
                attempt=1,
                max_attempts=3,
                scheduled_at=observed_at,
                started_at=observed_at,
                run_metadata={"trigger": "fixture"},
            )
            session.add_all([subscription, run])
            await session.flush()
            session.add(
                SubscriptionFilter(
                    subscription_id=subscription.id,
                    airline_codes=[],
                    origin_airport_codes=[],
                    destination_airport_codes=[],
                )
            )
            await session.flush()

            captures = (
                CapturedPayload(
                    provider="ctrip",
                    route_key="fixture-sha-tyo",
                    capture_name="calendar",
                    status_code=200,
                    url_without_query=(
                        "https://flights.ctrip.com/international/search/api/"
                        "FlightIntlAndInlandLowestPriceSearch"
                    ),
                    received_at=observed_at,
                    payload=load_fixture("calendar_one_way.json"),
                ),
                CapturedPayload(
                    provider="ctrip",
                    route_key="fixture-sha-tyo",
                    capture_name="batch_search",
                    status_code=200,
                    url_without_query=(
                        "https://flights.ctrip.com/international/search/api/search/batchSearch"
                    ),
                    received_at=observed_at,
                    payload=load_fixture("batch_search.json"),
                ),
            )

            first = await persist_collection_payloads(
                session,
                run=run,
                provider=provider,
                search_query=query,
                captures=captures,
                observed_at=observed_at,
            )
            await finalize_collection_success(
                session,
                run=run,
                search_query=query,
                result=first,
                finished_at=observed_at + timedelta(seconds=4),
            )
            await session.flush()

            assert first.calendar_count == 3
            assert first.calendar_snapshot_count == 3
            assert first.itinerary_count == 2
            assert first.offer_count == 2
            assert first.price_observation_count == 2
            assert first.latest_snapshot_count == 2
            assert first.diagnostics == ()

            daily_trends = (
                await session.scalars(
                    select(DailyTrendAggregate)
                    .where(DailyTrendAggregate.search_query_id == query.id)
                    .order_by(DailyTrendAggregate.direct_only)
                )
            ).all()
            assert len(daily_trends) == 2
            assert [item.direct_only for item in daily_trends] == [False, True]
            assert [item.sample_count for item in daily_trends] == [1, 1]
            coverage = await session.get(
                DailyTrendAggregateCoverage,
                (query.id, observed_at.date()),
            )
            assert coverage is not None
            assert coverage.source_last_observed_at == observed_at

            second = await persist_collection_payloads(
                session,
                run=run,
                provider=provider,
                search_query=query,
                captures=captures,
                observed_at=observed_at,
            )
            await session.flush()

            assert await _count(session, CalendarPriceObservation, run.id) == 3
            assert await session.scalar(
                select(func.count()).select_from(LatestCalendarPriceSnapshot).where(
                    LatestCalendarPriceSnapshot.search_query_id == query.id
                )
            ) == 3
            assert await _count(session, Itinerary, run.id) == 2
            assert await _count(session, FareOffer, run.id) == 2
            assert await _count(session, PriceObservation, run.id) == 2
            assert await session.scalar(
                select(func.count()).select_from(LatestPriceSnapshot).where(
                    LatestPriceSnapshot.search_query_id == query.id
                )
            ) == 2
            assert second.calendar_count == 3
            assert second.price_observation_count == 0

            segment = await session.scalar(
                select(Segment)
                .join(Itinerary)
                .where(
                    Itinerary.collection_run_id == run.id,
                    Segment.flight_number == "ZZ101",
                )
            )
            assert segment is not None
            assert segment.flight_number == "ZZ101"
            assert segment.departure_at_utc == datetime(2026, 8, 15, 1, 5, tzinfo=UTC)
            assert segment.arrival_at_utc == datetime(2026, 8, 15, 4, 10, tzinfo=UTC)
            assert segment.departure_timezone == "Asia/Shanghai"
            assert segment.arrival_timezone == "Asia/Tokyo"

            assert run.status == CollectionStatus.SUCCEEDED.value
            assert run.itinerary_count == 2
            assert run.offer_count == 2
            assert subscription.last_collected_at == observed_at
            assert subscription.next_due_at == observed_at + timedelta(seconds=900)
            context = await load_subscription_fare_context(
                session,
                user_id=user.id,
                subscription_id=subscription.id,
            )
            assert context is not None
            assert await load_subscription_fare_context(
                session,
                user_id=uuid4(),
                subscription_id=subscription.id,
            ) is None
            calendar_page = await list_latest_calendar_prices(
                session,
                search_query_id=query.id,
                currency="CNY",
                round_trip=False,
                departure_start=date(2026, 8, 14),
                departure_end=date(2026, 8, 16),
                return_start=None,
                return_end=None,
                after=None,
                limit=2,
            )
            assert calendar_page.has_more is True
            assert [item.departure_date for item in calendar_page.items] == [
                date(2026, 8, 14),
                date(2026, 8, 15),
            ]
            assert all(item.direct_verified is False for item in calendar_page.items)

            round_trip_query = SearchQuery(
                provider="ctrip",
                query_hash=uuid4().hex + uuid4().hex,
                trip_type="round_trip",
                adults=1,
                children=0,
                infants=0,
                cabin="economy",
                currency="CNY",
                direct_only=False,
                normalized_query={"fixture": "round_trip"},
            )
            session.add(round_trip_query)
            await session.flush()
            session.add_all(
                [
                    SearchLeg(
                        search_query_id=round_trip_query.id,
                        position=0,
                        origin_code="SHA",
                        destination_code="TYO",
                        departure_date=date(2026, 9, 3),
                    ),
                    SearchLeg(
                        search_query_id=round_trip_query.id,
                        position=1,
                        origin_code="TYO",
                        destination_code="SHA",
                        departure_date=date(2026, 9, 8),
                    ),
                ]
            )
            round_trip_run = CollectionRun(
                search_query_id=round_trip_query.id,
                provider_id=provider.id,
                idempotency_key=f"fixture-round:{uuid4().hex}",
                status=CollectionStatus.RUNNING.value,
                attempt=1,
                max_attempts=3,
                scheduled_at=observed_at,
                started_at=observed_at,
                run_metadata={"trigger": "fixture"},
            )
            session.add(round_trip_run)
            await session.flush()
            round_trip_result = await persist_collection_payloads(
                session,
                run=round_trip_run,
                provider=provider,
                search_query=round_trip_query,
                captures=(
                    CapturedPayload(
                        provider="ctrip",
                        route_key="fixture-round-sha-tyo",
                        capture_name="calendar",
                        status_code=200,
                        url_without_query="https://example.invalid/round-calendar",
                        received_at=observed_at,
                        payload=load_fixture("calendar_round_trip.json"),
                    ),
                ),
                observed_at=observed_at,
            )
            assert round_trip_result.calendar_snapshot_count == 3
            round_trip_page = await list_latest_calendar_prices(
                session,
                search_query_id=round_trip_query.id,
                currency="CNY",
                round_trip=True,
                departure_start=date(2026, 9, 3),
                departure_end=date(2026, 9, 5),
                return_start=date(2026, 9, 8),
                return_end=date(2026, 9, 10),
                after=None,
                limit=10,
            )
            assert [
                (item.departure_date, item.return_date)
                for item in round_trip_page.items
            ] == [
                (date(2026, 9, 3), date(2026, 9, 8)),
                (date(2026, 9, 3), date(2026, 9, 10)),
                (date(2026, 9, 5), date(2026, 9, 10)),
            ]
            history_page = await load_price_history(
                session,
                context=context,
                since=observed_at - timedelta(days=1),
                as_of=observed_at + timedelta(days=1),
                resolution="raw",
                limit=100,
            )
            assert history_page.sample_count == 1
            assert len(history_page.items) == 1
            assert history_page.items[0].price_minor == 185075
            session.add_all(
                [
                    CollectionRun(
                        search_query_id=query.id,
                        provider_id=provider.id,
                        idempotency_key=f"fixture-recent-failure:{uuid4().hex}",
                        status=CollectionStatus.FAILED.value,
                        attempt=1,
                        max_attempts=3,
                        scheduled_at=observed_at + timedelta(minutes=59),
                        started_at=observed_at + timedelta(minutes=59),
                        finished_at=observed_at + timedelta(hours=1),
                        error_code="fixture_failure",
                        run_metadata={"trigger": "fixture"},
                    ),
                    CollectionRun(
                        search_query_id=query.id,
                        provider_id=provider.id,
                        idempotency_key=f"fixture-old-failure:{uuid4().hex}",
                        status=CollectionStatus.FAILED.value,
                        attempt=1,
                        max_attempts=3,
                        scheduled_at=observed_at - timedelta(days=2, minutes=1),
                        started_at=observed_at - timedelta(days=2, minutes=1),
                        finished_at=observed_at - timedelta(days=2),
                        error_code="fixture_old_failure",
                        run_metadata={"trigger": "fixture"},
                    ),
                ]
            )
            await session.flush()
            collection_health = await load_collection_health(
                session,
                user_id=user.id,
                now=observed_at + timedelta(hours=2),
            )
            assert collection_health.last_success_at == observed_at + timedelta(seconds=4)
            assert collection_health.success_rate_24h == 50.0
            assert collection_health.next_scheduled_at == observed_at + timedelta(seconds=900)
            subscription.enabled = False
            await session.flush()
            disabled_health = await load_collection_health(
                session,
                user_id=user.id,
                now=observed_at + timedelta(hours=2),
            )
            assert disabled_health.last_success_at is None
            assert disabled_health.success_rate_24h is None
            assert disabled_health.next_scheduled_at is None
            subscription.enabled = True
            await session.flush()

            observations = (
                await session.scalars(
                    select(SchemaObservation)
                    .where(SchemaObservation.collection_run_id == run.id)
                    .order_by(SchemaObservation.endpoint)
                )
            ).all()
            assert len(observations) == 2
            assert {item.occurrence_count for item in observations} == {2}

            later_run = CollectionRun(
                search_query_id=query.id,
                provider_id=provider.id,
                idempotency_key=f"fixture-later:{uuid4().hex}",
                status=CollectionStatus.RUNNING.value,
                attempt=1,
                max_attempts=3,
                scheduled_at=observed_at + timedelta(hours=3),
                started_at=observed_at + timedelta(hours=3),
                run_metadata={"trigger": "fixture"},
            )
            session.add(later_run)
            await session.flush()
            later_result = await persist_collection_payloads(
                session,
                run=later_run,
                provider=provider,
                search_query=query,
                captures=(captures[1],),
                observed_at=observed_at + timedelta(hours=3),
            )
            await finalize_collection_success(
                session,
                run=later_run,
                search_query=query,
                result=later_result,
                finished_at=observed_at + timedelta(hours=3, seconds=4),
            )
            await session.flush()

            aggregate_as_of = observed_at + timedelta(hours=4)
            aggregate_first = await load_price_history(
                session,
                context=context,
                since=observed_at - timedelta(days=1),
                as_of=aggregate_as_of,
                resolution="hour",
                limit=1,
            )
            assert len(aggregate_first.items) == 1
            assert aggregate_first.has_more is True
            aggregate_second = await load_price_history(
                session,
                context=context,
                since=observed_at - timedelta(days=1),
                as_of=aggregate_as_of,
                resolution="hour",
                limit=1,
                after=BucketCursor(
                    as_of=aggregate_as_of,
                    bucket=aggregate_first.items[-1].observed_at,
                    resolution="hour",
                ),
            )
            assert len(aggregate_second.items) == 1
            assert aggregate_second.items[0].observed_at > aggregate_first.items[0].observed_at

            context.filters.airline_codes = ["ZZ"]
            filtered_latest = await load_subscription_latest_fares(
                session,
                contexts=(context,),
            )
            assert filtered_latest[subscription.id].total_price_minor == 218000
            assert filtered_latest[subscription.id].currency == "CNY"
            context.filters.max_price_minor = 200000
            assert await load_subscription_latest_fares(session, contexts=(context,)) == {}

            def batch_context(
                *,
                airline_codes: list[str] | None = None,
                origin_airport_codes: list[str] | None = None,
                destination_airport_codes: list[str] | None = None,
                max_price_minor: int | None = None,
                max_stops: int | None = None,
                max_duration_minutes: int | None = None,
                departure_time_start_minutes: int | None = None,
                departure_time_end_minutes: int | None = None,
            ) -> SubscriptionFareContext:
                local_subscription = Subscription(
                    id=uuid4(),
                    user_id=user.id,
                    search_query_id=query.id,
                    name="Set-based equivalence route",
                    enabled=True,
                    poll_interval_seconds=900,
                    tags=[],
                )
                return SubscriptionFareContext(
                    subscription=local_subscription,
                    search_query=query,
                    filters=SubscriptionFilter(
                        subscription_id=local_subscription.id,
                        airline_codes=airline_codes or [],
                        origin_airport_codes=origin_airport_codes or [],
                        destination_airport_codes=destination_airport_codes or [],
                        max_price_minor=max_price_minor,
                        max_stops=max_stops,
                        max_duration_minutes=max_duration_minutes,
                        departure_time_start_minutes=departure_time_start_minutes,
                        departure_time_end_minutes=departure_time_end_minutes,
                        additional_filters={},
                    ),
                    legs=context.legs,
                )

            batch_contexts = (
                batch_context(),
                batch_context(airline_codes=["ZZ"]),
                batch_context(airline_codes=["NO"]),
                batch_context(origin_airport_codes=["PVG"]),
                batch_context(destination_airport_codes=["NRT"]),
                batch_context(max_price_minor=200000),
                batch_context(max_stops=0),
                batch_context(max_duration_minutes=200),
                batch_context(
                    departure_time_start_minutes=540,
                    departure_time_end_minutes=600,
                ),
            )
            singleton_latest = {}
            for batch_item in batch_contexts:
                singleton_latest.update(
                    await load_subscription_latest_fares(
                        session,
                        contexts=(batch_item,),
                    )
                )
            assert await load_subscription_latest_fares(
                session,
                contexts=batch_contexts,
            ) == singleton_latest

            first_offers, first_has_more, offer_total = await _load_offers(
                session,
                later_run.id,
                filters=SearchFilters(),
                provider="ctrip",
                currency="CNY",
                after=None,
                limit=1,
            )
            assert offer_total == 2
            assert len(first_offers) == 1
            assert first_has_more is True
            second_offers, second_has_more, second_total = await _load_offers(
                session,
                later_run.id,
                filters=SearchFilters(),
                provider="ctrip",
                currency="CNY",
                after=OfferCursor(
                    run_id=later_run.id,
                    price_minor=first_offers[0].total_price_minor,
                    row_id=first_offers[0].id,
                    filter_key="fixture",
                ),
                limit=1,
            )
            assert second_total == 2
            assert len(second_offers) == 1
            assert second_has_more is False
            assert second_offers[0].id != first_offers[0].id

            context.filters.airline_codes = []
            context.filters.max_price_minor = None
            alternate_context = SubscriptionFareContext(
                subscription=context.subscription,
                search_query=context.search_query,
                filters=SubscriptionFilter(
                    subscription_id=subscription.id,
                    airline_codes=["ZZ"],
                    origin_airport_codes=[],
                    destination_airport_codes=[],
                    max_price_minor=None,
                    max_stops=None,
                    max_duration_minutes=None,
                    departure_time_start_minutes=None,
                    departure_time_end_minutes=None,
                    additional_filters={},
                ),
                legs=context.legs,
            )
            analytics = await load_dashboard_price_analytics(
                session,
                contexts=(context, alternate_context),
                as_of=observed_at + timedelta(hours=27),
            )
            raw_analytics = await load_dashboard_price_analytics(
                session,
                contexts=(context, alternate_context),
                as_of=observed_at + timedelta(hours=27),
                use_daily_aggregates=False,
            )
            assert analytics == raw_analytics
            assert len(analytics.trend) == 1
            assert analytics.trend[0].price_minor == 185075
            assert analytics.trend[0].sample_count == 4
            assert analytics.price_change_percent == 0.0

            trend_as_of = observed_at + timedelta(hours=27)
            coverage_start = (trend_as_of - timedelta(days=30)).date() + timedelta(days=1)
            coverage_end = trend_as_of.date() - timedelta(days=1)
            first_maintenance_page = await maintain_daily_trend_aggregates(
                session,
                start_date=coverage_start,
                end_date=coverage_end,
                batch_size=10,
                search_query_id=query.id,
            )
            second_maintenance_page = await maintain_daily_trend_aggregates(
                session,
                start_date=coverage_start,
                end_date=coverage_end,
                batch_size=100,
                search_query_id=query.id,
                after=first_maintenance_page.next_cursor,
            )
            rerun = await maintain_daily_trend_aggregates(
                session,
                start_date=coverage_start,
                end_date=coverage_end,
                batch_size=100,
                search_query_id=query.id,
            )
            assert first_maintenance_page.day_count == 10
            assert first_maintenance_page.next_cursor is not None
            assert second_maintenance_page.day_count == 19
            assert (
                first_maintenance_page.aggregate_count
                + second_maintenance_page.aggregate_count
                == 2
            )
            assert rerun.day_count == 29
            assert rerun.aggregate_count == 2
            covered_analytics = await load_dashboard_price_analytics(
                session,
                contexts=(context, alternate_context),
                as_of=trend_as_of,
            )
            assert covered_analytics == raw_analytics

            all_filter_analytics = await load_dashboard_price_analytics(
                session,
                contexts=batch_contexts,
                as_of=trend_as_of,
            )
            all_filter_raw_analytics = await load_dashboard_price_analytics(
                session,
                contexts=batch_contexts,
                as_of=trend_as_of,
                use_daily_aggregates=False,
            )
            assert all_filter_analytics == all_filter_raw_analytics
            dashboard_stats = await load_dashboard_subscription_stats(
                session,
                user_id=user.id,
            )
            assert dashboard_stats.active_subscriptions == 1
            assert dashboard_stats.routes_tracked == 1
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


async def _count(
    session: AsyncSession,
    model: type[Any],
    run_id: Any,
) -> int:
    value = await session.scalar(
        select(func.count()).select_from(model).where(model.collection_run_id == run_id)
    )
    return int(value or 0)
