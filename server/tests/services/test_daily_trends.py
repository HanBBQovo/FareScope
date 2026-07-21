from datetime import UTC, datetime, timedelta, timezone

from app.services.daily_trends import _requested_partition_names
from app.services.fare_data import (
    FareFilterSpec,
    _full_utc_day_range,
)


def test_daily_trend_compatibility_excludes_every_detail_filter_and_max_price() -> None:
    assert FareFilterSpec().supports_daily_trend_aggregate is True
    assert FareFilterSpec(direct_only=True).supports_daily_trend_aggregate is True

    incompatible = (
        FareFilterSpec(airline_codes=("MU",)),
        FareFilterSpec(departure_airports=("PVG",)),
        FareFilterSpec(arrival_airports=("NRT",)),
        FareFilterSpec(max_price_minor=180_000),
        FareFilterSpec(max_stops=0),
        FareFilterSpec(max_duration_minutes=240),
        FareFilterSpec(departure_minute_start=360, departure_minute_end=720),
    )
    assert all(not filters.supports_daily_trend_aggregate for filters in incompatible)


def test_full_day_range_keeps_only_complete_utc_days() -> None:
    since = datetime(2026, 7, 19, 10, 30, tzinfo=UTC)
    as_of = datetime(2026, 7, 21, 10, 30, tzinfo=UTC)
    assert _full_utc_day_range(since, as_of) == (
        datetime(2026, 7, 20, tzinfo=UTC),
        datetime(2026, 7, 21, tzinfo=UTC),
    )


def test_full_day_range_normalizes_non_utc_inputs() -> None:
    china_standard_time = timezone(timedelta(hours=8))
    since = datetime(2026, 7, 19, 7, tzinfo=china_standard_time)
    as_of = datetime(2026, 7, 21, 7, tzinfo=china_standard_time)

    assert _full_utc_day_range(since, as_of) == (
        datetime(2026, 7, 19, tzinfo=UTC),
        datetime(2026, 7, 20, tzinfo=UTC),
    )


def test_default_thirty_days_fit_the_bootstrap_hot_partition_window() -> None:
    end_date = datetime(2026, 7, 21, tzinfo=UTC).date()

    assert _requested_partition_names(end_date - timedelta(days=29), end_date) == (
        "price_observations_y2026m06",
        "price_observations_y2026m07",
    )
    assert _requested_partition_names(end_date - timedelta(days=89), end_date) == (
        "price_observations_y2026m04",
        "price_observations_y2026m05",
        "price_observations_y2026m06",
        "price_observations_y2026m07",
    )
