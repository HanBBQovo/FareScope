from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Uuid,
    and_,
    any_,
    cast,
    column,
    exists,
    extract,
    func,
    lateral,
    or_,
    select,
    true,
    tuple_,
    union_all,
    values,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

from app.api.pagination import BucketCursor, DatePairCursor, TimestampCursor
from app.domain.search import SearchFilters
from app.models import (
    CollectionRun,
    DailyTrendAggregate,
    DailyTrendAggregateCoverage,
    FareOffer,
    Itinerary,
    LatestCalendarPriceSnapshot,
    PriceObservation,
    SearchLeg,
    SearchQuery,
    Segment,
    Subscription,
    SubscriptionFilter,
)
from app.models.enums import CollectionStatus

HistoryResolution = Literal["raw", "hour", "day"]


@dataclass(frozen=True, slots=True)
class FareFilterSpec:
    direct_only: bool = False
    airline_codes: tuple[str, ...] = ()
    departure_airports: tuple[str, ...] = ()
    arrival_airports: tuple[str, ...] = ()
    max_price_minor: int | None = None
    max_stops: int | None = None
    max_duration_minutes: int | None = None
    departure_minute_start: int | None = None
    departure_minute_end: int | None = None

    @classmethod
    def from_search_filters(cls, filters: SearchFilters) -> FareFilterSpec:
        return cls(**filters.model_dump())

    @classmethod
    def from_subscription(
        cls,
        search_query: SearchQuery,
        filters: SubscriptionFilter,
    ) -> FareFilterSpec:
        return cls(
            direct_only=search_query.direct_only,
            airline_codes=tuple(filters.airline_codes),
            departure_airports=tuple(filters.origin_airport_codes),
            arrival_airports=tuple(filters.destination_airport_codes),
            max_price_minor=filters.max_price_minor,
            max_stops=filters.max_stops,
            max_duration_minutes=filters.max_duration_minutes,
            departure_minute_start=filters.departure_time_start_minutes,
            departure_minute_end=filters.departure_time_end_minutes,
        )

    @property
    def requires_itinerary_scan(self) -> bool:
        return bool(
            self.airline_codes
            or self.departure_airports
            or self.arrival_airports
            or self.max_stops is not None
            or self.max_duration_minutes is not None
            or self.departure_minute_start is not None
            or self.departure_minute_end is not None
        )

    @property
    def supports_daily_trend_aggregate(self) -> bool:
        return not self.requires_itinerary_scan and self.max_price_minor is None


@dataclass(frozen=True, slots=True)
class SubscriptionFareContext:
    subscription: Subscription
    search_query: SearchQuery
    filters: SubscriptionFilter
    legs: tuple[SearchLeg, ...]


@dataclass(frozen=True, slots=True)
class CalendarPriceItem:
    departure_date: date
    return_date: date | None
    currency: str
    lowest_price_minor: int
    total_price_minor: int | None
    observed_at: datetime
    direct_verified: bool


@dataclass(frozen=True, slots=True)
class CalendarPricePage:
    items: tuple[CalendarPriceItem, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    observed_at: datetime
    price_minor: int
    lowest_price_minor: int
    highest_price_minor: int
    average_price_minor: float
    sample_count: int
    row_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PriceHistoryPage:
    items: tuple[HistoryPoint, ...]
    has_more: bool
    minimum_price_minor: int | None
    maximum_price_minor: int | None
    average_price_minor: float | None
    sample_count: int


@dataclass(frozen=True, slots=True)
class CollectionHealthStats:
    last_success_at: datetime | None
    success_rate_24h: float | None
    next_scheduled_at: datetime | None


@dataclass(frozen=True, slots=True)
class SubscriptionLatestFare:
    subscription_id: UUID
    collection_run_id: UUID
    total_price_minor: int
    currency: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DashboardSubscriptionStats:
    active_subscriptions: int
    routes_tracked: int


@dataclass(frozen=True, slots=True)
class DashboardPriceAnalytics:
    trend: tuple[HistoryPoint, ...]
    price_change_percent: float | None


def _fare_filter_values(
    rows: list[tuple[object, ...]],
    *,
    name: str,
):
    return (
        values(
            column("branch_number", Integer),
            column("query_id", Uuid),
            column("currency", String(3)),
            column("direct_only", Boolean),
            column("airline_codes", ARRAY(String(8))),
            column("departure_airports", ARRAY(String(8))),
            column("arrival_airports", ARRAY(String(8))),
            column("max_price_minor", Integer),
            column("max_stops", Integer),
            column("max_duration_minutes", Integer),
            column("departure_minute_start", Integer),
            column("departure_minute_end", Integer),
            column("requires_itinerary_scan", Boolean),
            name=name,
        )
        .data(rows)
        .cte(name)
    )


def _fare_filter_row(
    branch_number: int,
    query_id: UUID,
    currency: str,
    filters: FareFilterSpec,
) -> tuple[object, ...]:
    return (
        branch_number,
        query_id,
        currency,
        filters.direct_only,
        filters.airline_codes,
        filters.departure_airports,
        filters.arrival_airports,
        filters.max_price_minor,
        filters.max_stops,
        filters.max_duration_minutes,
        filters.departure_minute_start,
        filters.departure_minute_end,
        filters.requires_itinerary_scan,
    )


def _dashboard_aggregate_filter_values(
    rows: list[tuple[int, UUID, str, bool]],
):
    return (
        values(
            column("branch_number", Integer),
            column("query_id", Uuid),
            column("currency", String(3)),
            column("direct_only", Boolean),
            name="dashboard_daily_aggregate_filters",
        )
        .data(rows)
        .cte("dashboard_daily_aggregate_filters")
    )


def _set_based_itinerary_filter_conditions(filter_rows) -> tuple[ColumnElement[bool], ...]:
    max_stops = cast(filter_rows.c.max_stops, Integer)
    max_duration_minutes = cast(filter_rows.c.max_duration_minutes, Integer)
    departure_minute_start = cast(filter_rows.c.departure_minute_start, Integer)
    departure_minute_end = cast(filter_rows.c.departure_minute_end, Integer)
    airline_segment = aliased(Segment)
    airline_match = exists(
        select(1).where(
            airline_segment.itinerary_id == Itinerary.id,
            airline_segment.marketing_airline_code == any_(filter_rows.c.airline_codes),
        )
    ).correlate(Itinerary, filter_rows)

    first_segment = aliased(Segment)
    departure_minute = extract("hour", first_segment.departure_local) * 60 + extract(
        "minute", first_segment.departure_local
    )
    first_match = exists(
        select(1).where(
            first_segment.itinerary_id == Itinerary.id,
            first_segment.leg_position == 0,
            first_segment.position == 0,
            or_(
                func.cardinality(filter_rows.c.departure_airports) == 0,
                first_segment.origin_airport_code == any_(filter_rows.c.departure_airports),
            ),
            or_(
                departure_minute_start.is_(None),
                departure_minute_end.is_(None),
                and_(
                    departure_minute >= departure_minute_start,
                    departure_minute < departure_minute_end,
                ),
            ),
        )
    ).correlate(Itinerary, filter_rows)
    needs_first_match = or_(
        func.cardinality(filter_rows.c.departure_airports) > 0,
        departure_minute_start.is_not(None),
    )

    arrival_segment = aliased(Segment)
    later_segment = aliased(Segment)
    has_later_segment = exists(
        select(1).where(
            later_segment.itinerary_id == arrival_segment.itinerary_id,
            later_segment.leg_position == 0,
            later_segment.position > arrival_segment.position,
        )
    ).correlate(arrival_segment)
    arrival_match = exists(
        select(1).where(
            arrival_segment.itinerary_id == Itinerary.id,
            arrival_segment.leg_position == 0,
            arrival_segment.destination_airport_code == any_(filter_rows.c.arrival_airports),
            ~has_later_segment,
        )
    ).correlate(Itinerary, filter_rows)

    return (
        or_(filter_rows.c.direct_only.is_(False), Itinerary.is_direct.is_(True)),
        or_(
            max_stops.is_(None),
            Itinerary.stop_count <= max_stops,
        ),
        or_(
            max_duration_minutes.is_(None),
            Itinerary.total_duration_minutes <= max_duration_minutes,
        ),
        or_(func.cardinality(filter_rows.c.airline_codes) == 0, airline_match),
        or_(~needs_first_match, first_match),
        or_(func.cardinality(filter_rows.c.arrival_airports) == 0, arrival_match),
    )


def _dashboard_run_minima_statement(
    filter_rows,
    *,
    since: datetime,
    as_of: datetime,
    requires_itinerary_scan: bool,
):
    max_price_minor = cast(filter_rows.c.max_price_minor, Integer)
    conditions: list[ColumnElement[bool]] = [
        PriceObservation.search_query_id == filter_rows.c.query_id,
        PriceObservation.currency == filter_rows.c.currency,
        or_(
            filter_rows.c.direct_only.is_(False),
            PriceObservation.is_direct.is_(True),
        ),
        or_(
            max_price_minor.is_(None),
            PriceObservation.total_price_minor <= max_price_minor,
        ),
    ]
    conditions.extend(
        (
            PriceObservation.observed_at >= since,
            PriceObservation.observed_at <= as_of,
        )
    )
    statement = select(
        PriceObservation.collection_run_id.label("run_id"),
        PriceObservation.observed_at.label("observed_at"),
        func.min(PriceObservation.total_price_minor).label("price_minor"),
    )
    if requires_itinerary_scan:
        statement = statement.join(
            Itinerary,
            Itinerary.id == PriceObservation.itinerary_id,
        )
        conditions.extend(
            (
                Itinerary.search_query_id == filter_rows.c.query_id,
                *_set_based_itinerary_filter_conditions(filter_rows),
            )
        )
    else:
        conditions.append(PriceObservation.is_lowest.is_(True))
    run_minima = lateral(
        statement.where(*conditions).group_by(
            PriceObservation.collection_run_id,
            PriceObservation.observed_at,
        )
    ).alias("dashboard_run_minima")
    return select(
        filter_rows.c.branch_number,
        run_minima.c.observed_at,
        run_minima.c.price_minor,
    ).select_from(filter_rows.join(run_minima, true()))


def _dashboard_raw_trend_contribution(statement, *, name: str):
    observations = statement.cte(name)
    bucket = func.date_trunc(
        "day",
        func.timezone("UTC", observations.c.observed_at),
    ).label("bucket")
    return select(
        observations.c.branch_number,
        bucket,
        func.min(observations.c.price_minor).label("lowest_price_minor"),
        func.max(observations.c.price_minor).label("highest_price_minor"),
        func.sum(observations.c.price_minor).label("price_sum_minor"),
        func.count().label("sample_count"),
    ).group_by(observations.c.branch_number, bucket)


def _dashboard_daily_trend_contribution(
    filter_rows,
    *,
    full_start: datetime,
    full_end: datetime,
):
    return (
        select(
            filter_rows.c.branch_number,
            cast(DailyTrendAggregate.observation_date, DateTime()).label("bucket"),
            DailyTrendAggregate.lowest_price_minor,
            DailyTrendAggregate.highest_price_minor,
            DailyTrendAggregate.price_sum_minor,
            DailyTrendAggregate.sample_count,
        )
        .select_from(filter_rows)
        .join(
            DailyTrendAggregate,
            and_(
                DailyTrendAggregate.search_query_id == filter_rows.c.query_id,
                DailyTrendAggregate.currency == filter_rows.c.currency,
                DailyTrendAggregate.direct_only == filter_rows.c.direct_only,
                DailyTrendAggregate.observation_date >= full_start.date(),
                DailyTrendAggregate.observation_date < full_end.date(),
            ),
        )
    )


def _dashboard_aggregate_coverage_groups(
    filter_rows,
    *,
    full_start: datetime,
    full_end: datetime,
):
    expected_day_count = (full_end - full_start).days
    coverage_counts = (
        select(
            filter_rows.c.branch_number,
            filter_rows.c.query_id,
            filter_rows.c.currency,
            filter_rows.c.direct_only,
            func.count(DailyTrendAggregateCoverage.observation_date).label("covered_day_count"),
        )
        .select_from(filter_rows)
        .outerjoin(
            DailyTrendAggregateCoverage,
            and_(
                DailyTrendAggregateCoverage.search_query_id == filter_rows.c.query_id,
                DailyTrendAggregateCoverage.observation_date >= full_start.date(),
                DailyTrendAggregateCoverage.observation_date < full_end.date(),
            ),
        )
        .group_by(
            filter_rows.c.branch_number,
            filter_rows.c.query_id,
            filter_rows.c.currency,
            filter_rows.c.direct_only,
        )
        .cte("dashboard_daily_coverage_counts")
    )
    columns = (
        coverage_counts.c.branch_number,
        coverage_counts.c.query_id,
        coverage_counts.c.currency,
        coverage_counts.c.direct_only,
    )
    covered_filters = (
        select(*columns)
        .where(coverage_counts.c.covered_day_count == expected_day_count)
        .cte("dashboard_covered_aggregate_filters")
    )
    fallback_filters = (
        select(*columns)
        .where(coverage_counts.c.covered_day_count != expected_day_count)
        .cte("dashboard_fallback_aggregate_filters")
    )
    return covered_filters, fallback_filters


def _dashboard_simple_run_minima_statement(
    filter_rows,
    *,
    range_start: datetime,
    range_end: datetime,
    range_end_inclusive: bool,
):
    end_condition = (
        PriceObservation.observed_at <= range_end
        if range_end_inclusive
        else PriceObservation.observed_at < range_end
    )
    run_minima = lateral(
        select(
            PriceObservation.observed_at.label("observed_at"),
            func.min(PriceObservation.total_price_minor).label("price_minor"),
        )
        .where(
            PriceObservation.search_query_id == filter_rows.c.query_id,
            PriceObservation.currency == filter_rows.c.currency,
            PriceObservation.observed_at >= range_start,
            end_condition,
            PriceObservation.is_lowest.is_(True),
            or_(
                filter_rows.c.direct_only.is_(False),
                PriceObservation.is_direct.is_(True),
            ),
        )
        .group_by(
            PriceObservation.collection_run_id,
            PriceObservation.observed_at,
        )
    ).alias("dashboard_simple_run_minima")
    return select(
        filter_rows.c.branch_number,
        run_minima.c.observed_at,
        run_minima.c.price_minor,
    ).select_from(filter_rows.join(run_minima, true()))


def _full_utc_day_range(since: datetime, as_of: datetime) -> tuple[datetime, datetime]:
    since_utc = since.astimezone(UTC)
    as_of_utc = as_of.astimezone(UTC)
    since_midnight = datetime.combine(since_utc.date(), datetime.min.time(), tzinfo=UTC)
    full_start = (
        since_midnight if since_utc == since_midnight else since_midnight + timedelta(days=1)
    )
    full_end = datetime.combine(as_of_utc.date(), datetime.min.time(), tzinfo=UTC)
    return full_start, max(full_start, full_end)


async def load_subscription_fare_context(
    session: AsyncSession,
    *,
    user_id: UUID,
    subscription_id: UUID,
) -> SubscriptionFareContext | None:
    row = (
        await session.execute(
            select(Subscription, SearchQuery, SubscriptionFilter)
            .join(SearchQuery, SearchQuery.id == Subscription.search_query_id)
            .join(
                SubscriptionFilter,
                SubscriptionFilter.subscription_id == Subscription.id,
            )
            .where(
                Subscription.id == subscription_id,
                Subscription.user_id == user_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    subscription, search_query, filters = row
    legs = tuple(
        (
            await session.scalars(
                select(SearchLeg)
                .where(SearchLeg.search_query_id == search_query.id)
                .order_by(SearchLeg.position)
            )
        ).all()
    )
    return SubscriptionFareContext(
        subscription=subscription,
        search_query=search_query,
        filters=filters,
        legs=legs,
    )


async def load_collection_health(
    session: AsyncSession,
    *,
    user_id: UUID,
    now: datetime,
) -> CollectionHealthStats:
    """Calculate health over all of the owner's routes, not just the visible run page."""

    query_ids = select(Subscription.search_query_id).where(
        Subscription.user_id == user_id,
        Subscription.enabled.is_(True),
    )
    day_ago = now - timedelta(hours=24)
    last_success_at = await session.scalar(
        select(CollectionRun.finished_at)
        .where(
            CollectionRun.search_query_id.in_(query_ids),
            CollectionRun.status == CollectionStatus.SUCCEEDED.value,
            CollectionRun.finished_at.is_not(None),
            CollectionRun.finished_at <= now,
        )
        .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
        .limit(1)
    )
    successful_24h, terminal_24h = (
        await session.execute(
            select(
                func.count()
                .filter(
                    CollectionRun.status == CollectionStatus.SUCCEEDED.value,
                )
                .label("successful_24h"),
                func.count().label("terminal_24h"),
            ).where(
                CollectionRun.search_query_id.in_(query_ids),
                CollectionRun.status.in_(
                    (
                        CollectionStatus.SUCCEEDED.value,
                        CollectionStatus.FAILED.value,
                        CollectionStatus.CANCELED.value,
                    )
                ),
                CollectionRun.finished_at >= day_ago,
                CollectionRun.finished_at <= now,
            )
        )
    ).one()
    next_due_at = await session.scalar(
        select(func.min(Subscription.next_due_at)).where(
            Subscription.user_id == user_id,
            Subscription.enabled.is_(True),
            Subscription.next_due_at.is_not(None),
        )
    )
    success_rate = successful_24h / terminal_24h * 100 if terminal_24h else None
    return CollectionHealthStats(
        last_success_at=last_success_at,
        success_rate_24h=success_rate,
        next_scheduled_at=next_due_at,
    )


async def load_dashboard_subscription_stats(
    session: AsyncSession,
    *,
    user_id: UUID,
) -> DashboardSubscriptionStats:
    active_subscriptions, routes_tracked = (
        await session.execute(
            select(
                func.count().filter(Subscription.enabled.is_(True)),
                func.count(func.distinct(Subscription.search_query_id)),
            ).where(Subscription.user_id == user_id)
        )
    ).one()
    return DashboardSubscriptionStats(
        active_subscriptions=int(active_subscriptions or 0),
        routes_tracked=int(routes_tracked or 0),
    )


async def load_dashboard_price_analytics(
    session: AsyncSession,
    *,
    contexts: tuple[SubscriptionFareContext, ...],
    as_of: datetime,
    days: int = 30,
    currency: str = "CNY",
    use_daily_aggregates: bool = True,
) -> DashboardPriceAnalytics:
    """Aggregate a bounded trend for the active, visible dashboard routes.

    Identical canonical-query/filter pairs are deduplicated. Different local filters remain
    separate because they represent distinct prices visible to the owner.
    """

    if not 2 <= days <= 90:
        raise ValueError("dashboard trend days must be between 2 and 90")
    if as_of.tzinfo is None:
        raise ValueError("dashboard trend snapshot must include a timezone")

    grouped_contexts: dict[tuple[UUID, FareFilterSpec], SubscriptionFareContext] = {}
    for context in contexts:
        if not context.subscription.enabled or context.search_query.currency != currency:
            continue
        key = (
            context.search_query.id,
            FareFilterSpec.from_subscription(context.search_query, context.filters),
        )
        grouped_contexts.setdefault(key, context)

    if not grouped_contexts:
        return DashboardPriceAnalytics(trend=(), price_change_percent=None)

    filter_entries = [
        (
            branch_number,
            context,
            filters,
        )
        for branch_number, ((_, filters), context) in enumerate(grouped_contexts.items())
    ]
    since = as_of - timedelta(days=days)
    full_start, full_end = _full_utc_day_range(since, as_of)
    aggregate_entries = (
        [entry for entry in filter_entries if entry[2].supports_daily_trend_aggregate]
        if use_daily_aggregates
        else []
    )
    raw_entries = [entry for entry in filter_entries if entry not in aggregate_entries]
    trend_statements = []

    for requires_itinerary_scan in (False, True):
        matching_entries = [
            entry
            for entry in raw_entries
            if entry[2].requires_itinerary_scan is requires_itinerary_scan
        ]
        if not matching_entries:
            continue
        filter_rows = _fare_filter_values(
            [
                _fare_filter_row(
                    branch_number,
                    context.search_query.id,
                    context.search_query.currency,
                    filters,
                )
                for branch_number, context, filters in matching_entries
            ],
            name=(
                "dashboard_raw_scan_filters"
                if requires_itinerary_scan
                else "dashboard_raw_simple_filters"
            ),
        )
        trend_statements.append(
            _dashboard_raw_trend_contribution(
                _dashboard_run_minima_statement(
                    filter_rows,
                    since=since,
                    as_of=as_of,
                    requires_itinerary_scan=requires_itinerary_scan,
                ),
                name=(
                    "dashboard_raw_scan_observations"
                    if requires_itinerary_scan
                    else "dashboard_raw_simple_observations"
                ),
            )
        )

    if aggregate_entries:
        aggregate_filter_rows = _dashboard_aggregate_filter_values(
            [
                (
                    branch_number,
                    context.search_query.id,
                    context.search_query.currency,
                    filters.direct_only,
                )
                for branch_number, context, filters in aggregate_entries
            ]
        )
        if full_start < full_end:
            covered_filter_rows, fallback_filter_rows = _dashboard_aggregate_coverage_groups(
                aggregate_filter_rows,
                full_start=full_start,
                full_end=full_end,
            )
            trend_statements.append(
                _dashboard_daily_trend_contribution(
                    covered_filter_rows,
                    full_start=full_start,
                    full_end=full_end,
                )
            )
            trend_statements.append(
                _dashboard_raw_trend_contribution(
                    _dashboard_simple_run_minima_statement(
                        fallback_filter_rows,
                        range_start=since,
                        range_end=as_of,
                        range_end_inclusive=True,
                    ),
                    name="dashboard_fallback_aggregate_observations",
                )
            )
            if since < full_start:
                trend_statements.append(
                    _dashboard_raw_trend_contribution(
                        _dashboard_simple_run_minima_statement(
                            covered_filter_rows,
                            range_start=since,
                            range_end=full_start,
                            range_end_inclusive=False,
                        ),
                        name="dashboard_aggregate_start_boundary_observations",
                    )
                )
            if full_end <= as_of:
                trend_statements.append(
                    _dashboard_raw_trend_contribution(
                        _dashboard_simple_run_minima_statement(
                            covered_filter_rows,
                            range_start=max(full_end, since),
                            range_end=as_of,
                            range_end_inclusive=True,
                        ),
                        name="dashboard_aggregate_end_boundary_observations",
                    )
                )
        else:
            trend_statements.append(
                _dashboard_raw_trend_contribution(
                    _dashboard_simple_run_minima_statement(
                        aggregate_filter_rows,
                        range_start=since,
                        range_end=as_of,
                        range_end_inclusive=True,
                    ),
                    name="dashboard_no_full_day_observations",
                )
            )

    contribution_statement = (
        trend_statements[0] if len(trend_statements) == 1 else union_all(*trend_statements)
    )
    contributions = contribution_statement.cte("dashboard_trend_contributions")

    rolling_statements = []
    previous_start = as_of - timedelta(hours=48)
    current_start = as_of - timedelta(hours=24)
    for requires_itinerary_scan in (False, True):
        matching_entries = [
            entry
            for entry in filter_entries
            if entry[2].requires_itinerary_scan is requires_itinerary_scan
        ]
        if not matching_entries:
            continue
        filter_rows = _fare_filter_values(
            [
                _fare_filter_row(
                    branch_number,
                    context.search_query.id,
                    context.search_query.currency,
                    filters,
                )
                for branch_number, context, filters in matching_entries
            ],
            name=(
                "dashboard_rolling_scan_filters"
                if requires_itinerary_scan
                else "dashboard_rolling_simple_filters"
            ),
        )
        rolling_statements.append(
            _dashboard_run_minima_statement(
                filter_rows,
                since=previous_start,
                as_of=as_of,
                requires_itinerary_scan=requires_itinerary_scan,
            )
        )
    rolling_statement = (
        rolling_statements[0] if len(rolling_statements) == 1 else union_all(*rolling_statements)
    )
    rolling = rolling_statement.cte("dashboard_rolling_observations")
    current_price = (
        select(func.min(rolling.c.price_minor))
        .where(rolling.c.observed_at >= current_start)
        .scalar_subquery()
    )
    previous_price = (
        select(func.min(rolling.c.price_minor))
        .where(
            rolling.c.observed_at >= previous_start,
            rolling.c.observed_at < current_start,
        )
        .scalar_subquery()
    )
    sample_count = func.sum(contributions.c.sample_count)
    price_sum = cast(func.sum(contributions.c.price_sum_minor), Numeric(38, 0))
    rows = (
        await session.execute(
            select(
                contributions.c.bucket,
                func.min(contributions.c.lowest_price_minor).label("lowest_price_minor"),
                func.max(contributions.c.highest_price_minor).label("highest_price_minor"),
                (price_sum / cast(func.nullif(sample_count, 0), Numeric(38, 0))).label(
                    "average_price_minor"
                ),
                cast(sample_count, BigInteger).label("sample_count"),
                current_price.label("current_price_minor"),
                previous_price.label("previous_price_minor"),
            )
            .group_by(contributions.c.bucket)
            .order_by(contributions.c.bucket)
            .limit(days + 1)
        )
    ).all()
    if not rows:
        return DashboardPriceAnalytics(trend=(), price_change_percent=None)

    trend = tuple(
        HistoryPoint(
            observed_at=_as_utc(row.bucket),
            price_minor=row.lowest_price_minor,
            lowest_price_minor=row.lowest_price_minor,
            highest_price_minor=row.highest_price_minor,
            average_price_minor=float(row.average_price_minor),
            sample_count=int(row.sample_count),
        )
        for row in rows
    )
    current = rows[0].current_price_minor
    previous = rows[0].previous_price_minor
    price_change_percent = (
        (current - previous) / previous * 100
        if current is not None and previous not in (None, 0)
        else None
    )
    return DashboardPriceAnalytics(
        trend=trend,
        price_change_percent=price_change_percent,
    )


async def load_subscription_latest_fares(
    session: AsyncSession,
    *,
    contexts: tuple[SubscriptionFareContext, ...],
) -> dict[UUID, SubscriptionLatestFare]:
    if not contexts:
        return {}

    grouped: dict[tuple[UUID, str, FareFilterSpec], list[UUID]] = {}
    for context in contexts:
        key = (
            context.search_query.id,
            context.search_query.currency,
            FareFilterSpec.from_subscription(context.search_query, context.filters),
        )
        grouped.setdefault(key, []).append(context.subscription.id)

    branch_keys = list(grouped)
    filter_rows = _fare_filter_values(
        [
            _fare_filter_row(branch_number, query_id, currency, filters)
            for branch_number, (query_id, currency, filters) in enumerate(branch_keys)
        ],
        name="latest_fare_filters",
    )
    latest_run = lateral(
        select(
            CollectionRun.id.label("run_id"),
            CollectionRun.finished_at.label("finished_at"),
        )
        .where(
            CollectionRun.search_query_id == filter_rows.c.query_id,
            CollectionRun.status == CollectionStatus.SUCCEEDED.value,
            CollectionRun.finished_at.is_not(None),
        )
        .order_by(CollectionRun.finished_at.desc(), CollectionRun.id.desc())
        .limit(1)
    ).alias("latest_successful_run")
    best_offer = lateral(
        select(
            FareOffer.total_price_minor.label("total_price_minor"),
            FareOffer.currency.label("currency"),
        )
        .join(Itinerary, Itinerary.id == FareOffer.itinerary_id)
        .where(
            FareOffer.collection_run_id == latest_run.c.run_id,
            FareOffer.currency == filter_rows.c.currency,
            Itinerary.search_query_id == filter_rows.c.query_id,
            or_(
                cast(filter_rows.c.max_price_minor, Integer).is_(None),
                FareOffer.total_price_minor <= cast(filter_rows.c.max_price_minor, Integer),
            ),
            *_set_based_itinerary_filter_conditions(filter_rows),
        )
        .order_by(FareOffer.total_price_minor, FareOffer.id)
        .limit(1)
    ).alias("best_filtered_offer")
    rows = (
        await session.execute(
            select(
                filter_rows.c.branch_number,
                latest_run.c.run_id,
                latest_run.c.finished_at,
                best_offer.c.total_price_minor,
                best_offer.c.currency,
            ).select_from(filter_rows.join(latest_run, true()).join(best_offer, true()))
        )
    ).all()
    result: dict[UUID, SubscriptionLatestFare] = {}
    for row in rows:
        key = branch_keys[row.branch_number]
        if row.finished_at is None:
            continue
        for subscription_id in grouped[key]:
            result[subscription_id] = SubscriptionLatestFare(
                subscription_id=subscription_id,
                collection_run_id=row.run_id,
                total_price_minor=row.total_price_minor,
                currency=row.currency,
                observed_at=row.finished_at,
            )
    return result


async def list_latest_calendar_prices(
    session: AsyncSession,
    *,
    search_query_id: UUID,
    currency: str,
    round_trip: bool,
    departure_start: date,
    departure_end: date,
    return_start: date | None,
    return_end: date | None,
    after: DatePairCursor | None,
    limit: int,
) -> CalendarPricePage:
    if not 1 <= limit <= 500:
        raise ValueError("calendar page limit must be between 1 and 500")

    return_sort = (
        LatestCalendarPriceSnapshot.return_date
        if round_trip
        else func.coalesce(LatestCalendarPriceSnapshot.return_date, date.min)
    )
    statement = (
        select(LatestCalendarPriceSnapshot)
        .where(
            LatestCalendarPriceSnapshot.search_query_id == search_query_id,
            LatestCalendarPriceSnapshot.currency == currency,
            LatestCalendarPriceSnapshot.departure_date >= departure_start,
            LatestCalendarPriceSnapshot.departure_date <= departure_end,
        )
        .order_by(
            LatestCalendarPriceSnapshot.departure_date,
            return_sort,
        )
        .limit(limit + 1)
    )
    statement = statement.where(
        LatestCalendarPriceSnapshot.return_date.is_not(None)
        if round_trip
        else LatestCalendarPriceSnapshot.return_date.is_(None)
    )
    if return_start is not None:
        statement = statement.where(
            LatestCalendarPriceSnapshot.return_date.is_not(None),
            LatestCalendarPriceSnapshot.return_date >= return_start,
        )
    if return_end is not None:
        statement = statement.where(
            LatestCalendarPriceSnapshot.return_date.is_not(None),
            LatestCalendarPriceSnapshot.return_date <= return_end,
        )
    if after is not None:
        statement = statement.where(
            tuple_(LatestCalendarPriceSnapshot.departure_date, return_sort)
            > tuple_(after.departure_date, after.return_date or date.min)
        )

    rows = (await session.scalars(statement)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return CalendarPricePage(
        items=tuple(
            CalendarPriceItem(
                departure_date=row.departure_date,
                return_date=row.return_date,
                currency=row.currency,
                lowest_price_minor=row.lowest_price_minor,
                total_price_minor=row.total_price_minor,
                observed_at=row.observed_at,
                direct_verified=row.direct_verified,
            )
            for row in rows
        ),
        has_more=has_more,
    )


async def load_price_history(
    session: AsyncSession,
    *,
    context: SubscriptionFareContext,
    since: datetime,
    as_of: datetime,
    resolution: HistoryResolution,
    limit: int,
    after: TimestampCursor | BucketCursor | None = None,
) -> PriceHistoryPage:
    if not 1 <= limit <= 500:
        raise ValueError("history page limit must be between 1 and 500")
    if resolution == "raw" and after is not None and not isinstance(after, TimestampCursor):
        raise ValueError("raw history requires a timestamp cursor")
    if resolution != "raw" and after is not None and not isinstance(after, BucketCursor):
        raise ValueError("aggregated history requires a bucket cursor")

    filters = FareFilterSpec.from_subscription(context.search_query, context.filters)
    run_minima = _history_run_minima(
        search_query=context.search_query,
        filters=filters,
        since=since,
        as_of=as_of,
    )
    stats_row = (
        await session.execute(
            select(
                func.min(run_minima.c.price_minor),
                func.max(run_minima.c.price_minor),
                func.avg(run_minima.c.price_minor),
                func.count(),
            ).select_from(run_minima)
        )
    ).one()
    minimum, maximum, average, sample_count = stats_row

    if resolution == "raw":
        statement = select(
            run_minima.c.run_id,
            run_minima.c.observed_at,
            run_minima.c.price_minor,
        )
        if after is not None:
            statement = statement.where(
                tuple_(run_minima.c.observed_at, run_minima.c.run_id)
                > tuple_(after.timestamp, after.row_id)
            )
        rows = (
            await session.execute(
                statement.order_by(run_minima.c.observed_at, run_minima.c.run_id).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(
            HistoryPoint(
                observed_at=row.observed_at,
                price_minor=row.price_minor,
                lowest_price_minor=row.price_minor,
                highest_price_minor=row.price_minor,
                average_price_minor=float(row.price_minor),
                sample_count=1,
                row_id=row.run_id,
            )
            for row in rows
        )
    else:
        bucket = func.date_trunc(
            resolution,
            func.timezone("UTC", run_minima.c.observed_at),
        ).label("bucket")
        aggregate = (
            select(
                bucket,
                func.min(run_minima.c.price_minor).label("lowest_price_minor"),
                func.max(run_minima.c.price_minor).label("highest_price_minor"),
                func.avg(run_minima.c.price_minor).label("average_price_minor"),
                func.count().label("sample_count"),
            )
            .group_by(bucket)
            .subquery("history_buckets")
        )
        statement = select(aggregate)
        if isinstance(after, BucketCursor):
            statement = statement.where(aggregate.c.bucket > after.bucket)
        rows = (
            await session.execute(statement.order_by(aggregate.c.bucket).limit(limit + 1))
        ).all()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(
            HistoryPoint(
                observed_at=_as_utc(row.bucket),
                price_minor=row.lowest_price_minor,
                lowest_price_minor=row.lowest_price_minor,
                highest_price_minor=row.highest_price_minor,
                average_price_minor=float(row.average_price_minor),
                sample_count=row.sample_count,
            )
            for row in rows
        )

    return PriceHistoryPage(
        items=items,
        has_more=has_more,
        minimum_price_minor=minimum,
        maximum_price_minor=maximum,
        average_price_minor=float(average) if average is not None else None,
        sample_count=int(sample_count or 0),
    )


def itinerary_filter_conditions(filters: FareFilterSpec) -> tuple[ColumnElement[bool], ...]:
    conditions: list[ColumnElement[bool]] = []
    if filters.direct_only:
        conditions.append(Itinerary.is_direct.is_(True))
    if filters.max_stops is not None:
        conditions.append(Itinerary.stop_count <= filters.max_stops)
    if filters.max_duration_minutes is not None:
        conditions.append(Itinerary.total_duration_minutes <= filters.max_duration_minutes)

    if filters.airline_codes:
        airline_segment = aliased(Segment)
        conditions.append(
            exists(
                select(1).where(
                    airline_segment.itinerary_id == Itinerary.id,
                    airline_segment.marketing_airline_code.in_(filters.airline_codes),
                )
            )
        )

    if filters.departure_airports or filters.departure_minute_start is not None:
        first_segment = aliased(Segment)
        first_conditions: list[ColumnElement[bool]] = [
            first_segment.itinerary_id == Itinerary.id,
            first_segment.leg_position == 0,
            first_segment.position == 0,
        ]
        if filters.departure_airports:
            first_conditions.append(
                first_segment.origin_airport_code.in_(filters.departure_airports)
            )
        if filters.departure_minute_start is not None and filters.departure_minute_end is not None:
            departure_minute = extract("hour", first_segment.departure_local) * 60 + extract(
                "minute", first_segment.departure_local
            )
            first_conditions.extend(
                (
                    departure_minute >= filters.departure_minute_start,
                    departure_minute < filters.departure_minute_end,
                )
            )
        conditions.append(exists(select(1).where(*first_conditions)))

    if filters.arrival_airports:
        arrival_segment = aliased(Segment)
        later_segment = aliased(Segment)
        has_later_segment = exists(
            select(1).where(
                later_segment.itinerary_id == arrival_segment.itinerary_id,
                later_segment.leg_position == 0,
                later_segment.position > arrival_segment.position,
            )
        ).correlate(arrival_segment)
        conditions.append(
            exists(
                select(1).where(
                    arrival_segment.itinerary_id == Itinerary.id,
                    arrival_segment.leg_position == 0,
                    arrival_segment.destination_airport_code.in_(filters.arrival_airports),
                    ~has_later_segment,
                )
            )
        )
    return tuple(conditions)


def resolve_history_resolution(
    requested: Literal["auto", "raw", "hour", "day"],
    *,
    days: int,
) -> HistoryResolution:
    if requested == "auto":
        return "hour" if days <= 14 else "day"
    return requested


def validate_calendar_window(start: date, end: date, *, maximum_days: int = 366) -> None:
    if end < start:
        raise ValueError("calendar range end cannot precede its start")
    if end - start > timedelta(days=maximum_days):
        raise ValueError(f"calendar range cannot exceed {maximum_days} days")


def validate_calendar_cursor_mode(cursor: DatePairCursor, *, round_trip: bool) -> None:
    if round_trip and cursor.return_date is None:
        raise ValueError("roundtrip calendar cursor requires a return date")
    if not round_trip and cursor.return_date is not None:
        raise ValueError("one-way calendar cursor cannot contain a return date")


def _history_run_minima(
    *,
    search_query: SearchQuery,
    filters: FareFilterSpec,
    since: datetime,
    as_of: datetime,
    cte_name: str = "history_run_minima",
) -> CTE:
    conditions: list[ColumnElement[bool]] = [
        PriceObservation.search_query_id == search_query.id,
        PriceObservation.currency == search_query.currency,
        PriceObservation.observed_at >= since,
        PriceObservation.observed_at <= as_of,
    ]
    if filters.direct_only:
        conditions.append(PriceObservation.is_direct.is_(True))
    if filters.max_price_minor is not None:
        conditions.append(PriceObservation.total_price_minor <= filters.max_price_minor)
    if not filters.requires_itinerary_scan:
        conditions.append(PriceObservation.is_lowest.is_(True))

    statement = (
        select(
            PriceObservation.collection_run_id.label("run_id"),
            PriceObservation.observed_at.label("observed_at"),
            func.min(PriceObservation.total_price_minor).label("price_minor"),
        )
        .where(*conditions)
        .group_by(PriceObservation.collection_run_id, PriceObservation.observed_at)
    )
    if filters.requires_itinerary_scan:
        statement = statement.join(
            Itinerary,
            Itinerary.id == PriceObservation.itinerary_id,
        ).where(
            Itinerary.search_query_id == search_query.id,
            *itinerary_filter_conditions(filters),
        )
    return statement.cte(cte_name)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
