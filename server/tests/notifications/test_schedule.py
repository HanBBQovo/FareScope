from datetime import UTC, datetime, time

import pytest

from app.domain.notification_schedule import (
    NotificationScheduleError,
    is_delivery_allowed,
    next_delivery_at,
    validate_notification_schedule,
)


def test_unconstrained_schedule_preserves_immediate_delivery() -> None:
    now = datetime(2026, 7, 20, 15, 12, 34, tzinfo=UTC)

    assert next_delivery_at(
        now,
        timezone_name=None,
        quiet_hours_start=None,
        quiet_hours_end=None,
        allowed_weekdays=None,
    ) == now


def test_cross_midnight_quiet_hours_delay_until_local_end() -> None:
    quiet_start = time(22)
    quiet_end = time(8)
    during_quiet = datetime(2026, 7, 20, 15, tzinfo=UTC)  # 23:00 in Shanghai
    expected = datetime(2026, 7, 21, 0, tzinfo=UTC)  # 08:00 in Shanghai

    assert not is_delivery_allowed(
        during_quiet,
        timezone_name="Asia/Shanghai",
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        allowed_weekdays=None,
    )
    assert next_delivery_at(
        during_quiet,
        timezone_name="Asia/Shanghai",
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        allowed_weekdays=None,
    ) == expected
    assert is_delivery_allowed(
        expected,
        timezone_name="Asia/Shanghai",
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        allowed_weekdays=None,
    )


def test_weekday_constraint_skips_disallowed_local_days() -> None:
    saturday = datetime(2026, 7, 25, 2, tzinfo=UTC)  # Saturday 10:00 in Shanghai

    assert next_delivery_at(
        saturday,
        timezone_name="Asia/Shanghai",
        quiet_hours_start=time(22),
        quiet_hours_end=time(8),
        allowed_weekdays=(0, 1, 2, 3, 4),
    ) == datetime(2026, 7, 27, 0, tzinfo=UTC)


def test_spring_forward_resolves_nonexistent_quiet_end_with_zoneinfo() -> None:
    during_quiet = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)  # 01:30 EST

    assert next_delivery_at(
        during_quiet,
        timezone_name="America/New_York",
        quiet_hours_start=time(1),
        quiet_hours_end=time(2, 30),
        allowed_weekdays=None,
    ) == datetime(2026, 3, 8, 7, tzinfo=UTC)  # 03:00 EDT


def test_fall_back_selects_the_next_matching_fold() -> None:
    first_fold = datetime(2026, 11, 1, 5, tzinfo=UTC)  # 01:00 EDT
    second_fold = datetime(2026, 11, 1, 6, 15, tzinfo=UTC)  # 01:15 EST
    schedule = {
        "timezone_name": "America/New_York",
        "quiet_hours_start": time(0, 30),
        "quiet_hours_end": time(1, 30),
        "allowed_weekdays": None,
    }

    assert next_delivery_at(first_fold, **schedule) == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )
    assert next_delivery_at(second_fold, **schedule) == datetime(
        2026, 11, 1, 6, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("timezone_name", "quiet_start", "quiet_end", "weekdays", "message"),
    [
        ("Mars/Olympus", time(22), time(8), None, "valid IANA"),
        ("Asia/Shanghai", time(22), None, None, "configured together"),
        ("Asia/Shanghai", time(8), time(8), None, "must be different"),
        ("Asia/Shanghai", None, None, [], "cannot be empty"),
        (None, None, None, [0, 1, 2], "timezone is required"),
    ],
)
def test_invalid_schedules_are_rejected(
    timezone_name: str | None,
    quiet_start: time | None,
    quiet_end: time | None,
    weekdays: list[int] | None,
    message: str,
) -> None:
    with pytest.raises(NotificationScheduleError, match=message):
        validate_notification_schedule(
            timezone_name=timezone_name,
            quiet_hours_start=quiet_start,
            quiet_hours_end=quiet_end,
            allowed_weekdays=weekdays,
        )
