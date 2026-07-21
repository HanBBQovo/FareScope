from collections.abc import Iterable
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


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
