from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.fares import _load_offers
from app.domain.search import SearchFilters
from app.models import SearchLeg, SearchQuery, Subscription, SubscriptionFilter, User
from app.services.collection_operations import _load_run_status_counts, _load_schema_signals
from app.services.fare_data import (
    SubscriptionFareContext,
    list_latest_calendar_prices,
    load_collection_health,
    load_dashboard_price_analytics,
    load_dashboard_subscription_stats,
    load_price_history,
    load_subscription_latest_fares,
)
from app.services.subscriptions import list_subscription_views


def _sqlalchemy_url(value: str) -> str:
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _asyncpg_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _search_filters(context: SubscriptionFareContext) -> SearchFilters:
    filters = context.filters
    return SearchFilters(
        direct_only=context.search_query.direct_only,
        airline_codes=tuple(filters.airline_codes),
        departure_airports=tuple(filters.origin_airport_codes),
        arrival_airports=tuple(filters.destination_airport_codes),
        max_price_minor=filters.max_price_minor,
        max_stops=filters.max_stops,
        max_duration_minutes=filters.max_duration_minutes,
        departure_minute_start=filters.departure_time_start_minutes,
        departure_minute_end=filters.departure_time_end_minutes,
    )


async def _measure[T](label: str, call: Callable[[], Awaitable[T]]) -> T:
    started = time.perf_counter()
    result = await call()
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"APP {label} {elapsed_ms:.3f} ms")
    return result


async def main() -> None:
    raw_database_url = os.environ["FARESCOPE_PERF_DATABASE_URL"]
    database_url = _sqlalchemy_url(raw_database_url)
    target_email = os.getenv("FARESCOPE_PERF_TARGET_EMAIL", "perf+1@example.invalid")
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    captured: list[tuple[str, str, tuple[Any, ...]]] = []
    capture_label = "setup"

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: tuple[Any, ...],
        _context: object,
        _executemany: bool,
    ) -> None:
        statement_kind = statement.lstrip().upper()
        if capture_label != "setup" and statement_kind.startswith(("SELECT", "WITH")):
            captured.append((capture_label, statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == target_email))
        if user is None:
            raise RuntimeError("performance fixture user not found; run generate_load.sql first")
        subscriptions = (
            await session.scalars(
                select(Subscription)
                .where(Subscription.user_id == user.id)
                .order_by(Subscription.created_at.desc(), Subscription.id.desc())
                .limit(100)
            )
        ).all()
        query_ids = {item.search_query_id for item in subscriptions}
        queries = (
            await session.scalars(select(SearchQuery).where(SearchQuery.id.in_(query_ids)))
        ).all()
        filters = (
            await session.scalars(
                select(SubscriptionFilter).where(
                    SubscriptionFilter.subscription_id.in_({item.id for item in subscriptions})
                )
            )
        ).all()
        legs = (
            await session.scalars(
                select(SearchLeg)
                .where(SearchLeg.search_query_id.in_(query_ids))
                .order_by(SearchLeg.search_query_id, SearchLeg.position)
            )
        ).all()
        query_map = {item.id: item for item in queries}
        filter_map = {item.subscription_id: item for item in filters}
        legs_by_query: dict[object, list[SearchLeg]] = defaultdict(list)
        for leg in legs:
            legs_by_query[leg.search_query_id].append(leg)
        contexts = tuple(
            SubscriptionFareContext(
                subscription=subscription,
                search_query=query_map[subscription.search_query_id],
                filters=filter_map[subscription.id],
                legs=tuple(legs_by_query[subscription.search_query_id]),
            )
            for subscription in subscriptions
        )
        now = datetime.now(UTC)

        capture_label = "subscription-list"
        await _measure(
            capture_label,
            lambda: list_subscription_views(
                session,
                user_id=user.id,
                limit=50,
                as_of=now,
            ),
        )
        capture_label = "dashboard-latest"
        latest_fares = await _measure(
            capture_label,
            lambda: load_subscription_latest_fares(session, contexts=contexts),
        )

        offer_context = next(
            context for context in contexts if context.subscription.id in latest_fares
        )
        capture_label = "fare-search"
        await _measure(
            capture_label,
            lambda: _load_offers(
                session,
                latest_fares[offer_context.subscription.id].collection_run_id,
                filters=_search_filters(offer_context),
                provider=offer_context.search_query.provider,
                currency=offer_context.search_query.currency,
                after=None,
                limit=50,
            ),
        )

        history_context = next(
            context
            for context in contexts
            if not context.filters.airline_codes
            and not context.filters.origin_airport_codes
            and not context.filters.destination_airport_codes
        )
        for resolution in ("raw", "day"):
            capture_label = f"price-history-{resolution}"
            await _measure(
                capture_label,
                lambda resolution=resolution: load_price_history(
                    session,
                    context=history_context,
                    since=now - timedelta(days=30),
                    as_of=now,
                    resolution=resolution,
                    limit=50,
                ),
            )

        for round_trip in (False, True):
            calendar_context = next(
                context
                for context in contexts
                if (context.search_query.trip_type == "round_trip") is round_trip
            )
            outbound_date = calendar_context.legs[0].departure_date
            return_date = calendar_context.legs[1].departure_date if round_trip else None
            capture_label = "calendar-roundtrip" if round_trip else "calendar-oneway"
            await _measure(
                capture_label,
                lambda calendar_context=calendar_context,
                round_trip=round_trip,
                outbound_date=outbound_date,
                return_date=return_date: list_latest_calendar_prices(
                    session,
                    search_query_id=calendar_context.search_query.id,
                    currency=calendar_context.search_query.currency,
                    round_trip=round_trip,
                    departure_start=outbound_date,
                    departure_end=outbound_date + timedelta(days=180),
                    return_start=return_date,
                    return_end=(return_date + timedelta(days=180)) if return_date else None,
                    after=None,
                    limit=50,
                ),
            )

        capture_label = "dashboard-trend"
        await _measure(
            capture_label,
            lambda: load_dashboard_price_analytics(
                session,
                contexts=contexts,
                as_of=now,
            ),
        )
        capture_label = "dashboard-counts"
        await _measure(
            capture_label,
            lambda: load_dashboard_subscription_stats(session, user_id=user.id),
        )
        capture_label = "collection-health"
        await _measure(
            capture_label,
            lambda: load_collection_health(session, user_id=user.id, now=now),
        )
        capture_label = "collection-run-status-counts"
        await _measure(
            capture_label,
            lambda: _load_run_status_counts(session, user_id=user.id, now=now),
        )
        capture_label = "provider-schema-signals"
        await _measure(
            capture_label,
            lambda: _load_schema_signals(
                session,
                user_id=user.id,
                now=now,
                limit=20,
            ),
        )

    event.remove(engine.sync_engine, "before_cursor_execute", capture)
    await engine.dispose()

    connection = await asyncpg.connect(_asyncpg_url(raw_database_url))
    try:
        for index, (label, statement, parameters) in enumerate(captured, start=1):
            rows = await connection.fetch(
                "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) " + statement,
                *parameters,
            )
            value = rows[0][0]
            payload = json.loads(value) if isinstance(value, str) else value
            result = payload[0]
            root = result["Plan"]
            print(
                "PLAN "
                f"{index} {label} execution={result['Execution Time']:.3f}ms "
                f"planning={result['Planning Time']:.3f}ms "
                f"node={root['Node Type']} rows={root['Actual Rows']} "
                f"hits={root.get('Shared Hit Blocks', 0)} "
                f"reads={root.get('Shared Read Blocks', 0)}"
            )
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
