"""Maintained UTC-day summaries for bounded Dashboard trend reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    String,
    Uuid,
    and_,
    cast,
    column,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    true,
    union_all,
    values,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory_locks import acquire_observation_partition_shared_lock
from app.db.partitions import (
    month_start,
    price_observation_partition_name,
    shift_month,
)
from app.models import (
    DailyTrendAggregate,
    DailyTrendAggregateCoverage,
    PriceObservation,
    Subscription,
)


@dataclass(frozen=True, slots=True)
class DailyTrendRefreshResult:
    search_query_id: UUID
    observation_date: date
    aggregate_count: int
    source_last_observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class DailyTrendMaintenanceResult:
    refreshed: tuple[DailyTrendRefreshResult, ...]
    next_cursor: tuple[date, UUID] | None

    @property
    def day_count(self) -> int:
        return len(self.refreshed)

    @property
    def aggregate_count(self) -> int:
        return sum(item.aggregate_count for item in self.refreshed)


class DailyTrendSourceUnavailableError(RuntimeError):
    def __init__(
        self,
        *,
        start_date: date,
        end_date: date,
        archived_partitions: tuple[str, ...] = (),
        missing_hot_partitions: tuple[str, ...] = (),
    ) -> None:
        self.start_date = start_date
        self.end_date = end_date
        self.archived_partitions = archived_partitions
        self.missing_hot_partitions = missing_hot_partitions
        reasons = []
        if archived_partitions:
            reasons.append(f"detached archive overlap: {', '.join(archived_partitions)}")
        if missing_hot_partitions:
            reasons.append(f"hot source unavailable: {', '.join(missing_hot_partitions)}")
        super().__init__(
            f"daily trend source is not provably complete for {start_date} through {end_date}"
            + (f" ({'; '.join(reasons)})" if reasons else "")
        )


def utc_day_bounds(observation_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(observation_date, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _requested_partition_names(start_date: date, end_date: date) -> tuple[str, ...]:
    current = month_start(start_date)
    final = month_start(end_date)
    names = []
    while current <= final:
        names.append(price_observation_partition_name(current))
        current = shift_month(current, 1)
    return tuple(names)


async def _require_complete_hot_source(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
) -> None:
    await acquire_observation_partition_shared_lock(session)
    requested = set(_requested_partition_names(start_date, end_date))
    attached = {
        str(value)
        for value in await session.scalars(
            text(
                """
                SELECT child.relname
                FROM pg_inherits
                JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                JOIN pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
                WHERE parent_ns.nspname = 'public'
                  AND parent.relname = 'price_observations'
                  AND child_ns.nspname = 'public'
                  AND child.relname ~ '^price_observations_y[0-9]{4}m(0[1-9]|1[0-2])$'
                """
            )
        )
    }
    archived = {
        str(value)
        for value in await session.scalars(
            text(
                """
                SELECT child.relname
                FROM pg_class AS child
                JOIN pg_namespace AS namespace ON namespace.oid = child.relnamespace
                WHERE namespace.nspname = 'farescope_archive'
                  AND child.relkind IN ('r', 'p')
                  AND child.relname ~ '^price_observations_y[0-9]{4}m(0[1-9]|1[0-2])$'
                """
            )
        )
    }
    archived_partitions = tuple(sorted(requested & archived))
    missing_hot_partitions = tuple(sorted(requested - attached - archived))
    if archived_partitions or missing_hot_partitions:
        raise DailyTrendSourceUnavailableError(
            start_date=start_date,
            end_date=end_date,
            archived_partitions=archived_partitions,
            missing_hot_partitions=missing_hot_partitions,
        )


async def refresh_daily_trend_day(
    session: AsyncSession,
    *,
    search_query_id: UUID,
    observation_date: date,
) -> DailyTrendRefreshResult:
    """Rebuild one query/day from raw observations inside the caller transaction.

    A transaction advisory lock serializes collection retries and maintenance for the
    same query/day. Delete-and-rebuild keeps the operation idempotent and also handles
    corrected source rows without additive drift.
    """

    result = await _refresh_daily_trend_days(
        session,
        ((search_query_id, observation_date),),
    )
    return result[0]


async def _refresh_daily_trend_days(
    session: AsyncSession,
    keys: tuple[tuple[UUID, date], ...],
) -> tuple[DailyTrendRefreshResult, ...]:
    if not keys:
        return ()
    ordered_keys = tuple(sorted(set(keys), key=lambda item: (item[1], item[0])))
    candidate_days = (
        values(
            column("search_query_id", Uuid),
            column("observation_date", Date),
            name="daily_trend_candidates",
        )
        .data(ordered_keys)
        .cte("daily_trend_candidates")
    )
    ordered_candidates = (
        select(candidate_days.c.search_query_id, candidate_days.c.observation_date)
        .order_by(candidate_days.c.observation_date, candidate_days.c.search_query_id)
        .subquery("ordered_daily_trend_candidates")
    )
    lock_key = (
        literal("daily-trend:")
        + cast(ordered_candidates.c.search_query_id, String)
        + literal(":")
        + cast(ordered_candidates.c.observation_date, String)
    )
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0)))
        .select_from(ordered_candidates)
        .order_by(
            ordered_candidates.c.observation_date,
            ordered_candidates.c.search_query_id,
        )
    )

    day_start = func.timezone(
        "UTC",
        cast(candidate_days.c.observation_date, DateTime()),
    )
    observation_join = and_(
        PriceObservation.search_query_id == candidate_days.c.search_query_id,
        PriceObservation.observed_at >= day_start,
        PriceObservation.observed_at < day_start + timedelta(days=1),
        PriceObservation.is_lowest.is_(True),
    )
    source_days = (
        select(
            candidate_days.c.search_query_id,
            candidate_days.c.observation_date,
            func.max(PriceObservation.observed_at).label("source_last_observed_at"),
        )
        .select_from(candidate_days)
        .outerjoin(PriceObservation, observation_join)
        .group_by(candidate_days.c.search_query_id, candidate_days.c.observation_date)
        .cte("daily_trend_source_summary")
    )
    source_rows = (await session.execute(select(source_days))).all()
    source_last_by_key = {
        (row.search_query_id, row.observation_date): row.source_last_observed_at
        for row in source_rows
    }

    await session.execute(
        delete(DailyTrendAggregate).where(
            exists(
                select(1)
                .select_from(candidate_days)
                .where(
                    candidate_days.c.search_query_id == DailyTrendAggregate.search_query_id,
                    candidate_days.c.observation_date == DailyTrendAggregate.observation_date,
                )
            )
        )
    )

    run_minima_statements = []
    for direct_only in (False, True):
        join_conditions = [observation_join]
        if direct_only:
            join_conditions.append(PriceObservation.is_direct.is_(True))
        run_minima_statements.append(
            select(
                candidate_days.c.search_query_id,
                candidate_days.c.observation_date,
                PriceObservation.currency.label("currency"),
                literal(direct_only).label("direct_only"),
                PriceObservation.collection_run_id.label("collection_run_id"),
                PriceObservation.observed_at.label("observed_at"),
                func.min(PriceObservation.total_price_minor).label("price_minor"),
            )
            .select_from(candidate_days)
            .join(PriceObservation, and_(*join_conditions))
            .group_by(
                candidate_days.c.search_query_id,
                candidate_days.c.observation_date,
                PriceObservation.currency,
                PriceObservation.collection_run_id,
                PriceObservation.observed_at,
            )
        )
    run_minima = union_all(*run_minima_statements).cte("daily_trend_run_minima")
    daily = select(
        run_minima.c.search_query_id,
        run_minima.c.observation_date,
        run_minima.c.currency,
        run_minima.c.direct_only,
        func.min(run_minima.c.price_minor).label("lowest_price_minor"),
        func.max(run_minima.c.price_minor).label("highest_price_minor"),
        func.sum(run_minima.c.price_minor).label("price_sum_minor"),
        func.count().label("sample_count"),
        func.min(run_minima.c.observed_at).label("first_observed_at"),
        func.max(run_minima.c.observed_at).label("last_observed_at"),
    ).group_by(
        run_minima.c.search_query_id,
        run_minima.c.observation_date,
        run_minima.c.currency,
        run_minima.c.direct_only,
    )
    aggregate_insert = (
        pg_insert(DailyTrendAggregate)
        .from_select(
            [
                DailyTrendAggregate.search_query_id,
                DailyTrendAggregate.observation_date,
                DailyTrendAggregate.currency,
                DailyTrendAggregate.direct_only,
                DailyTrendAggregate.lowest_price_minor,
                DailyTrendAggregate.highest_price_minor,
                DailyTrendAggregate.price_sum_minor,
                DailyTrendAggregate.sample_count,
                DailyTrendAggregate.first_observed_at,
                DailyTrendAggregate.last_observed_at,
            ],
            daily,
        )
        .returning(
            DailyTrendAggregate.search_query_id,
            DailyTrendAggregate.observation_date,
        )
    )
    inserted_rows = (await session.execute(aggregate_insert)).all()
    aggregate_counts: dict[tuple[UUID, date], int] = {}
    for row in inserted_rows:
        key = (row.search_query_id, row.observation_date)
        aggregate_counts[key] = aggregate_counts.get(key, 0) + 1

    coverage_insert = pg_insert(DailyTrendAggregateCoverage).from_select(
        [
            DailyTrendAggregateCoverage.search_query_id,
            DailyTrendAggregateCoverage.observation_date,
            DailyTrendAggregateCoverage.source_last_observed_at,
            DailyTrendAggregateCoverage.refreshed_at,
        ],
        select(
            source_days.c.search_query_id,
            source_days.c.observation_date,
            source_days.c.source_last_observed_at,
            func.now(),
        ),
    )
    await session.execute(
        coverage_insert.on_conflict_do_update(
            index_elements=[
                DailyTrendAggregateCoverage.search_query_id,
                DailyTrendAggregateCoverage.observation_date,
            ],
            set_={
                "source_last_observed_at": coverage_insert.excluded.source_last_observed_at,
                "refreshed_at": func.now(),
            },
        )
    )
    return tuple(
        DailyTrendRefreshResult(
            search_query_id=query_id,
            observation_date=observation_date,
            aggregate_count=aggregate_counts.get((query_id, observation_date), 0),
            source_last_observed_at=source_last_by_key[(query_id, observation_date)],
        )
        for query_id, observation_date in ordered_keys
    )


async def maintain_daily_trend_aggregates(
    session: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    batch_size: int = 500,
    search_query_id: UUID | None = None,
    after: tuple[date, UUID] | None = None,
) -> DailyTrendMaintenanceResult:
    """Rebuild one keyset-paginated batch of tracked query/days.

    Every requested date for a subscribed canonical query is eligible, including empty dates. The
    requested months must remain attached to the hot observation parent; detached or missing
    source partitions fail before existing aggregate rows can be changed.
    """

    if end_date < start_date:
        raise ValueError("daily trend end date must not precede start date")
    if not 1 <= batch_size <= 10_000:
        raise ValueError("daily trend batch size must be between 1 and 10000")

    await _require_complete_hot_source(
        session,
        start_date=start_date,
        end_date=end_date,
    )
    tracked_queries = (
        select(Subscription.search_query_id.label("search_query_id"))
        .group_by(Subscription.search_query_id)
    )
    if search_query_id is not None:
        tracked_queries = tracked_queries.where(Subscription.search_query_id == search_query_id)
    tracked_query_ids = tracked_queries.cte("daily_trend_tracked_queries")
    calendar_days = (
        func.generate_series(
            literal(start_date, type_=Date),
            literal(end_date, type_=Date),
            timedelta(days=1),
        )
        .table_valued("observation_date")
        .render_derived(name="daily_trend_calendar_days")
    )
    source_days = (
        select(
            tracked_query_ids.c.search_query_id,
            cast(calendar_days.c.observation_date, Date).label("observation_date"),
        )
        .select_from(tracked_query_ids.join(calendar_days, true()))
        .cte("daily_trend_source_days")
    )
    source_day_page = (
        select(source_days.c.search_query_id, source_days.c.observation_date)
        .order_by(source_days.c.observation_date, source_days.c.search_query_id)
        .limit(batch_size)
    )
    if after is not None:
        after_date, after_query_id = after
        source_day_page = source_day_page.where(
            or_(
                source_days.c.observation_date > after_date,
                and_(
                    source_days.c.observation_date == after_date,
                    source_days.c.search_query_id > after_query_id,
                ),
            )
        )
    candidates = (await session.execute(source_day_page)).all()
    refreshed = await _refresh_daily_trend_days(
        session,
        tuple((candidate.search_query_id, candidate.observation_date) for candidate in candidates),
    )
    next_cursor = (
        (candidates[-1].observation_date, candidates[-1].search_query_id) if candidates else after
    )
    return DailyTrendMaintenanceResult(
        refreshed=refreshed,
        next_cursor=next_cursor,
    )
