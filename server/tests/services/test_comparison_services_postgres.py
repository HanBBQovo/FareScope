from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.partitions import (
    calendar_price_observation_partition_ddl,
    price_observation_partition_ddl,
)
from app.models import (
    AuditEvent,
    CalendarPriceObservation,
    CollectionRun,
    ComparisonView,
    ComparisonViewItem,
    FareOffer,
    Itinerary,
    LatestCalendarPriceSnapshot,
    PriceObservation,
    Provider,
    SearchLeg,
    SearchQuery,
    Subscription,
    SubscriptionFilter,
    User,
)
from app.services.comparisons import (
    ComparisonConflictError,
    ComparisonLimitError,
    ComparisonNotFoundError,
    ComparisonVersionConflictError,
    ComparisonViewRecord,
    create_comparison_view,
    get_comparison_view,
    list_comparison_views,
    replace_comparison_view,
)
from app.services.daily_trends import maintain_daily_trend_aggregates
from app.services.fare_data import (
    load_subscription_calendar_analytics,
    load_subscription_fare_contexts,
    load_subscription_latest_calendar_fares,
    load_subscription_latest_fares,
    load_subscription_price_analytics,
)

DATABASE_URL = os.getenv("FARESCOPE_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="FARESCOPE_TEST_DATABASE_URL is not configured",
    ),
]


async def test_comparison_crud_owner_idempotency_version_and_degraded_state() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with factory() as session, session.begin():
                owner, owner_subscriptions = await _seed_owner(
                    session,
                    prefix="comparison-owner",
                    currencies=("CNY", "CNY", "JPY"),
                )
                outsider, outsider_subscriptions = await _seed_owner(
                    session,
                    prefix="comparison-outsider",
                    currencies=("CNY", "CNY"),
                )

            route_ids = tuple(subscription.id for subscription in owner_subscriptions[:2])
            async with factory() as session, session.begin():
                record, created = await create_comparison_view(
                    session,
                    user=owner,
                    name="  Japan   routes ",
                    subscription_ids=route_ids,
                    trend_days=30,
                    idempotency_key="comparison-crud-create",
                )
                comparison_id = record.view.id
                assert created is True
                assert record.view.name == "Japan routes"
                assert record.view.currency == "CNY"
                assert record.subscription_ids == route_ids

            async with factory() as session, session.begin():
                replay, replay_created = await create_comparison_view(
                    session,
                    user=owner,
                    name="Japan routes",
                    subscription_ids=route_ids,
                    trend_days=30,
                    idempotency_key="comparison-crud-create",
                )
                assert replay_created is False
                assert replay.view.id == comparison_id
                with pytest.raises(ComparisonConflictError, match="idempotency"):
                    await create_comparison_view(
                        session,
                        user=owner,
                        name="Different request",
                        subscription_ids=route_ids,
                        trend_days=30,
                        idempotency_key="comparison-crud-create",
                    )

            async with factory() as session, session.begin():
                with pytest.raises(ComparisonNotFoundError):
                    await create_comparison_view(
                        session,
                        user=owner,
                        name="Foreign route",
                        subscription_ids=(
                            owner_subscriptions[0].id,
                            outsider_subscriptions[0].id,
                        ),
                        trend_days=30,
                        idempotency_key="comparison-foreign-route",
                    )
                with pytest.raises(ValueError, match="same currency"):
                    await create_comparison_view(
                        session,
                        user=owner,
                        name="Mixed currency",
                        subscription_ids=(
                            owner_subscriptions[0].id,
                            owner_subscriptions[2].id,
                        ),
                        trend_days=30,
                        idempotency_key="comparison-mixed-currency",
                    )

            reversed_ids = tuple(reversed(route_ids))
            async with factory() as session, session.begin():
                updated = await replace_comparison_view(
                    session,
                    user=owner,
                    comparison_id=comparison_id,
                    name="Japan autumn",
                    subscription_ids=reversed_ids,
                    trend_days=90,
                    expected_version=1,
                )
                assert updated.view.version == 2
                assert updated.subscription_ids == reversed_ids

            async with factory() as session, session.begin():
                idempotent_retry = await replace_comparison_view(
                    session,
                    user=owner,
                    comparison_id=comparison_id,
                    name="Japan autumn",
                    subscription_ids=reversed_ids,
                    trend_days=90,
                    expected_version=1,
                )
                assert idempotent_retry.view.version == 2
                with pytest.raises(ComparisonVersionConflictError):
                    await replace_comparison_view(
                        session,
                        user=owner,
                        comparison_id=comparison_id,
                        name="Stale edit",
                        subscription_ids=reversed_ids,
                        trend_days=90,
                        expected_version=1,
                    )

            async with factory() as session, session.begin():
                page = await list_comparison_views(
                    session,
                    user_id=owner.id,
                    as_of=datetime.now(UTC),
                    limit=20,
                )
                assert [item.view.id for item in page.items] == [comparison_id]
                assert outsider.id != owner.id
                await session.execute(
                    delete(Subscription).where(Subscription.id == reversed_ids[0])
                )

            async with factory() as session:
                degraded = await get_comparison_view(
                    session,
                    user_id=owner.id,
                    comparison_id=comparison_id,
                )
                assert degraded.active_route_count == 1
                assert degraded.missing_subscription_count == 1
                assert degraded.comparable is False
                item_count = len(
                    (
                        await session.scalars(
                            select(ComparisonViewItem).where(
                                ComparisonViewItem.comparison_view_id == comparison_id
                            )
                        )
                    ).all()
                )
                assert item_count == 1
                with pytest.raises(ComparisonNotFoundError):
                    await get_comparison_view(
                        session,
                        user_id=outsider.id,
                        comparison_id=comparison_id,
                    )
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_comparison_quota_is_serialized_across_concurrent_creates() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        owner, subscriptions = await _seed_owner(
            session,
            prefix="comparison-concurrency",
            currencies=("CNY", "CNY", "CNY"),
        )
        user_id = owner.id
        query_ids = tuple(subscription.search_query_id for subscription in subscriptions)

    async def create(name: str, route_ids: tuple[UUID, UUID]):
        async with factory() as session, session.begin():
            loaded_user = await session.get(User, user_id)
            assert loaded_user is not None
            return await create_comparison_view(
                session,
                user=loaded_user,
                name=name,
                subscription_ids=route_ids,
                trend_days=7,
                idempotency_key=f"comparison-concurrent:{name}",
                max_views=1,
            )

    try:
        results = await asyncio.gather(
            create("first", (subscriptions[0].id, subscriptions[1].id)),
            create("second", (subscriptions[1].id, subscriptions[2].id)),
            return_exceptions=True,
        )
        assert sum(isinstance(result, tuple) for result in results) == 1
        assert sum(isinstance(result, ComparisonLimitError) for result in results) == 1
    finally:
        async with factory() as session, session.begin():
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(SearchQuery).where(SearchQuery.id.in_(query_ids)))
        await engine.dispose()


async def test_concurrent_replacements_with_the_same_name_return_a_stable_conflict() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        owner, subscriptions = await _seed_owner(
            session,
            prefix="comparison-concurrent-replace",
            currencies=("CNY", "CNY", "CNY"),
        )
        user_id = owner.id
        query_ids = tuple(subscription.search_query_id for subscription in subscriptions)
        first, _ = await create_comparison_view(
            session,
            user=owner,
            name="First original",
            subscription_ids=(subscriptions[0].id, subscriptions[1].id),
            trend_days=30,
            idempotency_key="comparison-concurrent-replace:first",
        )
        second, _ = await create_comparison_view(
            session,
            user=owner,
            name="Second original",
            subscription_ids=(subscriptions[1].id, subscriptions[2].id),
            trend_days=30,
            idempotency_key="comparison-concurrent-replace:second",
        )

    ready_count = 0
    both_ready = asyncio.Event()
    release = asyncio.Event()

    async def rename(comparison_id: UUID, route_ids: tuple[UUID, UUID]):
        nonlocal ready_count
        async with factory() as session, session.begin():
            loaded_user = await session.get(User, user_id)
            assert loaded_user is not None
            ready_count += 1
            if ready_count == 2:
                both_ready.set()
            await release.wait()
            return await replace_comparison_view(
                session,
                user=loaded_user,
                comparison_id=comparison_id,
                name="Shared target",
                subscription_ids=route_ids,
                trend_days=30,
                expected_version=1,
            )

    first_task = asyncio.create_task(
        rename(first.view.id, (subscriptions[0].id, subscriptions[1].id))
    )
    second_task = asyncio.create_task(
        rename(second.view.id, (subscriptions[1].id, subscriptions[2].id))
    )
    try:
        await both_ready.wait()
        release.set()
        results = await asyncio.gather(first_task, second_task, return_exceptions=True)
        assert sum(isinstance(result, ComparisonViewRecord) for result in results) == 1
        assert sum(isinstance(result, ComparisonConflictError) for result in results) == 1

        async with factory() as session:
            shared_name_count = await session.scalar(
                select(func.count())
                .select_from(ComparisonView)
                .where(
                    ComparisonView.user_id == user_id,
                    ComparisonView.normalized_name == "shared target",
                )
            )
            assert shared_name_count == 1
    finally:
        release.set()
        for task in (first_task, second_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        async with factory() as session, session.begin():
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(SearchQuery).where(SearchQuery.id.in_(query_ids)))
        await engine.dispose()


async def test_comparison_price_and_calendar_batches_preserve_separate_semantics() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            as_of = datetime.now(UTC).replace(microsecond=0)
            await connection.execute(text(price_observation_partition_ddl(as_of)))
            await connection.execute(text(calendar_price_observation_partition_ddl(as_of)))
            async with factory() as session, session.begin():
                owner, subscriptions = await _seed_owner(
                    session,
                    prefix="comparison-data",
                    currencies=("CNY", "CNY"),
                    round_trip_positions={1},
                )
                provider = Provider(
                    code=f"cmp-{uuid4().hex}",
                    display_name="Comparison fixture",
                    enabled=True,
                    adapter_version="test",
                )
                session.add(provider)
                await session.flush()
                for index, subscription in enumerate(subscriptions):
                    await _seed_detailed_price(
                        session,
                        provider=provider,
                        subscription=subscription,
                        observed_at=as_of - timedelta(days=2 - index),
                        price_minor=180_000 + index * 40_000,
                    )

                one_way_query = subscriptions[0].search_query_id
                round_trip_query = subscriptions[1].search_query_id
                one_way_departure = date(2026, 9, 10)
                round_departure = date(2026, 9, 11)
                round_return = date(2026, 9, 18)
                one_way_run = await _latest_run(session, query_id=one_way_query)
                round_run = await _latest_run(session, query_id=round_trip_query)
                session.add_all(
                    (
                        LatestCalendarPriceSnapshot(
                            search_query_id=one_way_query,
                            collection_run_id=one_way_run.id,
                            provider_id=provider.id,
                            departure_date=one_way_departure,
                            return_date=None,
                            currency="CNY",
                            lowest_price_minor=160_000,
                            total_price_minor=160_000,
                            observed_at=as_of - timedelta(hours=2),
                            source_endpoint="fixture",
                            direct_verified=False,
                        ),
                        LatestCalendarPriceSnapshot(
                            search_query_id=round_trip_query,
                            collection_run_id=round_run.id,
                            provider_id=provider.id,
                            departure_date=round_departure,
                            return_date=round_return,
                            currency="CNY",
                            lowest_price_minor=170_000,
                            total_price_minor=None,
                            observed_at=as_of - timedelta(hours=1),
                            source_endpoint="fixture",
                            direct_verified=False,
                        ),
                    )
                )
                session.add_all(
                    (
                        _calendar_observation(
                            query_id=one_way_query,
                            run_id=one_way_run.id,
                            provider_id=provider.id,
                            departure_date=one_way_departure,
                            return_date=None,
                            observed_at=as_of - timedelta(days=2),
                            lowest=155_000,
                            total=155_000,
                        ),
                        _calendar_observation(
                            query_id=one_way_query,
                            run_id=one_way_run.id,
                            provider_id=provider.id,
                            departure_date=one_way_departure,
                            return_date=None,
                            observed_at=as_of - timedelta(days=2, minutes=-1),
                            lowest=158_000,
                            total=158_000,
                        ),
                        _calendar_observation(
                            query_id=round_trip_query,
                            run_id=round_run.id,
                            provider_id=provider.id,
                            departure_date=round_departure,
                            return_date=round_return,
                            observed_at=as_of - timedelta(days=2),
                            lowest=165_000,
                            total=None,
                        ),
                        _calendar_observation(
                            query_id=round_trip_query,
                            run_id=round_run.id,
                            provider_id=provider.id,
                            departure_date=round_departure,
                            return_date=round_return,
                            observed_at=as_of - timedelta(days=1),
                            lowest=175_000,
                            total=350_000,
                        ),
                    )
                )

            route_ids = tuple(subscription.id for subscription in subscriptions)
            async with factory() as session:
                contexts = await load_subscription_fare_contexts(
                    session,
                    user_id=owner.id,
                    subscription_ids=route_ids,
                )
                latest = await load_subscription_latest_fares(session, contexts=contexts)
                assert latest[route_ids[0]].total_price_minor == 180_000
                assert latest[route_ids[1]].total_price_minor == 220_000

                latest_calendar = await load_subscription_latest_calendar_fares(
                    session,
                    contexts=contexts,
                )
                assert latest_calendar[route_ids[0]].lowest_price_minor == 160_000
                assert latest_calendar[route_ids[1]].lowest_price_minor == 170_000
                assert latest_calendar[route_ids[1]].total_price_minor is None
                assert latest_calendar[route_ids[1]].direct_verified is False

                calendar = await load_subscription_calendar_analytics(
                    session,
                    contexts=contexts,
                    as_of=as_of,
                    days=7,
                )
                assert [point.price_minor for point in calendar[route_ids[0]].trend] == [155_000]
                assert calendar[route_ids[0]].trend[0].sample_count == 1
                assert [point.price_minor for point in calendar[route_ids[1]].trend] == [350_000]

                raw = await load_subscription_price_analytics(
                    session,
                    contexts=contexts,
                    as_of=as_of,
                    days=7,
                    use_daily_aggregates=False,
                )
                fallback = await load_subscription_price_analytics(
                    session,
                    contexts=contexts,
                    as_of=as_of,
                    days=7,
                )
                assert fallback == raw

            coverage_start = (as_of - timedelta(days=7)).date() + timedelta(days=1)
            coverage_end = as_of.date() - timedelta(days=1)
            async with factory() as session, session.begin():
                for subscription in subscriptions:
                    await maintain_daily_trend_aggregates(
                        session,
                        start_date=coverage_start,
                        end_date=coverage_end,
                        search_query_id=subscription.search_query_id,
                        batch_size=100,
                    )
            async with factory() as session:
                contexts = await load_subscription_fare_contexts(
                    session,
                    user_id=owner.id,
                    subscription_ids=route_ids,
                )
                aggregate = await load_subscription_price_analytics(
                    session,
                    contexts=contexts,
                    as_of=as_of,
                    days=7,
                )
                raw = await load_subscription_price_analytics(
                    session,
                    contexts=contexts,
                    as_of=as_of,
                    days=7,
                    use_daily_aggregates=False,
                )
                assert aggregate == raw
        finally:
            await transaction.rollback()
    await engine.dispose()


async def test_comparison_snapshot_business_query_count_is_constant_for_two_and_eight_routes() -> (
    None
):
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
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
            if normalized.startswith(("SELECT", "WITH")):
                statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", track_statement)
        try:
            async with factory() as session, session.begin():
                owner, subscriptions = await _seed_owner(
                    session,
                    prefix="comparison-query-count",
                    currencies=("CNY",) * 8,
                )
                two, _ = await create_comparison_view(
                    session,
                    user=owner,
                    name="Two routes",
                    subscription_ids=tuple(item.id for item in subscriptions[:2]),
                    trend_days=7,
                    idempotency_key="comparison-query-count-two",
                )
                eight, _ = await create_comparison_view(
                    session,
                    user=owner,
                    name="Eight routes",
                    subscription_ids=tuple(item.id for item in subscriptions),
                    trend_days=7,
                    idempotency_key="comparison-query-count-eight",
                )

            for comparison_id in (two.view.id, eight.view.id):
                statements.clear()
                async with factory() as session:
                    record = await get_comparison_view(
                        session,
                        user_id=owner.id,
                        comparison_id=comparison_id,
                    )
                    contexts = await load_subscription_fare_contexts(
                        session,
                        user_id=owner.id,
                        subscription_ids=record.subscription_ids,
                    )
                    await load_subscription_latest_fares(session, contexts=contexts)
                    await load_subscription_latest_calendar_fares(session, contexts=contexts)
                    await load_subscription_price_analytics(
                        session,
                        contexts=contexts,
                        as_of=datetime.now(UTC),
                        days=record.view.trend_days,
                    )
                    await load_subscription_calendar_analytics(
                        session,
                        contexts=contexts,
                        as_of=datetime.now(UTC),
                        days=record.view.trend_days,
                    )
                assert len(statements) == 6
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", track_statement)
            await transaction.rollback()
    await engine.dispose()


async def test_repeatable_read_snapshot_cannot_mix_a_later_committed_run() -> None:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seed_time = datetime.now(UTC).replace(microsecond=0)
    async with engine.begin() as connection:
        await connection.execute(text(price_observation_partition_ddl(seed_time)))
    async with factory() as session, session.begin():
        owner, subscriptions = await _seed_owner(
            session,
            prefix="comparison-repeatable-read",
            currencies=("CNY", "CNY"),
        )
        provider = Provider(
            code=f"rr-{uuid4().hex}",
            display_name="Repeatable read fixture",
            enabled=True,
            adapter_version="test",
        )
        session.add(provider)
        await session.flush()
        await _seed_detailed_price(
            session,
            provider=provider,
            subscription=subscriptions[0],
            observed_at=seed_time - timedelta(days=1),
            price_minor=210_000,
        )
        user_id = owner.id
        subscription_ids = tuple(item.id for item in subscriptions)
        query_ids = tuple(item.search_query_id for item in subscriptions)
        provider_id = provider.id

    try:
        async with factory() as snapshot_session, snapshot_session.begin():
            await snapshot_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            snapshot_time = await snapshot_session.scalar(select(func.transaction_timestamp()))
            assert snapshot_time is not None
            contexts = await load_subscription_fare_contexts(
                snapshot_session,
                user_id=user_id,
                subscription_ids=subscription_ids,
            )
            initial_latest = await load_subscription_latest_fares(
                snapshot_session,
                contexts=contexts,
            )
            assert initial_latest[subscription_ids[0]].total_price_minor == 210_000

            async with factory() as writer, writer.begin():
                stored_provider = await writer.get(Provider, provider_id)
                stored_subscription = await writer.get(Subscription, subscription_ids[0])
                assert stored_provider is not None and stored_subscription is not None
                await _seed_detailed_price(
                    writer,
                    provider=stored_provider,
                    subscription=stored_subscription,
                    observed_at=snapshot_time - timedelta(minutes=1),
                    price_minor=170_000,
                )

            still_initial = await load_subscription_latest_fares(
                snapshot_session,
                contexts=contexts,
            )
            trend = await load_subscription_price_analytics(
                snapshot_session,
                contexts=contexts,
                as_of=snapshot_time,
                days=7,
                use_daily_aggregates=False,
            )
            assert still_initial[subscription_ids[0]].total_price_minor == 210_000
            assert trend[subscription_ids[0]].minimum_price_minor == 210_000

        async with factory() as fresh_session:
            fresh_contexts = await load_subscription_fare_contexts(
                fresh_session,
                user_id=user_id,
                subscription_ids=subscription_ids,
            )
            fresh_latest = await load_subscription_latest_fares(
                fresh_session,
                contexts=fresh_contexts,
            )
            assert fresh_latest[subscription_ids[0]].total_price_minor == 170_000
    finally:
        async with factory() as session, session.begin():
            await session.execute(
                delete(CollectionRun).where(CollectionRun.search_query_id.in_(query_ids))
            )
            await session.execute(delete(User).where(User.id == user_id))
            await session.execute(delete(SearchQuery).where(SearchQuery.id.in_(query_ids)))
            await session.execute(delete(Provider).where(Provider.id == provider_id))
        await engine.dispose()


async def _seed_owner(
    session: AsyncSession,
    *,
    prefix: str,
    currencies: tuple[str, ...],
    round_trip_positions: set[int] | None = None,
) -> tuple[User, list[Subscription]]:
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
    subscriptions: list[Subscription] = []
    for position, currency in enumerate(currencies):
        round_trip = position in (round_trip_positions or set())
        query = SearchQuery(
            provider="ctrip",
            query_hash=uuid4().hex + uuid4().hex,
            trip_type="round_trip" if round_trip else "one_way",
            adults=1,
            children=0,
            infants=0,
            cabin="economy",
            currency=currency,
            direct_only=False,
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
        departure = date(2026, 9, 10 + position)
        session.add(
            SearchLeg(
                search_query_id=query.id,
                position=0,
                origin_code="SHA",
                destination_code="TYO",
                departure_date=departure,
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
    return user, subscriptions


async def _seed_detailed_price(
    session: AsyncSession,
    *,
    provider: Provider,
    subscription: Subscription,
    observed_at: datetime,
    price_minor: int,
) -> None:
    run = CollectionRun(
        search_query_id=subscription.search_query_id,
        provider_id=provider.id,
        idempotency_key=f"comparison-data:{uuid4().hex}",
        status="succeeded",
        attempt=1,
        max_attempts=3,
        scheduled_at=observed_at,
        started_at=observed_at,
        finished_at=observed_at,
        offer_count=1,
        itinerary_count=1,
        run_metadata={"fixture": "comparison"},
    )
    session.add(run)
    await session.flush()
    itinerary = Itinerary(
        collection_run_id=run.id,
        search_query_id=subscription.search_query_id,
        provider_id=provider.id,
        provider_itinerary_id=f"itinerary-{uuid4().hex}",
        fingerprint=uuid4().hex + uuid4().hex,
        total_duration_minutes=180,
        stop_count=0,
        is_direct=True,
        leg_count=1,
        itinerary_metadata={},
    )
    session.add(itinerary)
    await session.flush()
    offer = FareOffer(
        collection_run_id=run.id,
        itinerary_id=itinerary.id,
        provider_offer_id=f"offer-{uuid4().hex}",
        fingerprint=uuid4().hex + uuid4().hex,
        cabin="economy",
        currency="CNY",
        total_price_minor=price_minor,
        offer_metadata={},
    )
    session.add(offer)
    await session.flush()
    session.add(
        PriceObservation(
            observed_at=observed_at,
            search_query_id=subscription.search_query_id,
            collection_run_id=run.id,
            itinerary_id=itinerary.id,
            fare_offer_id=offer.id,
            provider_id=provider.id,
            offer_fingerprint=offer.fingerprint,
            currency="CNY",
            total_price_minor=price_minor,
            is_lowest=True,
            is_direct=True,
        )
    )


async def _latest_run(session: AsyncSession, *, query_id: UUID) -> CollectionRun:
    run = await session.scalar(
        select(CollectionRun)
        .where(CollectionRun.search_query_id == query_id)
        .order_by(CollectionRun.finished_at.desc())
        .limit(1)
    )
    assert run is not None
    return run


def _calendar_observation(
    *,
    query_id: UUID,
    run_id: UUID,
    provider_id: UUID,
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
