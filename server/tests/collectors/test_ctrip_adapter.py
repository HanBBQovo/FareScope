from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.collectors.ctrip import CtripAdapter

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "ctrip"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def issue_codes(result: Any) -> set[str]:
    return {issue.code for issue in result.issues}


def test_parses_one_way_calendar_prices_in_minor_units() -> None:
    result = CtripAdapter().parse_calendar(load_fixture("calendar_one_way.json"))

    assert result.has_errors is False
    assert len(result.records) == 3
    assert result.records[0].departure_date == date(2026, 8, 14)
    assert result.records[0].return_date is None
    assert result.records[1].lowest.amount_minor == 218_000
    assert result.records[1].lowest.currency == "CNY"
    assert result.records[2].total is not None
    assert result.records[2].total.amount_minor == 226_550


def test_parses_dotnet_date_envelope_used_by_live_ctrip_response() -> None:
    result = CtripAdapter().parse_calendar(
        {
            "priceList": [
                {
                    "departDate": "/Date(1786723200000+0800)/",
                    "price": "2180",
                }
            ]
        }
    )

    assert len(result.records) == 1
    assert result.records[0].departure_date == date(2026, 8, 15)


def test_parses_round_trip_date_pairs_without_inventing_directness() -> None:
    result = CtripAdapter().parse_calendar(load_fixture("calendar_round_trip.json"))

    assert result.has_errors is False
    assert len(result.records) == 3
    assert result.records[0].departure_date == date(2026, 9, 3)
    assert result.records[0].return_date == date(2026, 9, 8)
    assert result.records[0].lowest.amount_minor == 197_500
    assert result.records[0].total is not None
    assert result.records[0].total.amount_minor == 395_000
    assert not hasattr(result.records[0], "direct_only")


def test_calendar_parser_keeps_valid_records_and_reports_bad_siblings() -> None:
    payload = load_fixture("calendar_one_way.json")
    payload["data"]["priceList"].extend(
        [
            {"departDate": "not-a-date", "price": 1000},
            {"departDate": "2026-08-17", "price": "unknown"},
        ]
    )

    result = CtripAdapter().parse_calendar(payload)

    assert len(result.records) == 3
    assert issue_codes(result) == {"invalid_departure_date", "invalid_price"}


def test_calendar_parser_deduplicates_a_date_pair_at_the_lower_price() -> None:
    payload = load_fixture("calendar_round_trip.json")
    duplicate = deepcopy(payload["data"]["lowestPriceList"][0])
    duplicate["price"] = 1800
    duplicate["totalPrice"] = 3600
    payload["data"]["lowestPriceList"].append(duplicate)

    result = CtripAdapter().parse_calendar(payload)

    assert len(result.records) == 3
    assert result.records[0].lowest.amount_minor == 180_000
    assert "duplicate_calendar_date" in issue_codes(result)


def test_calendar_parser_inherits_envelope_currency_minor_unit_rules() -> None:
    payload = load_fixture("calendar_one_way.json")
    payload["data"]["currency"] = "JPY"

    result = CtripAdapter().parse_calendar(payload)

    assert result.records[1].lowest.currency == "JPY"
    assert result.records[1].lowest.amount_minor == 2180


def test_calendar_schema_drift_is_observable_without_an_exception() -> None:
    result = CtripAdapter().parse_calendar({"status": 0, "data": {"priceList": None}})

    assert result.records == ()
    assert result.has_errors is True
    assert issue_codes(result) == {"calendar_records_missing"}
    assert len(result.schema_fingerprint) == 64


def test_parses_detailed_itineraries_segments_transfers_and_offers() -> None:
    result = CtripAdapter().parse_itineraries(load_fixture("batch_search.json"))

    assert result.has_errors is False
    assert len(result.records) == 2

    direct = result.records[0]
    assert direct.provider_itinerary_id == "redacted-itinerary-direct"
    assert direct.is_direct is True
    assert direct.duration_minutes == 185
    assert len(direct.legs) == 1
    assert direct.legs[0].transfer_count == 0
    segment = direct.legs[0].segments[0]
    assert segment.flight_number == "ZZ101"
    assert segment.departure_airport.code == "PVG"
    assert segment.arrival_airport.code == "NRT"
    assert segment.scheduled_departure.has_utc_offset is True
    assert segment.scheduled_departure.timezone_name == "Asia/Shanghai"
    assert direct.offers[0].total.amount_minor == 218_000
    assert direct.offers[0].adult_base is not None
    assert direct.offers[0].adult_base.amount_minor == 168_000
    assert direct.offers[0].seats_remaining == 4

    transfer = result.records[1]
    assert transfer.is_direct is False
    assert transfer.legs[0].transfer_count == 1
    assert len(transfer.legs[0].segments) == 2
    assert transfer.legs[0].segments[0].scheduled_departure.has_utc_offset is False
    assert transfer.offers[0].total.amount_minor == 185_075


def test_parses_round_trip_itinerary_as_two_ordered_legs() -> None:
    result = CtripAdapter().parse_itineraries(load_fixture("batch_search_round_trip.json"))

    assert result.has_errors is False
    assert len(result.records) == 1
    itinerary = result.records[0]
    assert itinerary.provider_itinerary_id == "redacted-round-trip-itinerary"
    assert itinerary.is_direct is True
    assert len(itinerary.legs) == 2
    assert [leg.index for leg in itinerary.legs] == [0, 1]
    assert itinerary.legs[0].segments[0].departure_airport.code == "NKG"
    assert itinerary.legs[0].segments[-1].arrival_airport.code == "PKX"
    assert itinerary.legs[1].segments[0].departure_airport.code == "PKX"
    assert itinerary.legs[1].segments[-1].arrival_airport.code == "NKG"
    assert itinerary.offers[0].total.amount_minor == 170_000


def test_infers_known_airport_timezones_for_naive_provider_times() -> None:
    payload = load_fixture("batch_search.json")
    payload["data"]["flightItineraryList"][0]["flightSegments"][0]["flightList"][0].pop(
        "departureTimeZone", None
    )
    payload["data"]["flightItineraryList"][0]["flightSegments"][0]["flightList"][0].pop(
        "arrivalTimeZone", None
    )

    result = CtripAdapter().parse_itineraries(payload)

    direct = result.records[0]
    segment = direct.legs[0].segments[0]
    assert segment.scheduled_departure.timezone_name == "Asia/Shanghai"
    assert segment.scheduled_arrival.timezone_name == "Asia/Tokyo"


def test_itinerary_parser_skips_broken_segment_and_reports_exact_path() -> None:
    payload = load_fixture("batch_search.json")
    del payload["data"]["flightItineraryList"][1]["flightSegments"][0]["flightList"][0][
        "arriveDateTime"
    ]

    result = CtripAdapter().parse_itineraries(payload)

    assert len(result.records) == 2
    transfer = result.records[1]
    assert len(transfer.legs[0].segments) == 1
    issue = next(
        issue for issue in result.issues if issue.code == "segment_required_fields_missing"
    )
    assert issue.path.endswith(".legs[0].segments[0]")
    assert "arrival time" in issue.message


def test_unknown_fields_do_not_change_normalized_records() -> None:
    payload = load_fixture("batch_search.json")
    baseline = CtripAdapter().parse_itineraries(payload)
    payload["data"]["newProviderEnvelope"] = {"opaque": [1, 2, 3]}
    payload["data"]["flightItineraryList"][0]["experimentalField"] = True

    changed = CtripAdapter().parse_itineraries(payload)

    assert changed.records == baseline.records
    assert changed.schema_fingerprint != baseline.schema_fingerprint


def test_invalid_optional_offer_fields_are_diagnosed_without_losing_total() -> None:
    payload = load_fixture("batch_search.json")
    offer = payload["data"]["flightItineraryList"][0]["priceList"][0]
    offer["adultPrice"] = "not-a-price"
    offer["adultTax"] = -1
    offer["seatCount"] = "many"

    result = CtripAdapter().parse_itineraries(payload)

    assert len(result.records) == 2
    parsed_offer = result.records[0].offers[0]
    assert parsed_offer.total.amount_minor == 218_000
    assert parsed_offer.adult_base is None
    assert parsed_offer.adult_tax is None
    assert parsed_offer.seats_remaining is None
    assert {
        "invalid_adult_base_price",
        "invalid_adult_tax",
        "invalid_seat_count",
    }.issubset(issue_codes(result))


@pytest.mark.parametrize(
    "payload, expected_code",
    [
        ({"status": 0, "data": {}}, "itinerary_list_missing"),
        (
            {"data": {"flightItineraryList": [{"itineraryId": "x", "flightSegments": None}]}},
            "itinerary_legs_missing",
        ),
    ],
)
def test_itinerary_schema_drift_returns_diagnostics(
    payload: dict[str, Any], expected_code: str
) -> None:
    result = CtripAdapter().parse_itineraries(payload)

    assert result.records == ()
    assert expected_code in issue_codes(result)


def test_fixture_contains_no_unverified_operational_fields() -> None:
    raw = (FIXTURE_ROOT / "batch_search.json").read_text(encoding="utf-8").lower()

    for unsupported in ("baggage", "refund", "changefee", "ontime", "on_time"):
        assert unsupported not in raw
