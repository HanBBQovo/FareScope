from datetime import UTC, datetime
from types import SimpleNamespace

from app.api.routes.fares import _fare_leg_public
from app.main import create_app


def _segment(
    *,
    position: int,
    flight_number: str,
    origin: str,
    origin_name: str,
    origin_terminal: str,
    destination: str,
    destination_name: str,
    destination_terminal: str,
    departure_utc: datetime,
    arrival_utc: datetime,
    departure_local: datetime,
    arrival_local: datetime,
):
    return SimpleNamespace(
        position=position,
        flight_number=flight_number,
        marketing_airline_code=flight_number[:2],
        origin_airport_code=origin,
        destination_airport_code=destination,
        departure_at_utc=departure_utc,
        arrival_at_utc=arrival_utc,
        departure_local=departure_local,
        arrival_local=arrival_local,
        departure_timezone="Asia/Shanghai" if origin == "PVG" else "Asia/Tokyo",
        arrival_timezone="Asia/Tokyo",
        duration_minutes=int((arrival_utc - departure_utc).total_seconds() // 60),
        aircraft_code=None,
        segment_metadata={
            "departure_airport_name": origin_name,
            "arrival_airport_name": destination_name,
            "departure_terminal": origin_terminal,
            "arrival_terminal": destination_terminal,
            "marketing_airline_name": None,
            "operating_flight_number": None,
            "technical_stop_count": 0,
        },
    )


def test_fare_leg_keeps_every_transfer_segment_and_airport_name() -> None:
    segments = [
        _segment(
            position=0,
            flight_number="MM080",
            origin="PVG",
            origin_name="浦东国际机场",
            origin_terminal="T2",
            destination="KIX",
            destination_name="关西国际机场",
            destination_terminal="T2",
            departure_utc=datetime(2026, 8, 19, 22, 15, tzinfo=UTC),
            arrival_utc=datetime(2026, 8, 20, 0, 35, tzinfo=UTC),
            departure_local=datetime(2026, 8, 20, 6, 15),
            arrival_local=datetime(2026, 8, 20, 9, 35),
        ),
        _segment(
            position=1,
            flight_number="7G026",
            origin="KIX",
            origin_name="关西国际机场",
            origin_terminal="T1",
            destination="HND",
            destination_name="羽田机场",
            destination_terminal="T1",
            departure_utc=datetime(2026, 8, 20, 8, 20, tzinfo=UTC),
            arrival_utc=datetime(2026, 8, 20, 9, 45, tzinfo=UTC),
            departure_local=datetime(2026, 8, 20, 17, 20),
            arrival_local=datetime(2026, 8, 20, 18, 45),
        ),
    ]

    payload = _fare_leg_public(0, segments).model_dump(by_alias=True, mode="json")

    assert payload["direction"] == "outbound"
    assert payload["originName"] == "浦东国际机场"
    assert payload["destinationName"] == "羽田机场"
    assert payload["stops"] == 1
    assert payload["durationMinutes"] == 690
    assert [segment["flightNumber"] for segment in payload["segments"]] == [
        "MM080",
        "7G026",
    ]
    assert payload["segments"][0]["destinationName"] == "关西国际机场"
    assert payload["segments"][0]["destinationTerminal"] == "T2"
    assert payload["segments"][1]["originTerminal"] == "T1"
    assert payload["segments"][0]["departureLocal"] == "2026-08-20T06:15:00"


def test_fare_openapi_exposes_segment_airport_and_local_time_fields() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    leg_fields = schemas["FareLegPublic"]["properties"]
    segment_fields = schemas["FareSegmentPublic"]["properties"]

    assert {"originName", "destinationName", "segments"}.issubset(leg_fields)
    assert {
        "flightNumber",
        "originName",
        "originTerminal",
        "destinationName",
        "destinationTerminal",
        "departureLocal",
        "arrivalLocal",
        "departureTimezone",
        "arrivalTimezone",
        "technicalStopCount",
    }.issubset(segment_fields)
