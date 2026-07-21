from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
import httpx
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.routes.fares import _load_offers
from app.domain.search import SearchFilters
from app.models import (
    CollectionRun,
    SearchLeg,
    SearchQuery,
    Subscription,
    SubscriptionFilter,
    User,
)
from app.models.enums import CollectionStatus
from app.services.collection_operations import (
    QueueDepths,
    _load_run_status_counts,
    _load_schema_signals,
)
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
from performance.safety import (
    DISPOSABLE_CONFIRMATION,
    redact_url,
    require_confirmation,
    to_asyncpg_url,
    to_sqlalchemy_url,
    validate_performance_database_url,
)

WORKLOADS = (
    "dashboard-100",
    "fare-search",
    "price-history",
    "price-calendar",
    "subscriptions",
    "collection-runs",
    "collection-operations",
)
LAYERS = ("service", "api")
SESSION_COOKIE_NAME = "farescope_session"


@dataclass(frozen=True, slots=True)
class BenchmarkUser:
    number: int
    user_id: UUID
    token: str
    contexts: tuple[SubscriptionFareContext, ...]


@dataclass(frozen=True, slots=True)
class MetricSlice:
    pool_acquire_ms: tuple[float, ...]
    statement_ms: tuple[float, ...]
    peak_checked_out: int


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    layer: str
    workload: str
    concurrency: int
    requests: int
    succeeded: int
    failed: int
    error_rate: float
    duration_seconds: float
    throughput_rps: float
    latency_ms: dict[str, float | None]
    pool_acquire_ms: dict[str, float | int | None]
    statements: dict[str, float | int | None]
    status_counts: dict[str, int]
    errors: dict[str, int]


class EngineMetrics:
    """Performance-only instrumentation around SQLAlchemy's async queue pool."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._pool = engine.sync_engine.pool
        self._original_do_get = self._pool._do_get  # noqa: SLF001
        self._pool_acquire_ms: list[float] = []
        self._statement_ms: list[float] = []
        self._checked_out = 0
        self._peak_checked_out = 0
        self._install()

    def _install(self) -> None:
        def timed_do_get() -> object:
            started = time.perf_counter()
            try:
                return self._original_do_get()
            finally:
                self._pool_acquire_ms.append((time.perf_counter() - started) * 1_000)

        self._pool._do_get = timed_do_get  # type: ignore[method-assign]  # noqa: SLF001

        def checkout(*_arguments: object) -> None:
            self._checked_out += 1
            self._peak_checked_out = max(self._peak_checked_out, self._checked_out)

        def checkin(*_arguments: object) -> None:
            self._checked_out -= 1

        def before_cursor_execute(
            _connection: object,
            _cursor: object,
            _statement: str,
            _parameters: object,
            context: object,
            _executemany: bool,
        ) -> None:
            context._farescope_perf_started = time.perf_counter()

        def after_cursor_execute(
            _connection: object,
            _cursor: object,
            _statement: str,
            _parameters: object,
            context: object,
            _executemany: bool,
        ) -> None:
            started = getattr(context, "_farescope_perf_started", None)
            if started is not None:
                self._statement_ms.append((time.perf_counter() - started) * 1_000)

        self._checkout_listener = checkout
        self._checkin_listener = checkin
        self._before_listener = before_cursor_execute
        self._after_listener = after_cursor_execute
        event.listen(self._pool, "checkout", checkout)
        event.listen(self._pool, "checkin", checkin)
        event.listen(self._engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        event.listen(self._engine.sync_engine, "after_cursor_execute", after_cursor_execute)

    def marker(self) -> tuple[int, int, int]:
        self._peak_checked_out = self._checked_out
        return (
            len(self._pool_acquire_ms),
            len(self._statement_ms),
            self._checked_out,
        )

    def since(self, marker: tuple[int, int, int]) -> MetricSlice:
        pool_index, statement_index, _checked_out_at_start = marker
        return MetricSlice(
            pool_acquire_ms=tuple(self._pool_acquire_ms[pool_index:]),
            statement_ms=tuple(self._statement_ms[statement_index:]),
            peak_checked_out=self._peak_checked_out,
        )

    def close(self) -> None:
        self._pool._do_get = self._original_do_get  # type: ignore[method-assign]  # noqa: SLF001
        event.remove(self._pool, "checkout", self._checkout_listener)
        event.remove(self._pool, "checkin", self._checkin_listener)
        event.remove(self._engine.sync_engine, "before_cursor_execute", self._before_listener)
        event.remove(self._engine.sync_engine, "after_cursor_execute", self._after_listener)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run concurrent FareScope service and in-process ASGI API read benchmarks."
    )
    parser.add_argument(
        "--confirm",
        required=True,
        help=f"Required exact value: {DISPOSABLE_CONFIRMATION}",
    )
    parser.add_argument(
        "--layers",
        default=",".join(LAYERS),
        help="Comma-separated subset of service,api.",
    )
    parser.add_argument(
        "--workloads",
        default=",".join(WORKLOADS),
        help=f"Comma-separated subset of {','.join(WORKLOADS)}.",
    )
    parser.add_argument("--concurrency", default="1,8,16,32")
    parser.add_argument("--requests-per-scenario", type=int, default=80)
    parser.add_argument("--warmup-requests", type=int, default=8)
    parser.add_argument("--users", type=int, default=64)
    parser.add_argument("--pool-size", type=int, default=8)
    parser.add_argument("--max-overflow", type=int, default=4)
    parser.add_argument("--pool-timeout-seconds", type=float, default=2.0)
    parser.add_argument(
        "--daily-trend-mode",
        choices=("aggregate", "raw"),
        default="aggregate",
        help="Use maintained daily aggregates or force the exact raw Dashboard trend path.",
    )
    parser.add_argument(
        "--client-cold-probe",
        action="store_true",
        help="Compare the first and second service call on a fresh client pool.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON result path; defaults to performance/results/concurrency-<UTC>.json.",
    )
    return parser.parse_args()


def _split_choices(value: str, *, allowed: Sequence[str], name: str) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    invalid = sorted(set(selected) - set(allowed))
    if not selected or invalid:
        raise ValueError(f"invalid {name}: {invalid or value!r}")
    return selected


def _parse_concurrency(value: str) -> tuple[int, ...]:
    levels = tuple(dict.fromkeys(int(item.strip()) for item in value.split(",") if item.strip()))
    if not levels or any(level <= 0 for level in levels):
        raise ValueError("concurrency values must be positive integers")
    return levels


def _fixture_uuid(label: str, number: int) -> UUID:
    return UUID(hashlib.md5(f"{label}{number}".encode()).hexdigest())


def _session_token(number: int) -> str:
    return f"farescope-performance-session-{number}"


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _prepare_fixture(database_url: str, *, user_count: int) -> None:
    connection = await asyncpg.connect(to_asyncpg_url(database_url))
    try:
        available_users = await connection.fetchval(
            "SELECT count(*) FROM users WHERE normalized_username LIKE 'perf-user-%'"
        )
        available_queries = await connection.fetchval(
            """
            SELECT count(*) FROM search_queries
            WHERE normalized_query @> '{"perf_fixture": true}'::jsonb
            """
        )
        if available_users < user_count:
            raise RuntimeError(
                f"benchmark needs {user_count} generated users, found {available_users}"
            )
        if available_queries < 100:
            raise RuntimeError(
                f"dashboard-100 needs at least 100 generated searches, found {available_queries}"
            )

        await connection.execute(
            """
            INSERT INTO subscriptions (
                id, user_id, search_query_id, name, enabled, poll_interval_seconds,
                next_due_at, last_collected_at, tags, created_at, updated_at
            )
            SELECT
                md5('farescope-perf-concurrency-sub-' || query_number)::uuid,
                md5('farescope-perf-user-1')::uuid,
                md5('farescope-perf-search-' || query_number)::uuid,
                format('Concurrency Dashboard Route %s', query_number),
                true,
                21600,
                current_timestamp + query_number * interval '1 second',
                current_timestamp - interval '6 hours',
                '["performance", "concurrency"]'::jsonb,
                current_timestamp - query_number * interval '1 millisecond',
                current_timestamp - query_number * interval '1 millisecond'
            FROM generate_series(13, 100) AS queries(query_number)
            ON CONFLICT DO NOTHING
            """
        )
        await connection.execute(
            """
            INSERT INTO subscription_filters (
                id, subscription_id, airline_codes, origin_airport_codes,
                destination_airport_codes, max_price_minor, currency, max_stops,
                max_duration_minutes, departure_time_start_minutes,
                departure_time_end_minutes, additional_filters, created_at, updated_at
            )
            SELECT
                md5('farescope-perf-concurrency-filter-' || query_number)::uuid,
                md5('farescope-perf-concurrency-sub-' || query_number)::uuid,
                '[]'::jsonb,
                '[]'::jsonb,
                '[]'::jsonb,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                NULL,
                '{"perf_fixture": true, "concurrency": true}'::jsonb,
                current_timestamp - query_number * interval '1 millisecond',
                current_timestamp - query_number * interval '1 millisecond'
            FROM generate_series(13, 100) AS queries(query_number)
            ON CONFLICT DO NOTHING
            """
        )
        expires_at = datetime.now(UTC) + timedelta(days=7)
        sessions = [
            (
                _fixture_uuid("farescope-perf-session-", number),
                _fixture_uuid("farescope-perf-user-", number),
                _session_digest(_session_token(number)),
                expires_at,
            )
            for number in range(1, user_count + 1)
        ]
        await connection.executemany(
            """
            INSERT INTO sessions (
                id, user_id, token_hash, expires_at, last_seen_at, revoked_at,
                ip_hash, user_agent, created_at
            )
            VALUES ($1, $2, $3, $4, NULL, NULL, NULL, 'FareScope benchmark', current_timestamp)
            ON CONFLICT (token_hash) DO UPDATE
            SET expires_at = EXCLUDED.expires_at, revoked_at = NULL
            """,
            sessions,
        )
        await connection.execute(
            """
            INSERT INTO schema_observations (
                id, provider_id, collection_run_id, endpoint, schema_fingerprint,
                field_summary, first_seen_at, last_seen_at, occurrence_count, created_at
            )
            SELECT
                md5('farescope-perf-schema-' || observation_number)::uuid,
                '00000000-0000-0000-0000-000000000001'::uuid,
                md5(
                    'farescope-perf-run-success-'
                    || (((observation_number - 1) % $1) + 1)
                )::uuid,
                CASE observation_number % 4
                    WHEN 0 THEN '/itinerary/api/12808/lowestPrice'
                    WHEN 1 THEN '/international/search/api/search/batchSearch'
                    WHEN 2 THEN '/itinerary/api/12808/products'
                    ELSE '/international/search/api/flightlist'
                END,
                md5('farescope-perf-schema-fingerprint-' || observation_number),
                jsonb_build_object(
                    'shape',
                    jsonb_build_object(
                        'data', 'object',
                        'status', 'string',
                        format('fixture_field_%s', observation_number % 20), 'array'
                    ),
                    'perf_fixture',
                    true
                ),
                current_timestamp - observation_number * interval '12 hours',
                current_timestamp - observation_number * interval '5 minutes',
                observation_number * 3,
                current_timestamp - observation_number * interval '12 hours'
            FROM generate_series(1, 200) AS observations(observation_number)
            ON CONFLICT DO NOTHING
            """,
            available_queries,
        )
        await connection.execute("ANALYZE subscriptions")
        await connection.execute("ANALYZE subscription_filters")
        await connection.execute("ANALYZE sessions")
        await connection.execute("ANALYZE schema_observations")
    finally:
        await connection.close()


async def _load_contexts(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 100,
) -> tuple[SubscriptionFareContext, ...]:
    subscriptions = (
        await session.scalars(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
            .limit(limit)
        )
    ).all()
    if not subscriptions:
        return ()
    query_ids = {item.search_query_id for item in subscriptions}
    subscription_ids = {item.id for item in subscriptions}
    queries = (
        await session.scalars(select(SearchQuery).where(SearchQuery.id.in_(query_ids)))
    ).all()
    filters = (
        await session.scalars(
            select(SubscriptionFilter).where(
                SubscriptionFilter.subscription_id.in_(subscription_ids)
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
    legs_by_query: dict[UUID, list[SearchLeg]] = defaultdict(list)
    for leg in legs:
        legs_by_query[leg.search_query_id].append(leg)
    return tuple(
        SubscriptionFareContext(
            subscription=subscription,
            search_query=query_map[subscription.search_query_id],
            filters=filter_map[subscription.id],
            legs=tuple(legs_by_query[subscription.search_query_id]),
        )
        for subscription in subscriptions
        if subscription.search_query_id in query_map and subscription.id in filter_map
    )


async def _load_users(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_count: int,
) -> tuple[BenchmarkUser, ...]:
    result: list[BenchmarkUser] = []
    async with factory() as session:
        for number in range(1, user_count + 1):
            user_id = _fixture_uuid("farescope-perf-user-", number)
            user = await session.get(User, user_id)
            if user is None:
                raise RuntimeError(f"generated performance user {number} is missing")
            contexts = await _load_contexts(session, user_id=user_id)
            if not contexts:
                raise RuntimeError(f"generated performance user {number} has no routes")
            result.append(
                BenchmarkUser(
                    number=number,
                    user_id=user_id,
                    token=_session_token(number),
                    contexts=contexts,
                )
            )
    if len(result[0].contexts) != 100:
        raise RuntimeError(
            f"dashboard benchmark user must have 100 routes; got {len(result[0].contexts)}"
        )
    return tuple(result)


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


def _context_for_calendar(user: BenchmarkUser) -> SubscriptionFareContext:
    return next(context for context in user.contexts if context.legs)


def _user_for_request(users: tuple[BenchmarkUser, ...], request_number: int) -> BenchmarkUser:
    return users[request_number % len(users)]


def _service_call(
    workload: str,
    *,
    factory: async_sessionmaker[AsyncSession],
    users: tuple[BenchmarkUser, ...],
    daily_trend_mode: str,
) -> Callable[[int], Awaitable[int | None]]:
    async def call(request_number: int) -> int | None:
        user = users[0] if workload == "dashboard-100" else _user_for_request(users, request_number)
        context = _context_for_calendar(user)
        now = datetime.now(UTC)
        async with factory() as session:
            if workload == "dashboard-100":
                contexts = await _load_contexts(session, user_id=user.user_id)
                await load_subscription_latest_fares(session, contexts=contexts)
                await load_dashboard_price_analytics(
                    session,
                    contexts=contexts,
                    as_of=now,
                    use_daily_aggregates=daily_trend_mode == "aggregate",
                )
                await load_dashboard_subscription_stats(session, user_id=user.user_id)
                await load_collection_health(session, user_id=user.user_id, now=now)
            elif workload == "fare-search":
                run_id = await session.scalar(
                    select(CollectionRun.id)
                    .where(
                        CollectionRun.search_query_id == context.search_query.id,
                        CollectionRun.status == CollectionStatus.SUCCEEDED.value,
                        CollectionRun.finished_at.is_not(None),
                    )
                    .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
                    .limit(1)
                )
                await _load_offers(
                    session,
                    run_id,
                    filters=_search_filters(context),
                    provider=context.search_query.provider,
                    currency=context.search_query.currency,
                    after=None,
                    limit=50,
                )
            elif workload == "price-history":
                await load_price_history(
                    session,
                    context=context,
                    since=now - timedelta(days=21),
                    as_of=now,
                    resolution="day",
                    limit=200,
                )
            elif workload == "price-calendar":
                round_trip = context.search_query.trip_type == "round_trip"
                departure_date = context.legs[0].departure_date
                return_date = context.legs[1].departure_date if round_trip else None
                await list_latest_calendar_prices(
                    session,
                    search_query_id=context.search_query.id,
                    currency=context.search_query.currency,
                    round_trip=round_trip,
                    departure_start=departure_date,
                    departure_end=departure_date + timedelta(days=180),
                    return_start=return_date,
                    return_end=(return_date + timedelta(days=180)) if return_date else None,
                    after=None,
                    limit=200,
                )
            elif workload == "subscriptions":
                await list_subscription_views(
                    session,
                    user_id=user.user_id,
                    limit=50,
                    as_of=now,
                )
            elif workload == "collection-runs":
                query_ids = select(Subscription.search_query_id).where(
                    Subscription.user_id == user.user_id
                )
                runs = (
                    await session.scalars(
                        select(CollectionRun)
                        .where(CollectionRun.search_query_id.in_(query_ids))
                        .order_by(CollectionRun.scheduled_at.desc(), CollectionRun.id.desc())
                        .limit(21)
                    )
                ).all()
                if runs:
                    await session.scalars(
                        select(SearchQuery).where(
                            SearchQuery.id.in_({run.search_query_id for run in runs})
                        )
                    )
                await load_collection_health(session, user_id=user.user_id, now=now)
            elif workload == "collection-operations":
                await _load_run_status_counts(session, user_id=user.user_id, now=now)
                await _load_schema_signals(
                    session,
                    user_id=user.user_id,
                    now=now,
                    limit=20,
                )
            else:  # pragma: no cover - validated by CLI
                raise ValueError(f"unknown workload: {workload}")
        return None

    return call


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "p50": None, "p95": None, "p99": None, "max": None}
    return {
        "min": round(min(values), 3),
        "p50": round(_percentile(values, 0.50) or 0, 3),
        "p95": round(_percentile(values, 0.95) or 0, 3),
        "p99": round(_percentile(values, 0.99) or 0, 3),
        "max": round(max(values), 3),
    }


async def _run_scenario(
    *,
    layer: str,
    workload: str,
    call: Callable[[int], Awaitable[int | None]],
    concurrency: int,
    request_count: int,
    warmup_count: int,
    metrics: EngineMetrics,
) -> ScenarioResult:
    for request_number in range(warmup_count):
        response_status = await call(request_number)
        if response_status is not None and response_status != 200:
            raise RuntimeError(
                f"warmup failed for {layer}/{workload}: HTTP {response_status}"
            )

    marker = metrics.marker()
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    succeeded = 0
    failed = 0
    start_gate = asyncio.Event()

    async def worker(worker_number: int) -> tuple[int, int]:
        worker_succeeded = 0
        worker_failed = 0
        await start_gate.wait()
        for request_number in range(worker_number, request_count, concurrency):
            started = time.perf_counter()
            try:
                response_status = await call(request_number)
                if response_status is not None:
                    statuses[str(response_status)] += 1
                    if response_status != 200:
                        raise RuntimeError(f"HTTP {response_status}")
                worker_succeeded += 1
            except Exception as error:  # benchmark must aggregate failures, not abort the matrix
                worker_failed += 1
                errors[f"{type(error).__name__}: {error}"] += 1
            finally:
                latencies.append((time.perf_counter() - started) * 1_000)
        return worker_succeeded, worker_failed

    workers = [asyncio.create_task(worker(number)) for number in range(concurrency)]
    started = time.perf_counter()
    start_gate.set()
    for worker_succeeded, worker_failed in await asyncio.gather(*workers):
        succeeded += worker_succeeded
        failed += worker_failed
    elapsed = time.perf_counter() - started
    metric_slice = metrics.since(marker)
    statement_count = len(metric_slice.statement_ms)
    pool_distribution = _distribution(metric_slice.pool_acquire_ms)
    return ScenarioResult(
        layer=layer,
        workload=workload,
        concurrency=concurrency,
        requests=request_count,
        succeeded=succeeded,
        failed=failed,
        error_rate=round(failed / request_count, 6),
        duration_seconds=round(elapsed, 6),
        throughput_rps=round(request_count / elapsed, 3),
        latency_ms=_distribution(latencies),
        pool_acquire_ms={
            **pool_distribution,
            "acquisitions": len(metric_slice.pool_acquire_ms),
            "over_1ms": sum(value > 1 for value in metric_slice.pool_acquire_ms),
            "peak_checked_out": metric_slice.peak_checked_out,
        },
        statements={
            **_distribution(metric_slice.statement_ms),
            "count": statement_count,
            "per_request": round(statement_count / request_count, 3),
        },
        status_counts=dict(sorted(statuses.items())),
        errors=dict(errors.most_common(5)),
    )


def _api_params(workload: str, user: BenchmarkUser) -> tuple[str, dict[str, str | int | bool]]:
    context = _context_for_calendar(user)
    if workload == "dashboard-100":
        return "/api/dashboard/overview", {}
    if workload == "fare-search":
        leg = context.legs[0]
        return (
            "/api/fares/search",
            {
                "origin": leg.origin_code,
                "destination": leg.destination_code,
                "departureDate": leg.departure_date.isoformat(),
                "tripType": "oneway",
                "directOnly": context.search_query.direct_only,
                "limit": 50,
            },
        )
    if workload == "price-history":
        return (
            "/api/prices/history",
            {
                "routeId": str(context.subscription.id),
                "days": 21,
                "resolution": "day",
                "limit": 200,
            },
        )
    if workload == "price-calendar":
        return (
            "/api/prices/calendar",
            {"routeId": str(context.subscription.id), "limit": 200},
        )
    if workload == "subscriptions":
        return "/api/subscriptions", {"limit": 50}
    if workload == "collection-runs":
        return "/api/collection/runs", {"limit": 20}
    if workload == "collection-operations":
        return "/api/collection/operations", {}
    raise ValueError(f"unknown workload: {workload}")


@asynccontextmanager
async def _stub_fare_collection(target_context: SubscriptionFareContext):
    import app.api.routes.fares as fares_routes

    original_get_or_create = fares_routes.get_or_create_canonical_search
    original_ensure = fares_routes.ensure_on_demand_collection_run
    original_dispatch = fares_routes.dispatch_collection_run_safely

    async def get_existing_query(
        database: AsyncSession,
        _search: object,
    ) -> tuple[SearchQuery, bool]:
        query = await database.get(SearchQuery, target_context.search_query.id)
        if query is None:
            raise RuntimeError("benchmark search query disappeared")
        return query, False

    async def get_existing_run(
        database: AsyncSession,
        *,
        search_query: SearchQuery,
    ) -> CollectionRun:
        run = await database.scalar(
            select(CollectionRun)
            .where(
                CollectionRun.search_query_id == search_query.id,
                CollectionRun.status == CollectionStatus.SUCCEEDED.value,
                CollectionRun.finished_at.is_not(None),
            )
            .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
            .limit(1)
        )
        if run is None:
            raise RuntimeError("benchmark successful collection run disappeared")
        return run

    async def no_dispatch(*_arguments: object, **_keyword_arguments: object) -> None:
        return None

    fares_routes.get_or_create_canonical_search = get_existing_query
    fares_routes.ensure_on_demand_collection_run = get_existing_run
    fares_routes.dispatch_collection_run_safely = no_dispatch
    try:
        yield
    finally:
        fares_routes.get_or_create_canonical_search = original_get_or_create
        fares_routes.ensure_on_demand_collection_run = original_ensure
        fares_routes.dispatch_collection_run_safely = original_dispatch


@asynccontextmanager
async def _stub_queue_depths():
    import app.services.collection_operations as collection_operations

    original = collection_operations.load_queue_depths

    async def unavailable(_redis_url: str) -> QueueDepths:
        return QueueDepths(
            available=False,
            collector=None,
            default=None,
            analysis=None,
            notifications=None,
        )

    collection_operations.load_queue_depths = unavailable
    try:
        yield
    finally:
        collection_operations.load_queue_depths = original


@asynccontextmanager
async def _configured_api_daily_trend_mode(mode: str):
    import app.api.routes.fares as fares_routes

    original = fares_routes.load_dashboard_price_analytics

    async def configured(*args: object, **kwargs: object):
        kwargs["use_daily_aggregates"] = mode == "aggregate"
        return await original(*args, **kwargs)

    fares_routes.load_dashboard_price_analytics = configured
    try:
        yield
    finally:
        fares_routes.load_dashboard_price_analytics = original


async def _service_matrix(
    *,
    database_url: str,
    users: tuple[BenchmarkUser, ...],
    workloads: tuple[str, ...],
    concurrency_levels: tuple[int, ...],
    request_count: int,
    warmup_count: int,
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: float,
    daily_trend_mode: str,
) -> list[ScenarioResult]:
    from app.db import create_engine, create_session_factory

    engine = create_engine(
        to_sqlalchemy_url(database_url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout_seconds=pool_timeout_seconds,
        application_name="farescope-performance-service",
    )
    metrics = EngineMetrics(engine)
    factory = create_session_factory(engine)
    results: list[ScenarioResult] = []
    try:
        for workload in workloads:
            call = _service_call(
                workload,
                factory=factory,
                users=users,
                daily_trend_mode=daily_trend_mode,
            )
            for concurrency in concurrency_levels:
                result = await _run_scenario(
                    layer="service",
                    workload=workload,
                    call=call,
                    concurrency=concurrency,
                    request_count=request_count,
                    warmup_count=warmup_count,
                    metrics=metrics,
                )
                results.append(result)
                _print_result(result)
    finally:
        metrics.close()
        await engine.dispose()
    return results


async def _api_matrix(
    *,
    database_url: str,
    users: tuple[BenchmarkUser, ...],
    workloads: tuple[str, ...],
    concurrency_levels: tuple[int, ...],
    request_count: int,
    warmup_count: int,
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: float,
    daily_trend_mode: str,
) -> list[ScenarioResult]:
    os.environ["FARESCOPE_DATABASE_URL"] = to_sqlalchemy_url(database_url)
    os.environ["FARESCOPE_DATABASE_POOL_SIZE"] = str(pool_size)
    os.environ["FARESCOPE_DATABASE_MAX_OVERFLOW"] = str(max_overflow)
    os.environ["FARESCOPE_DATABASE_POOL_TIMEOUT_SECONDS"] = str(pool_timeout_seconds)
    os.environ["FARESCOPE_ENVIRONMENT"] = "test"
    from app.main import create_app
    from app.settings import get_settings

    get_settings.cache_clear()
    app = create_app()
    results: list[ScenarioResult] = []
    async with app.router.lifespan_context(app):
        metrics = EngineMetrics(app.state.database_engine)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as client:
            target_context = _context_for_calendar(users[0])
            async with (
                _stub_fare_collection(target_context),
                _stub_queue_depths(),
                _configured_api_daily_trend_mode(daily_trend_mode),
            ):
                for workload in workloads:
                    async def call(request_number: int, workload: str = workload) -> int:
                        user = (
                            users[0]
                            if workload == "dashboard-100"
                            else _user_for_request(users, request_number)
                        )
                        path, params = _api_params(workload, user)
                        response = await client.get(
                            path,
                            params=params,
                            cookies={SESSION_COOKIE_NAME: user.token},
                        )
                        return response.status_code

                    for concurrency in concurrency_levels:
                        result = await _run_scenario(
                            layer="api",
                            workload=workload,
                            call=call,
                            concurrency=concurrency,
                            request_count=request_count,
                            warmup_count=warmup_count,
                            metrics=metrics,
                        )
                        results.append(result)
                        _print_result(result)
        metrics.close()
    get_settings.cache_clear()
    return results


async def _client_cold_probe(
    *,
    database_url: str,
    users: tuple[BenchmarkUser, ...],
    workloads: tuple[str, ...],
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: float,
    daily_trend_mode: str,
) -> list[dict[str, object]]:
    from app.db import create_engine, create_session_factory

    probes: list[dict[str, object]] = []
    for workload in workloads:
        engine = create_engine(
            to_sqlalchemy_url(database_url),
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout_seconds=pool_timeout_seconds,
            application_name="farescope-performance-client-cold",
        )
        metrics = EngineMetrics(engine)
        factory = create_session_factory(engine)
        call = _service_call(
            workload,
            factory=factory,
            users=users,
            daily_trend_mode=daily_trend_mode,
        )
        try:
            first_started = time.perf_counter()
            await call(0)
            first_ms = (time.perf_counter() - first_started) * 1_000
            second_started = time.perf_counter()
            await call(0)
            second_ms = (time.perf_counter() - second_started) * 1_000
            probes.append(
                {
                    "workload": workload,
                    "fresh_client_pool_first_ms": round(first_ms, 3),
                    "same_pool_second_ms": round(second_ms, 3),
                    "classification": "client/pool cold only",
                }
            )
        finally:
            metrics.close()
            await engine.dispose()
    return probes


async def _environment(database_url: str) -> dict[str, object]:
    connection = await asyncpg.connect(to_asyncpg_url(database_url))
    try:
        settings = await connection.fetchrow(
            """
            SELECT version() AS version,
                   current_setting('shared_buffers') AS shared_buffers,
                   current_setting('work_mem') AS work_mem,
                   current_setting('effective_cache_size') AS effective_cache_size,
                   current_setting('random_page_cost') AS random_page_cost,
                   current_setting('max_connections') AS max_connections
            """
        )
        counts = await connection.fetch(
            """
            SELECT relation, rows
            FROM (
                SELECT 'users'::text relation, count(*)::bigint rows FROM users
                WHERE normalized_username LIKE 'perf-user-%'
                UNION ALL SELECT 'subscriptions', count(*) FROM subscriptions
                WHERE tags @> '["performance"]'::jsonb
                UNION ALL SELECT 'search_queries', count(*) FROM search_queries
                WHERE normalized_query @> '{"perf_fixture": true}'::jsonb
                UNION ALL SELECT 'collection_runs', count(*) FROM collection_runs
                WHERE idempotency_key LIKE 'perf:%'
                UNION ALL SELECT 'itineraries', count(*) FROM itineraries
                WHERE itinerary_metadata @> '{"perf_fixture": true}'::jsonb
                UNION ALL SELECT 'fare_offers', count(*) FROM fare_offers
                WHERE offer_metadata @> '{"perf_fixture": true}'::jsonb
                UNION ALL SELECT 'price_observations', count(*) FROM price_observations
                WHERE offer_fingerprint LIKE 'perf:%'
                UNION ALL SELECT 'latest_calendar_prices', count(*)
                FROM latest_calendar_price_snapshots
                WHERE source_endpoint = 'performance-fixture'
                UNION ALL SELECT 'schema_observations', count(*) FROM schema_observations
                WHERE field_summary @> '{"perf_fixture": true}'::jsonb
            ) AS fixture_counts
            ORDER BY relation
            """
        )
        return {
            "database_url": redact_url(database_url),
            "database_size_bytes": await connection.fetchval(
                "SELECT pg_database_size(current_database())"
            ),
            "schema_revision": await connection.fetchval(
                "SELECT version_num FROM alembic_version"
            ),
            "postgresql": dict(settings) if settings else None,
            "rows": {row["relation"]: row["rows"] for row in counts},
            "machine": {
                "platform": platform.platform(),
                "processor": platform.processor(),
                "logical_cpus": os.cpu_count(),
            },
        }
    finally:
        await connection.close()


def _print_result(result: ScenarioResult) -> None:
    print(
        f"{result.layer:7} {result.workload:17} c={result.concurrency:<2} "
        f"rps={result.throughput_rps:>8.2f} "
        f"p50={result.latency_ms['p50']:>8}ms "
        f"p95={result.latency_ms['p95']:>8}ms "
        f"p99={result.latency_ms['p99']:>8}ms "
        f"errors={result.failed}/{result.requests} "
        f"pool-p95={result.pool_acquire_ms['p95']}ms"
    )


async def main() -> None:
    arguments = _arguments()
    require_confirmation(arguments.confirm)
    database_url = validate_performance_database_url(
        os.environ["FARESCOPE_PERF_DATABASE_URL"]
    )
    layers = _split_choices(arguments.layers, allowed=LAYERS, name="layers")
    workloads = _split_choices(arguments.workloads, allowed=WORKLOADS, name="workloads")
    concurrency_levels = _parse_concurrency(arguments.concurrency)
    if arguments.requests_per_scenario <= 0 or arguments.warmup_requests < 0:
        raise ValueError("request and warmup counts must be non-negative with requests > 0")
    if arguments.users < 1 or arguments.users > 500:
        raise ValueError("users must be between 1 and 500 for the reference fixture")
    if arguments.pool_size < 1 or arguments.max_overflow < 0:
        raise ValueError("pool_size must be positive and max_overflow non-negative")

    await _prepare_fixture(database_url, user_count=arguments.users)
    bootstrap_engine: AsyncEngine | None = None
    try:
        from app.db import create_engine, create_session_factory

        bootstrap_engine = create_engine(
            to_sqlalchemy_url(database_url),
            pool_size=arguments.pool_size,
            max_overflow=arguments.max_overflow,
            pool_timeout_seconds=arguments.pool_timeout_seconds,
            application_name="farescope-performance-bootstrap",
        )
        users = await _load_users(
            create_session_factory(bootstrap_engine),
            user_count=arguments.users,
        )
    finally:
        if bootstrap_engine is not None:
            await bootstrap_engine.dispose()

    client_cold = []
    if arguments.client_cold_probe:
        client_cold = await _client_cold_probe(
            database_url=database_url,
            users=users,
            workloads=workloads,
            pool_size=arguments.pool_size,
            max_overflow=arguments.max_overflow,
            pool_timeout_seconds=arguments.pool_timeout_seconds,
            daily_trend_mode=arguments.daily_trend_mode,
        )

    results: list[ScenarioResult] = []
    if "service" in layers:
        results.extend(
            await _service_matrix(
                database_url=database_url,
                users=users,
                workloads=workloads,
                concurrency_levels=concurrency_levels,
                request_count=arguments.requests_per_scenario,
                warmup_count=arguments.warmup_requests,
                pool_size=arguments.pool_size,
                max_overflow=arguments.max_overflow,
                pool_timeout_seconds=arguments.pool_timeout_seconds,
                daily_trend_mode=arguments.daily_trend_mode,
            )
        )
    if "api" in layers:
        results.extend(
            await _api_matrix(
                database_url=database_url,
                users=users,
                workloads=workloads,
                concurrency_levels=concurrency_levels,
                request_count=arguments.requests_per_scenario,
                warmup_count=arguments.warmup_requests,
                pool_size=arguments.pool_size,
                max_overflow=arguments.max_overflow,
                pool_timeout_seconds=arguments.pool_timeout_seconds,
                daily_trend_mode=arguments.daily_trend_mode,
            )
        )

    output = arguments.output or Path("performance/results") / (
        f"concurrency-{arguments.daily_trend_mode}-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": await _environment(database_url),
        "configuration": {
            "layers": layers,
            "workloads": workloads,
            "concurrency": concurrency_levels,
            "requests_per_scenario": arguments.requests_per_scenario,
            "warmup_requests": arguments.warmup_requests,
            "users": arguments.users,
            "pool_size": arguments.pool_size,
            "max_overflow": arguments.max_overflow,
            "pool_timeout_seconds": arguments.pool_timeout_seconds,
            "daily_trend_mode": arguments.daily_trend_mode,
            "api_transport": "httpx ASGITransport (full FastAPI stack, no socket/TLS)",
            "fare_search_api": (
                "real auth/read/serialization with benchmark-only canonical-search and "
                "collector-dispatch stubs; no provider I/O"
            ),
            "collection_operations_api": (
                "real auth/status/schema SQL and serialization with Redis LLEN stubbed to an "
                "immediate unavailable result"
            ),
        },
        "cache_scope": {
            "warm_runs": "PostgreSQL and OS caches are not cleared between scenarios",
            "client_cold_probe": client_cold,
            "true_postgresql_or_os_cold_cache": False,
            "reason": (
                "Clearing PostgreSQL shared buffers or the host page cache requires restarting "
                "or privileged cache eviction on a dedicated server. This shared local "
                "PostgreSQL was deliberately not restarted or purged."
            ),
        },
        "results": [asdict(result) for result in results],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {output}")


if __name__ == "__main__":
    asyncio.run(main())
