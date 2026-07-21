from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID


class InvalidCursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TimestampCursor:
    as_of: datetime
    timestamp: datetime
    row_id: UUID


@dataclass(frozen=True, slots=True)
class DatePairCursor:
    """Keyset cursor for a latest-per-date calendar matrix page."""

    departure_date: date
    return_date: date | None


@dataclass(frozen=True, slots=True)
class BucketCursor:
    as_of: datetime
    bucket: datetime
    resolution: Literal["hour", "day"]


@dataclass(frozen=True, slots=True)
class OfferCursor:
    run_id: UUID
    price_minor: int
    row_id: UUID
    filter_key: str


@dataclass(frozen=True, slots=True)
class RunCursor:
    scheduled_at: datetime
    row_id: UUID


def encode_timestamp_cursor(cursor: TimestampCursor) -> str:
    payload = json.dumps(
        {
            "a": cursor.as_of.isoformat(),
            "t": cursor.timestamp.isoformat(),
            "i": str(cursor.row_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_timestamp_cursor(value: str) -> TimestampCursor:
    try:
        payload = _decode_payload(value)
        as_of = datetime.fromisoformat(_required_string(payload, "a"))
        timestamp = datetime.fromisoformat(_required_string(payload, "t"))
        if as_of.tzinfo is None or timestamp.tzinfo is None:
            raise ValueError("cursor timestamps must include an offset")
        return TimestampCursor(
            as_of=as_of.astimezone(UTC),
            timestamp=timestamp.astimezone(UTC),
            row_id=UUID(_required_string(payload, "i")),
        )
    except _CURSOR_ERRORS as error:
        raise InvalidCursorError("invalid pagination cursor") from error


def encode_date_pair_cursor(cursor: DatePairCursor) -> str:
    payload = json.dumps(
        {
            "d": cursor.departure_date.isoformat(),
            "r": cursor.return_date.isoformat() if cursor.return_date else None,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_date_pair_cursor(value: str) -> DatePairCursor:
    try:
        payload = _decode_payload(value)
        return_value = payload.get("r")
        if return_value is not None and not isinstance(return_value, str):
            raise TypeError("return date must be a string or null")
        return DatePairCursor(
            departure_date=date.fromisoformat(_required_string(payload, "d")),
            return_date=date.fromisoformat(return_value) if return_value is not None else None,
        )
    except _CURSOR_ERRORS as error:
        raise InvalidCursorError("invalid pagination cursor") from error


def encode_bucket_cursor(cursor: BucketCursor) -> str:
    return _encode_payload(
        {
            "a": cursor.as_of.isoformat(),
            "b": cursor.bucket.isoformat(),
            "r": cursor.resolution,
        }
    )


def decode_bucket_cursor(value: str) -> BucketCursor:
    try:
        payload = _decode_payload(value)
        as_of = datetime.fromisoformat(_required_string(payload, "a"))
        bucket = datetime.fromisoformat(_required_string(payload, "b"))
        resolution = _required_string(payload, "r")
        if as_of.tzinfo is None or bucket.tzinfo is None:
            raise ValueError("cursor timestamps must include an offset")
        if resolution not in ("hour", "day"):
            raise ValueError("invalid bucket resolution")
        return BucketCursor(
            as_of=as_of.astimezone(UTC),
            bucket=bucket.astimezone(UTC),
            resolution=resolution,
        )
    except _CURSOR_ERRORS as error:
        raise InvalidCursorError("invalid pagination cursor") from error


def encode_offer_cursor(cursor: OfferCursor) -> str:
    return _encode_payload(
        {
            "f": cursor.filter_key,
            "i": str(cursor.row_id),
            "p": cursor.price_minor,
            "r": str(cursor.run_id),
        }
    )


def decode_offer_cursor(value: str) -> OfferCursor:
    try:
        payload = _decode_payload(value)
        price_minor = payload["p"]
        if not isinstance(price_minor, int) or isinstance(price_minor, bool) or price_minor < 0:
            raise TypeError("price must be a non-negative integer")
        return OfferCursor(
            run_id=UUID(_required_string(payload, "r")),
            price_minor=price_minor,
            row_id=UUID(_required_string(payload, "i")),
            filter_key=_required_string(payload, "f"),
        )
    except _CURSOR_ERRORS as error:
        raise InvalidCursorError("invalid pagination cursor") from error


def encode_run_cursor(cursor: RunCursor) -> str:
    return _encode_payload(
        {
            "i": str(cursor.row_id),
            "s": cursor.scheduled_at.isoformat(),
        }
    )


def decode_run_cursor(value: str) -> RunCursor:
    try:
        payload = _decode_payload(value)
        scheduled_at = datetime.fromisoformat(_required_string(payload, "s"))
        if scheduled_at.tzinfo is None:
            raise ValueError("cursor timestamp must include an offset")
        return RunCursor(
            scheduled_at=scheduled_at.astimezone(UTC),
            row_id=UUID(_required_string(payload, "i")),
        )
    except _CURSOR_ERRORS as error:
        raise InvalidCursorError("invalid pagination cursor") from error


_CURSOR_ERRORS = (
    AttributeError,
    binascii.Error,
    json.JSONDecodeError,
    KeyError,
    TypeError,
    UnicodeDecodeError,
    ValueError,
)


def _encode_payload(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _decode_payload(value: str) -> dict[str, object]:
    if not isinstance(value, str) or not value:
        raise TypeError("cursor must be a non-empty string")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise TypeError("cursor payload must be an object")
    return payload


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"cursor field {key!r} must be a non-empty string")
    return value
