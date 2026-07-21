import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_OBSERVATION_PARENTS = (
    "calendar_price_observations",
    "price_observations",
)
_PARTITION_PATTERN = re.compile(
    r"^(calendar_price_observations|price_observations)_y(\d{4})m(0[1-9]|1[0-2])$"
)
_ARCHIVE_SCHEMA = "farescope_archive"


@dataclass(frozen=True, slots=True)
class PartitionLifecycleAction:
    action: Literal["archive", "purge"]
    parent_table: str
    partition_name: str
    partition_month: date


def month_start(value: date | datetime) -> date:
    return date(value.year, value.month, 1)


def shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def iter_month_starts(
    reference: date | datetime,
    *,
    months_before: int = 1,
    months_ahead: int = 2,
) -> Iterable[date]:
    if months_before < 0 or months_ahead < 0:
        raise ValueError("Partition windows cannot be negative")

    anchor = month_start(reference)
    for offset in range(-months_before, months_ahead + 1):
        yield shift_month(anchor, offset)


def price_observation_partition_name(value: date | datetime) -> str:
    anchor = month_start(value)
    return f"price_observations_y{anchor.year:04d}m{anchor.month:02d}"


def price_observation_partition_ddl(value: date | datetime) -> str:
    start = month_start(value)
    end = shift_month(start, 1)
    name = price_observation_partition_name(start)
    start_iso = datetime(start.year, start.month, 1, tzinfo=UTC).isoformat()
    end_iso = datetime(end.year, end.month, 1, tzinfo=UTC).isoformat()
    return (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF price_observations "
        f"FOR VALUES FROM (TIMESTAMPTZ '{start_iso}') TO (TIMESTAMPTZ '{end_iso}')"
    )


def calendar_price_observation_partition_name(value: date | datetime) -> str:
    anchor = month_start(value)
    return f"calendar_price_observations_y{anchor.year:04d}m{anchor.month:02d}"


def calendar_price_observation_partition_ddl(value: date | datetime) -> str:
    start = month_start(value)
    end = shift_month(start, 1)
    name = calendar_price_observation_partition_name(start)
    start_iso = datetime(start.year, start.month, 1, tzinfo=UTC).isoformat()
    end_iso = datetime(end.year, end.month, 1, tzinfo=UTC).isoformat()
    return (
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF calendar_price_observations "
        f"FOR VALUES FROM (TIMESTAMPTZ '{start_iso}') TO (TIMESTAMPTZ '{end_iso}')"
    )


async def ensure_price_observation_partitions(
    connection: AsyncConnection,
    *,
    reference: date | datetime | None = None,
    months_before: int = 1,
    months_ahead: int = 2,
) -> tuple[str, ...]:
    reference = reference or datetime.now(UTC)
    names: list[str] = []
    for value in iter_month_starts(
        reference,
        months_before=months_before,
        months_ahead=months_ahead,
    ):
        await connection.execute(text(price_observation_partition_ddl(value)))
        names.append(price_observation_partition_name(value))
    return tuple(names)


async def ensure_calendar_price_observation_partitions(
    connection: AsyncConnection,
    *,
    reference: date | datetime | None = None,
    months_before: int = 1,
    months_ahead: int = 2,
) -> tuple[str, ...]:
    """Create the calendar partitions needed by the current collection horizon."""

    reference = reference or datetime.now(UTC)
    names: list[str] = []
    for value in iter_month_starts(
        reference,
        months_before=months_before,
        months_ahead=months_ahead,
    ):
        await connection.execute(text(calendar_price_observation_partition_ddl(value)))
        names.append(calendar_price_observation_partition_name(value))
    return tuple(names)


async def ensure_all_observation_partitions(
    connection: AsyncConnection,
    *,
    reference: date | datetime | None = None,
    months_before: int = 1,
    months_ahead: int = 2,
) -> dict[str, tuple[str, ...]]:
    """Keep both observation tables writable for the configured rolling horizon."""

    return {
        "price_observations": await ensure_price_observation_partitions(
            connection,
            reference=reference,
            months_before=months_before,
            months_ahead=months_ahead,
        ),
        "calendar_price_observations": await ensure_calendar_price_observation_partitions(
            connection,
            reference=reference,
            months_before=months_before,
            months_ahead=months_ahead,
        ),
    }


async def maintain_observation_partition_lifecycle(
    connection: AsyncConnection,
    *,
    reference: date | datetime | None = None,
    archive_after_months: int | None = 24,
    purge_after_months: int | None = None,
    max_actions: int = 2,
    protected_export_ranges: tuple[tuple[datetime, datetime], ...] = (),
) -> tuple[PartitionLifecycleAction, ...]:
    """Archive old hot partitions and optionally purge older archived partitions.

    Archiving is non-destructive: the partition is detached from the hot parent and
    moved into ``farescope_archive``. Purging is opt-in and only considers tables
    that have already completed that archive stage.
    """

    if archive_after_months is not None and archive_after_months < 3:
        raise ValueError("archive retention must keep at least three months hot")
    if purge_after_months is not None:
        if archive_after_months is None:
            raise ValueError("purging requires partition archiving to be enabled")
        if purge_after_months <= archive_after_months:
            raise ValueError("purge retention must exceed archive retention")
    if max_actions < 1:
        raise ValueError("partition maintenance must allow at least one action")
    if archive_after_months is None:
        return ()

    anchor = month_start(reference or datetime.now(UTC))
    actions: list[PartitionLifecycleAction] = []
    await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_ARCHIVE_SCHEMA}"))

    if purge_after_months is not None:
        purge_cutoff = shift_month(anchor, -purge_after_months)
        archived = await _list_archived_observation_partitions(connection)
        purge_candidates = sorted(
            (
                (partition_month_from_name(name), parent, name)
                for parent, name in archived
                if partition_month_from_name(name) < purge_cutoff
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        for partition_month, parent, name in purge_candidates:
            if len(actions) >= max_actions:
                return tuple(actions)
            if parent == "price_observations" and _partition_month_overlaps_ranges(
                partition_month,
                protected_export_ranges,
            ):
                continue
            await _purge_archived_partition(connection, name)
            actions.append(
                PartitionLifecycleAction(
                    action="purge",
                    parent_table=parent,
                    partition_name=name,
                    partition_month=partition_month,
                )
            )

    archive_cutoff = shift_month(anchor, -archive_after_months)
    attached = await _list_attached_observation_partitions(connection)
    archive_candidates = sorted(
        (
            (partition_month_from_name(name), parent, name)
            for parent, name in attached
            if partition_month_from_name(name) < archive_cutoff
        ),
        key=lambda item: (item[0], item[1], item[2]),
    )
    for partition_month, parent, name in archive_candidates:
        if len(actions) >= max_actions:
            break
        await _archive_attached_partition(connection, parent, name)
        actions.append(
            PartitionLifecycleAction(
                action="archive",
                parent_table=parent,
                partition_name=name,
                partition_month=partition_month,
            )
        )
    return tuple(actions)


def partition_month_from_name(name: str) -> date:
    match = _PARTITION_PATTERN.fullmatch(name)
    if match is None:
        raise ValueError("unrecognized observation partition name")
    return date(int(match.group(2)), int(match.group(3)), 1)


def _partition_month_overlaps_ranges(
    partition_month: date,
    ranges: tuple[tuple[datetime, datetime], ...],
) -> bool:
    partition_end_month = shift_month(partition_month, 1)
    partition_start = datetime(
        partition_month.year,
        partition_month.month,
        1,
        tzinfo=UTC,
    )
    partition_end = datetime(
        partition_end_month.year,
        partition_end_month.month,
        1,
        tzinfo=UTC,
    )
    return any(
        range_start < partition_end and range_end > partition_start
        for range_start, range_end in ranges
    )


async def _list_attached_observation_partitions(
    connection: AsyncConnection,
) -> tuple[tuple[str, str], ...]:
    rows = (
        await connection.execute(
            text(
                """
                SELECT parent.relname AS parent_name, child.relname AS partition_name
                FROM pg_inherits
                JOIN pg_class AS parent ON parent.oid = pg_inherits.inhparent
                JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
                JOIN pg_class AS child ON child.oid = pg_inherits.inhrelid
                JOIN pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
                WHERE parent_ns.nspname = 'public'
                  AND child_ns.nspname = 'public'
                  AND parent.relname IN (
                    'calendar_price_observations',
                    'price_observations'
                  )
                """
            )
        )
    ).mappings()
    return tuple(
        (str(row["parent_name"]), str(row["partition_name"]))
        for row in rows
        if _valid_partition_pair(str(row["parent_name"]), str(row["partition_name"]))
    )


async def _list_archived_observation_partitions(
    connection: AsyncConnection,
) -> tuple[tuple[str, str], ...]:
    names = (
        await connection.execute(
            text(
                """
                SELECT relname
                FROM pg_class
                JOIN pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                WHERE pg_namespace.nspname = 'farescope_archive'
                  AND pg_class.relkind IN ('r', 'p')
                """
            )
        )
    ).scalars()
    result: list[tuple[str, str]] = []
    for value in names:
        name = str(value)
        match = _PARTITION_PATTERN.fullmatch(name)
        if match is not None:
            result.append((match.group(1), name))
    return tuple(result)


async def _archive_attached_partition(
    connection: AsyncConnection,
    parent: str,
    name: str,
) -> None:
    if not _valid_partition_pair(parent, name):
        raise ValueError("invalid observation partition")
    quote = connection.dialect.identifier_preparer.quote
    public = quote("public")
    archive = quote(_ARCHIVE_SCHEMA)
    quoted_parent = quote(parent)
    quoted_name = quote(name)
    await connection.execute(
        text(f"ALTER TABLE {public}.{quoted_parent} DETACH PARTITION {public}.{quoted_name}")
    )
    await connection.execute(text(f"ALTER TABLE {public}.{quoted_name} SET SCHEMA {archive}"))


async def _purge_archived_partition(connection: AsyncConnection, name: str) -> None:
    partition_month_from_name(name)
    quote = connection.dialect.identifier_preparer.quote
    await connection.execute(text(f"DROP TABLE {quote(_ARCHIVE_SCHEMA)}.{quote(name)}"))


def _valid_partition_pair(parent: str, name: str) -> bool:
    match = _PARTITION_PATTERN.fullmatch(name)
    return parent in _OBSERVATION_PARENTS and match is not None and match.group(1) == parent
