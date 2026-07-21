from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.advisory_locks import acquire_observation_partition_shared_lock

_ARCHIVED_PRICE_PARTITION = re.compile(
    r"price_observations_y(?P<year>[0-9]{4})m(?P<month>0[1-9]|1[0-2])"
)


@dataclass(frozen=True, slots=True)
class ExportObservation:
    id: UUID
    observed_at: datetime
    collection_run_id: UUID
    itinerary_id: UUID
    fare_offer_id: UUID
    offer_fingerprint: str
    currency: str
    total_price_minor: int
    is_lowest: bool
    is_direct: bool


def archived_partition_overlaps(
    name: str,
    *,
    range_start: datetime,
    range_end: datetime,
) -> bool:
    match = _ARCHIVED_PRICE_PARTITION.fullmatch(name)
    if match is None:
        return False
    year = int(match.group("year"))
    month = int(match.group("month"))
    partition_start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        partition_end = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        partition_end = datetime(year, month + 1, 1, tzinfo=UTC)
    return partition_start < range_end and partition_end > range_start


async def list_archived_price_partitions(
    session: AsyncSession,
    *,
    range_start: datetime,
    range_end: datetime,
) -> tuple[str, ...]:
    rows = await session.scalars(
        text(
            """
            SELECT child.relname
            FROM pg_class AS child
            JOIN pg_namespace AS namespace ON namespace.oid = child.relnamespace
            WHERE namespace.nspname = 'farescope_archive'
              AND child.relkind IN ('r', 'p')
              AND child.relname ~ '^price_observations_y[0-9]{4}m(0[1-9]|1[0-2])$'
            ORDER BY child.relname
            """
        )
    )
    return tuple(
        name
        for value in rows
        if (name := str(value))
        and archived_partition_overlaps(
            name,
            range_start=range_start,
            range_end=range_end,
        )
    )


async def list_missing_price_observation_source_months(
    session: AsyncSession,
    *,
    export_job_id: UUID,
    as_of: datetime | None = None,
) -> tuple[date, ...]:
    """Return source months missing for the export's frozen collection-run manifest."""

    await acquire_observation_partition_shared_lock(session)
    required_months = tuple(
        await session.scalars(
            text(
                """
                SELECT DISTINCT
                       date_trunc('month', run.finished_at AT TIME ZONE 'UTC')::date
                FROM export_job_collection_runs AS manifest
                JOIN collection_runs AS run ON run.id = manifest.collection_run_id
                WHERE manifest.export_job_id = :export_job_id
                ORDER BY 1
                """
            ),
            {"export_job_id": export_job_id},
        )
    )
    if not required_months:
        return ()

    attached_rows = (
        await session.execute(
            text(
                """
                SELECT child.relname AS partition_name,
                       pg_get_expr(child.relpartbound, child.oid, true) = 'DEFAULT'
                           AS is_default
                FROM pg_inherits
                JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                JOIN pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
                WHERE parent_ns.nspname = 'public'
                  AND parent.relname = 'price_observations'
                  AND child_ns.nspname = 'public'
                  AND child.relispartition
                """
            )
        )
    ).mappings()
    attached_names: set[str] = set()
    has_default_partition = False
    for row in attached_rows:
        name = str(row["partition_name"])
        if bool(row["is_default"]):
            has_default_partition = True
        elif _ARCHIVED_PRICE_PARTITION.fullmatch(name) is not None:
            attached_names.add(name)

    archived_names = set(
        await list_archived_price_partitions(
            session,
            range_start=datetime(
                required_months[0].year,
                required_months[0].month,
                1,
                tzinfo=UTC,
            ),
            range_end=datetime(
                _next_month(required_months[-1]).year,
                _next_month(required_months[-1]).month,
                1,
                tzinfo=UTC,
            ),
        )
    )
    current_month = _utc_month_start(as_of or datetime.now(UTC))
    return tuple(
        month
        for month in required_months
        if _price_partition_name(month) not in attached_names
        and _price_partition_name(month) not in archived_names
        and not (has_default_partition and month >= current_month)
    )


async def load_export_observation_page(
    session: AsyncSession,
    *,
    job_id: UUID,
    search_query_id: UUID,
    range_start: datetime,
    range_end: datetime,
    after_observed_at: datetime | None,
    after_id: UUID | None,
    limit: int,
) -> tuple[ExportObservation, ...]:
    await acquire_observation_partition_shared_lock(session)
    archived_names = await list_archived_price_partitions(
        session,
        range_start=range_start,
        range_end=range_end,
    )
    quote = session.get_bind().dialect.identifier_preparer.quote
    sources = ["public.price_observations"]
    for name in archived_names:
        if _ARCHIVED_PRICE_PARTITION.fullmatch(name) is None:
            raise ValueError("unvalidated archive partition name")
        sources.append(f"farescope_archive.{quote(name)}")

    cursor_clause = ""
    parameters: dict[str, object] = {
        "job_id": job_id,
        "search_query_id": search_query_id,
        "range_start": range_start,
        "range_end": range_end,
        "limit": limit,
    }
    if after_observed_at is not None and after_id is not None:
        cursor_clause = (
            "AND (observation.observed_at, observation.id) > (:after_observed_at, :after_id)"
        )
        parameters["after_observed_at"] = after_observed_at
        parameters["after_id"] = after_id

    source_queries = [
        f"""
        SELECT observation.id, observation.observed_at,
               observation.collection_run_id, observation.itinerary_id,
               observation.fare_offer_id, observation.offer_fingerprint,
               observation.currency, observation.total_price_minor,
               observation.is_lowest, observation.is_direct,
               {priority} AS source_priority
        FROM {source} AS observation
        JOIN public.export_job_collection_runs AS manifest
          ON manifest.collection_run_id = observation.collection_run_id
         AND manifest.export_job_id = :job_id
        WHERE observation.search_query_id = :search_query_id
          AND observation.observed_at >= :range_start
          AND observation.observed_at < :range_end
          {cursor_clause}
        """
        for priority, source in enumerate(sources)
    ]
    statement = text(
        f"""
        SELECT DISTINCT ON (observed_at, id)
               id, observed_at, collection_run_id, itinerary_id, fare_offer_id,
               offer_fingerprint, currency, total_price_minor, is_lowest, is_direct
        FROM ({" UNION ALL ".join(source_queries)}) AS observations
        ORDER BY observed_at, id, source_priority
        LIMIT :limit
        """
    )
    rows = (await session.execute(statement, parameters)).mappings().all()
    return tuple(
        ExportObservation(
            id=row["id"],
            observed_at=row["observed_at"].astimezone(UTC),
            collection_run_id=row["collection_run_id"],
            itinerary_id=row["itinerary_id"],
            fare_offer_id=row["fare_offer_id"],
            offer_fingerprint=str(row["offer_fingerprint"]),
            currency=str(row["currency"]),
            total_price_minor=int(row["total_price_minor"]),
            is_lowest=bool(row["is_lowest"]),
            is_direct=bool(row["is_direct"]),
        )
        for row in rows
    )


def _utc_month_start(value: datetime) -> date:
    normalized = value.astimezone(UTC)
    return date(normalized.year, normalized.month, 1)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _price_partition_name(value: date) -> str:
    return f"price_observations_y{value.year:04d}m{value.month:02d}"


async def iter_export_observation_pages(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
    search_query_id: UUID,
    range_start: datetime,
    range_end: datetime,
    page_size: int,
):
    after_observed_at: datetime | None = None
    after_id: UUID | None = None
    while True:
        async with session_factory() as session, session.begin():
            page = await load_export_observation_page(
                session,
                job_id=job_id,
                search_query_id=search_query_id,
                range_start=range_start,
                range_end=range_end,
                after_observed_at=after_observed_at,
                after_id=after_id,
                limit=page_size,
            )
        if not page:
            return
        yield page
        if len(page) < page_size:
            return
        after_observed_at = page[-1].observed_at
        after_id = page[-1].id
