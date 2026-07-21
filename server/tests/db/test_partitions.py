from datetime import UTC, date, datetime

import pytest

from app.db.partitions import (
    calendar_price_observation_partition_ddl,
    calendar_price_observation_partition_name,
    iter_month_starts,
    price_observation_partition_ddl,
    price_observation_partition_name,
    shift_month,
)


def test_partition_window_crosses_year_boundaries() -> None:
    values = tuple(iter_month_starts(date(2026, 1, 20), months_before=1, months_ahead=2))

    assert values == (
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    )
    assert shift_month(date(2026, 1, 1), -13) == date(2024, 12, 1)


def test_partition_ddl_is_deterministic_and_uses_utc_bounds() -> None:
    value = datetime(2026, 7, 20, 15, 30, tzinfo=UTC)

    assert price_observation_partition_name(value) == "price_observations_y2026m07"
    assert price_observation_partition_ddl(value) == (
        "CREATE TABLE IF NOT EXISTS price_observations_y2026m07 "
        "PARTITION OF price_observations FOR VALUES FROM "
        "(TIMESTAMPTZ '2026-07-01T00:00:00+00:00') TO "
        "(TIMESTAMPTZ '2026-08-01T00:00:00+00:00')"
    )


def test_calendar_partition_ddl_is_deterministic() -> None:
    value = datetime(2026, 7, 20, 15, 30, tzinfo=UTC)

    assert calendar_price_observation_partition_name(value) == (
        "calendar_price_observations_y2026m07"
    )
    assert calendar_price_observation_partition_ddl(value) == (
        "CREATE TABLE IF NOT EXISTS calendar_price_observations_y2026m07 "
        "PARTITION OF calendar_price_observations FOR VALUES FROM "
        "(TIMESTAMPTZ '2026-07-01T00:00:00+00:00') TO "
        "(TIMESTAMPTZ '2026-08-01T00:00:00+00:00')"
    )


def test_negative_partition_windows_are_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        tuple(iter_month_starts(date(2026, 7, 1), months_before=-1))
