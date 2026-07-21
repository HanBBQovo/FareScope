import base64
import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.api.pagination import (
    BucketCursor,
    DatePairCursor,
    InvalidCursorError,
    TimestampCursor,
    decode_bucket_cursor,
    decode_date_pair_cursor,
    decode_timestamp_cursor,
    encode_bucket_cursor,
    encode_date_pair_cursor,
    encode_timestamp_cursor,
)
from app.domain.search import SearchFilters
from app.main import create_app
from app.models import Itinerary
from app.services.fare_data import (
    FareFilterSpec,
    itinerary_filter_conditions,
    resolve_history_resolution,
    validate_calendar_cursor_mode,
    validate_calendar_window,
)


def _non_null_parameter_schema(parameter: dict[str, object]) -> dict[str, object]:
    schema = parameter["schema"]
    assert isinstance(schema, dict)
    variants = schema.get("anyOf")
    if isinstance(variants, list):
        return next(item for item in variants if item.get("type") != "null")
    return schema


def test_price_endpoints_expose_bounded_owner_route_contracts() -> None:
    schema = create_app().openapi()
    history = schema["paths"]["/api/prices/history"]["get"]
    calendar = schema["paths"]["/api/prices/calendar"]["get"]

    history_params = {item["name"]: item for item in history["parameters"]}
    calendar_params = {item["name"]: item for item in calendar["parameters"]}
    assert history_params["routeId"]["required"] is True
    assert history_params["days"]["schema"]["maximum"] == 365
    assert history_params["limit"]["schema"]["maximum"] == 500
    assert calendar_params["routeId"]["required"] is True
    assert calendar_params["limit"]["schema"]["maximum"] == 500
    assert "cursor" in history_params and "cursor" in calendar_params

    fare_params = {
        item["name"]: item for item in schema["paths"]["/api/fares/search"]["get"]["parameters"]
    }
    runs_params = {
        item["name"]: item for item in schema["paths"]["/api/collection/runs"]["get"]["parameters"]
    }
    assert fare_params["limit"]["schema"]["maximum"] == 100
    assert "cursor" in fare_params and "cursor" in runs_params

    run_fields = schema["components"]["schemas"]["CollectionRunPublic"]["properties"]
    assert {
        "calendarObservations",
        "diagnostics",
        "itineraries",
        "offers",
        "attempt",
        "maxAttempts",
        "schemaFingerprint",
        "upstreamStatus",
        "warningCode",
    }.issubset(run_fields)

    operations = schema["paths"]["/api/collection/operations"]["get"]
    assert operations["responses"]["200"]["content"]["application/json"]["schema"]


def test_fare_search_exposes_verified_local_filter_contract() -> None:
    operation = create_app().openapi()["paths"]["/api/fares/search"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}

    assert {
        "airlineCodes",
        "departureAirports",
        "arrivalAirports",
        "maxPriceMinor",
        "maxStops",
        "maxDurationMinutes",
        "departureMinuteStart",
        "departureMinuteEnd",
    }.issubset(parameters)
    assert _non_null_parameter_schema(parameters["maxStops"])["maximum"] == 3
    assert _non_null_parameter_schema(parameters["maxDurationMinutes"])["maximum"] == 2880


def test_history_and_calendar_cursors_round_trip_and_reject_naive_timestamps() -> None:
    timestamp_cursor = TimestampCursor(
        as_of=datetime(2026, 7, 20, 8, tzinfo=UTC),
        timestamp=datetime(2026, 7, 19, 8, tzinfo=UTC),
        row_id=uuid4(),
    )
    assert decode_timestamp_cursor(encode_timestamp_cursor(timestamp_cursor)) == timestamp_cursor

    date_cursor = DatePairCursor(
        departure_date=date(2026, 8, 15),
        return_date=date(2026, 8, 22),
    )
    assert decode_date_pair_cursor(encode_date_pair_cursor(date_cursor)) == date_cursor

    bucket_cursor = BucketCursor(
        as_of=datetime(2026, 7, 20, 8, tzinfo=UTC),
        bucket=datetime(2026, 7, 19, 8, tzinfo=UTC),
        resolution="hour",
    )
    assert decode_bucket_cursor(encode_bucket_cursor(bucket_cursor)) == bucket_cursor

    naive = TimestampCursor(
        as_of=datetime(2026, 7, 20, 8),
        timestamp=datetime(2026, 7, 19, 8),
        row_id=uuid4(),
    )
    with pytest.raises(InvalidCursorError):
        decode_timestamp_cursor(encode_timestamp_cursor(naive))

    malformed_payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "a": "2026-07-20T08:00:00+00:00",
                "t": "2026-07-19T08:00:00+00:00",
                "i": 1,
            }
        ).encode()
    ).decode()
    with pytest.raises(InvalidCursorError):
        decode_timestamp_cursor(malformed_payload)


def test_calendar_cursor_mode_rejects_null_mismatches() -> None:
    with pytest.raises(ValueError, match="requires a return date"):
        validate_calendar_cursor_mode(
            DatePairCursor(departure_date=date(2026, 8, 15), return_date=None),
            round_trip=True,
        )
    with pytest.raises(ValueError, match="cannot contain"):
        validate_calendar_cursor_mode(
            DatePairCursor(
                departure_date=date(2026, 8, 15),
                return_date=date(2026, 8, 22),
            ),
            round_trip=False,
        )


def test_filter_sql_is_applied_before_the_bounded_offer_limit() -> None:
    filters = FareFilterSpec.from_search_filters(
        SearchFilters(
            direct_only=True,
            airline_codes=("MU",),
            departure_airports=("PVG",),
            arrival_airports=("NRT",),
            max_stops=0,
            max_duration_minutes=360,
            departure_minute_start=360,
            departure_minute_end=720,
        )
    )
    statement = select(Itinerary.id).where(*itinerary_filter_conditions(filters)).limit(100)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "itineraries.is_direct IS true" in sql
    assert sql.count("EXISTS") >= 3
    assert "segments" in sql
    assert "LIMIT 100" in sql


def test_history_resolution_and_calendar_windows_are_explicitly_bounded() -> None:
    assert resolve_history_resolution("auto", days=14) == "hour"
    assert resolve_history_resolution("auto", days=15) == "day"
    validate_calendar_window(date(2026, 1, 1), date(2027, 1, 2))

    with pytest.raises(ValueError, match="cannot exceed"):
        validate_calendar_window(date(2026, 1, 1), date(2027, 1, 3))
    with pytest.raises(ValueError, match="cannot precede"):
        validate_calendar_window(date(2026, 2, 1), date(2026, 1, 31))
