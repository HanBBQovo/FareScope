from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.search import FareSearch, SearchFilters, SearchLeg, TripType


def make_round_trip(**overrides: object) -> FareSearch:
    values: dict[str, object] = {
        "trip_type": TripType.ROUND_TRIP,
        "legs": (
            SearchLeg(origin="sha", destination="tyo", departure_date=date(2026, 8, 15)),
            SearchLeg(origin="TYO", destination="SHA", departure_date=date(2026, 8, 22)),
        ),
    }
    values.update(overrides)
    return FareSearch.model_validate(values)


def test_query_hash_is_stable_after_normalization() -> None:
    first = make_round_trip(
        filters=SearchFilters(
            airline_codes=("mu", "NH", "mu"),
            departure_airports=("pvg", "sha"),
        )
    )
    second = make_round_trip(
        filters=SearchFilters(
            airline_codes=("NH", "MU"),
            departure_airports=("SHA", "PVG"),
        )
    )

    assert first.query_hash == second.query_hash
    assert first.legs[0].origin == "SHA"
    assert first.filters.airline_codes == ("MU", "NH")


def test_collection_relevant_filter_changes_query_hash() -> None:
    all_itineraries = make_round_trip()
    direct_only = make_round_trip(filters=SearchFilters(direct_only=True))

    assert all_itineraries.query_hash != direct_only.query_hash


def test_user_local_filters_do_not_duplicate_collection_query() -> None:
    unrestricted = make_round_trip()
    filtered = make_round_trip(
        filters=SearchFilters(
            airline_codes=("MU",),
            max_price_minor=250_000,
            max_stops=1,
            max_duration_minutes=600,
            departure_minute_start=360,
            departure_minute_end=720,
        )
    )

    assert unrestricted.query_hash == filtered.query_hash
    assert filtered.local_filter_payload() == {
        "airline_codes": ["MU"],
        "departure_airports": [],
        "arrival_airports": [],
        "max_price_minor": 250000,
        "max_stops": 1,
        "max_duration_minutes": 600,
        "departure_minute_start": 360,
        "departure_minute_end": 720,
    }


@pytest.mark.parametrize(
    "values",
    [
        {
            "trip_type": TripType.ONE_WAY,
            "legs": (
                SearchLeg(origin="SHA", destination="TYO", departure_date=date(2026, 8, 15)),
                SearchLeg(origin="TYO", destination="SHA", departure_date=date(2026, 8, 22)),
            ),
        },
        {
            "trip_type": TripType.ROUND_TRIP,
            "legs": (
                SearchLeg(origin="SHA", destination="TYO", departure_date=date(2026, 8, 15)),
                SearchLeg(origin="KIX", destination="SHA", departure_date=date(2026, 8, 22)),
            ),
        },
        {
            "trip_type": TripType.ROUND_TRIP,
            "legs": (
                SearchLeg(origin="SHA", destination="TYO", departure_date=date(2026, 8, 15)),
                SearchLeg(origin="TYO", destination="SHA", departure_date=date(2026, 8, 14)),
            ),
        },
    ],
)
def test_invalid_trip_shapes_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FareSearch.model_validate(values)


def test_invalid_passenger_mix_is_rejected() -> None:
    with pytest.raises(ValidationError, match="accompanied"):
        FareSearch.model_validate(
            {
                "trip_type": "one_way",
                "legs": [
                    {
                        "origin": "SHA",
                        "destination": "TYO",
                        "departure_date": "2026-08-15",
                    }
                ],
                "passengers": {"adults": 1, "infants": 2},
            }
        )
