from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class NotificationScheduleError(ValueError):
    pass


def validate_timezone_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise NotificationScheduleError("timezone must be a valid IANA timezone")
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise NotificationScheduleError("timezone must be a valid IANA timezone") from error
    return normalized


def normalize_allowed_weekdays(value: Iterable[int] | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    weekdays = tuple(value)
    if not weekdays:
        raise NotificationScheduleError("allowed weekdays cannot be empty")
    if any(
        isinstance(day, bool) or not isinstance(day, int) or day < 0 or day > 6
        for day in weekdays
    ):
        raise NotificationScheduleError("allowed weekdays must use integers from 0 to 6")
    if len(set(weekdays)) != len(weekdays):
        raise NotificationScheduleError("allowed weekdays cannot contain duplicates")
    return tuple(sorted(weekdays))


def validate_notification_schedule(
    *,
    timezone_name: str | None,
    quiet_hours_start: time | None,
    quiet_hours_end: time | None,
    allowed_weekdays: Iterable[int] | None,
) -> tuple[str | None, tuple[int, ...] | None]:
    if (quiet_hours_start is None) != (quiet_hours_end is None):
        raise NotificationScheduleError("quiet hours start and end must be configured together")
    if quiet_hours_start is not None and quiet_hours_end is not None:
        _validate_minute_precision(quiet_hours_start)
        _validate_minute_precision(quiet_hours_end)
        if quiet_hours_start == quiet_hours_end:
            raise NotificationScheduleError("quiet hours start and end must be different")

    weekdays = normalize_allowed_weekdays(allowed_weekdays)
    has_constraints = quiet_hours_start is not None or weekdays is not None
    if has_constraints and timezone_name is None:
        raise NotificationScheduleError("timezone is required when delivery constraints are set")
    timezone = validate_timezone_name(timezone_name) if timezone_name is not None else None
    return timezone, weekdays


def is_delivery_allowed(
    moment: datetime,
    *,
    timezone_name: str | None,
    quiet_hours_start: time | None,
    quiet_hours_end: time | None,
    allowed_weekdays: Sequence[int] | None,
) -> bool:
    normalized_moment = _require_aware(moment)
    if quiet_hours_start is None and allowed_weekdays is None:
        return True
    timezone, weekdays = validate_notification_schedule(
        timezone_name=timezone_name,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        allowed_weekdays=allowed_weekdays,
    )
    assert timezone is not None
    return _is_allowed_local(
        normalized_moment.astimezone(ZoneInfo(timezone)),
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        allowed_weekdays=weekdays,
    )


def next_delivery_at(
    moment: datetime,
    *,
    timezone_name: str | None,
    quiet_hours_start: time | None,
    quiet_hours_end: time | None,
    allowed_weekdays: Sequence[int] | None,
) -> datetime:
    normalized_moment = _require_aware(moment).astimezone(UTC)
    if quiet_hours_start is None and allowed_weekdays is None:
        return normalized_moment

    timezone, weekdays = validate_notification_schedule(
        timezone_name=timezone_name,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        allowed_weekdays=allowed_weekdays,
    )
    assert timezone is not None
    zone = ZoneInfo(timezone)
    if _is_allowed_local(
        normalized_moment.astimezone(zone),
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
        allowed_weekdays=weekdays,
    ):
        return normalized_moment

    local_date = normalized_moment.astimezone(zone).date()
    boundary_times = {time.min}
    if quiet_hours_end is not None:
        boundary_times.add(quiet_hours_end)

    candidates: list[datetime] = []
    for day_offset in range(15):
        candidate_date = local_date + timedelta(days=day_offset)
        for boundary in boundary_times:
            local_boundary = datetime.combine(candidate_date, boundary)
            for candidate in _resolve_local_at_or_after(local_boundary, zone):
                if candidate < normalized_moment:
                    continue
                if _is_allowed_local(
                    candidate.astimezone(zone),
                    quiet_hours_start=quiet_hours_start,
                    quiet_hours_end=quiet_hours_end,
                    allowed_weekdays=weekdays,
                ):
                    candidates.append(candidate)
    if not candidates:
        raise NotificationScheduleError("delivery schedule has no reachable delivery time")
    return min(candidates)


def _is_allowed_local(
    local_moment: datetime,
    *,
    quiet_hours_start: time | None,
    quiet_hours_end: time | None,
    allowed_weekdays: Sequence[int] | None,
) -> bool:
    if allowed_weekdays is not None and local_moment.weekday() not in allowed_weekdays:
        return False
    if quiet_hours_start is None or quiet_hours_end is None:
        return True
    local_time = local_moment.timetz().replace(tzinfo=None)
    if quiet_hours_start < quiet_hours_end:
        in_quiet_hours = quiet_hours_start <= local_time < quiet_hours_end
    else:
        in_quiet_hours = local_time >= quiet_hours_start or local_time < quiet_hours_end
    return not in_quiet_hours


def _resolve_local_at_or_after(local_moment: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    # IANA transitions can remove a wall-clock interval. Advance through the gap
    # and return both folds when the first valid time is ambiguous.
    for minute_offset in range(3 * 24 * 60 + 1):
        candidate = local_moment + timedelta(minutes=minute_offset)
        instants = _valid_utc_instants(candidate, zone)
        if instants:
            return instants
    raise NotificationScheduleError("timezone transition could not be resolved")


def _valid_utc_instants(local_moment: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    instants: set[datetime] = set()
    for fold in (0, 1):
        candidate = local_moment.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        round_trip = candidate.astimezone(zone).replace(tzinfo=None)
        if round_trip == local_moment:
            instants.add(candidate)
    return tuple(sorted(instants))


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NotificationScheduleError("delivery schedule moments must be timezone-aware")
    return value


def _validate_minute_precision(value: time) -> None:
    if value.tzinfo is not None:
        raise NotificationScheduleError("quiet hours must be local wall-clock times")
    if value.second or value.microsecond:
        raise NotificationScheduleError("quiet hours must use minute precision")
