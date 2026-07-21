from datetime import UTC, date, datetime

import pytest

from app.db.partitions import (
    calendar_price_observation_partition_ddl,
    calendar_price_observation_partition_name,
    iter_month_starts,
    partition_month_from_name,
    price_observation_partition_ddl,
    price_observation_partition_name,
    shift_month,
)
from app.settings import Settings


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


def test_partition_names_are_strictly_parsed() -> None:
    assert partition_month_from_name("price_observations_y2024m12") == date(2024, 12, 1)
    assert partition_month_from_name("calendar_price_observations_y2025m01") == date(2025, 1, 1)
    with pytest.raises(ValueError, match="unrecognized"):
        partition_month_from_name("price_observations_y2024m13")
    with pytest.raises(ValueError, match="unrecognized"):
        partition_month_from_name("untrusted_table_y2024m12")


def test_partition_retention_is_non_destructive_by_default() -> None:
    settings = Settings()

    assert settings.collection_partition_archive_after_months == 24
    assert settings.collection_partition_purge_after_months is None
    assert (
        Settings(collection_partition_purge_after_months="").collection_partition_purge_after_months
        is None
    )
    assert (
        Settings(
            collection_partition_archive_after_months="off"
        ).collection_partition_archive_after_months
        is None
    )

    with pytest.raises(ValueError, match="must exceed"):
        Settings(collection_partition_purge_after_months=12)
    with pytest.raises(ValueError, match="requires archiving"):
        Settings(
            collection_partition_archive_after_months=None,
            collection_partition_purge_after_months=84,
        )


def test_export_retained_file_limit_covers_active_jobs() -> None:
    with pytest.raises(ValueError, match="must cover all active"):
        Settings(
            export_max_active_jobs=5,
            export_user_max_retained_files=4,
        )
